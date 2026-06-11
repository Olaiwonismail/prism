"""Audio device discovery and the controllable audio engine."""

import numpy as np
import sounddevice as sd

from . import config
from .pipeline import build_default_pipeline
from .dsp import rnnoise_denoise

_INT16_SCALE = 32768.0
_DB_FLOOR = -80.0  # quietest level reported to the UI


def find_device(name_substring, kind):
    """Return the index of the first device whose name contains name_substring
    (case-insensitive) and supports the given kind ("input" or "output").
    Returns None if no match is found.
    """
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    needle = name_substring.lower()
    for index, device in enumerate(sd.query_devices()):
        if needle in device["name"].lower() and device[channel_key] > 0:
            return index
    return None


def _is_real_mic(name):
    needle = name.lower()
    return not any(excluded.lower() in needle for excluded in config.INPUT_EXCLUDE)


def pick_input_device():
    """Return the index of a real microphone, never a VB-Cable or mapper device.

    Prefers the system default input; falls back to the first real input
    device. Returns None if no usable microphone exists.
    """
    default_index = sd.default.device[0]
    if default_index is not None and default_index >= 0:
        name = sd.query_devices(default_index)["name"]
        if _is_real_mic(name):
            return default_index
        print(f'Default mic "{name}" is a virtual device; picking a real mic instead.')
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and _is_real_mic(device["name"]):
            return index
    return None


def list_input_devices():
    """Return [(index, name)] of real microphones from a single host API.

    PortAudio lists every physical device once per host API (MME, DirectSound,
    WASAPI, WDM-KS). Restrict to WASAPI when available (full, untruncated
    names) or the default host API, so each mic appears once.
    """
    hostapis = sd.query_hostapis()
    target = next((i for i, api in enumerate(hostapis)
                   if api["name"] == "Windows WASAPI"), sd.default.hostapi)
    return [(index, device["name"])
            for index, device in enumerate(sd.query_devices())
            if (device["hostapi"] == target
                and device["max_input_channels"] > 0
                and _is_real_mic(device["name"]))]


def _dbfs(x):
    rms = float(np.sqrt(np.mean(x * x)))
    if rms <= 0.0:
        return _DB_FLOOR
    return max(_DB_FLOOR, 20.0 * np.log10(rms))


class AudioEngine:
    """Owns the mic -> pipeline -> cable stream; controllable from a UI.

    ``enabled`` toggles processing vs. raw passthrough (a plain bool the audio
    callback reads each block — atomic under the GIL, no locks). ``in_db``,
    ``out_db`` and ``gate_open`` are written by the callback for the UI to
    poll; they are display-only approximations.
    """

    def __init__(self, output_index):
        self.output_index = output_index
        self.input_index = None
        self.enabled = True
        self.in_db = _DB_FLOOR
        self.out_db = _DB_FLOOR
        self.gate_open = False
        self._stream = None

    @property
    def running(self):
        return self._stream is not None

    def start(self, input_index):
        """Open the duplex stream from input_index to the cable (non-blocking).

        Builds a fresh pipeline so filter/gate state never carries across
        devices. Raises on failure (e.g. device unplugged) with the stream
        left closed.
        """
        self.stop()
        pipeline = build_default_pipeline()
        out_channels = min(
            int(sd.query_devices(self.output_index)["max_output_channels"]), 2
        )

        def callback(indata, outdata, frames, time, status):
            if status:
                print("Audio status:", status)
            self.in_db = _dbfs(indata[:, 0].astype(np.float32) / _INT16_SCALE)
            if self.enabled:
                processed = pipeline.process_int16(indata)
            else:
                processed = indata[:, 0]  # raw passthrough
            # Broadcast mono to every output channel (e.g. a stereo cable).
            outdata[:] = processed[:, None]
            self.out_db = _dbfs(processed.astype(np.float32) / _INT16_SCALE)
            self.gate_open = self.in_db >= config.NOISE_GATE_THRESHOLD_DB

        stream = sd.Stream(
            device=(input_index, self.output_index),
            samplerate=config.SAMPLERATE,
            blocksize=config.BLOCKSIZE,
            dtype=config.DTYPE,
            channels=(1, out_channels),
            callback=callback,
        )
        stream.start()
        self._stream = stream
        self.input_index = input_index

    def stop(self):
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def switch_input(self, input_index):
        self.start(input_index)
