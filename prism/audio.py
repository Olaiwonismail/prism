"""Audio device discovery and the controllable audio engine."""

import numpy as np
import sounddevice as sd

from . import config
from .meters import NoiseMeter
from .pipeline import build_default_pipeline

_INT16_SCALE = 32768.0
_DB_FLOOR = -80.0  # quietest level reported to the UI


def find_device(name_substring, kind, hostapi=None):
    """Return the index of the first device whose name contains name_substring
    (case-insensitive) and supports the given kind ("input" or "output"),
    optionally restricted to one host API. Returns None if no match is found.
    """
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    needle = name_substring.lower()
    for index, device in enumerate(sd.query_devices()):
        if hostapi is not None and device["hostapi"] != hostapi:
            continue
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
        self.noise_floor_db = _DB_FLOOR  # room loudness between words (display)
        self.reduction_db = 0.0          # how much noise we're stripping (display)
        self._stream = None
        self._denoise_enabled = config.DENOISE_ENABLED
        self._denoise_mix = config.DENOISE_MIX
        self._denoiser_choice = config.DENOISER  # "rnnoise" | "gtcrn" | "deepfilternet" | "none"
        self._denoiser = None  # the active denoiser stage (RNNoise / GTCRN / DeepFilterNet)
        self._meter = None

    @property
    def denoiser_available(self):
        return self._denoiser is not None

    @property
    def denoiser_name(self):
        return getattr(self._denoiser, "name", "AI noise removal")

    @property
    def denoiser_enabled(self):
        return self._denoise_enabled

    @denoiser_enabled.setter
    def denoiser_enabled(self, value):
        """Flip AI denoising live; persists across mic switches/restarts."""
        self._denoise_enabled = bool(value)
        if self._denoiser is not None:
            self._denoiser.enabled = self._denoise_enabled

    @property
    def denoiser_mix(self):
        return self._denoise_mix

    @denoiser_mix.setter
    def denoiser_mix(self, value):
        """Set denoise strength live (0.0 = bypass, 1.0 = fully cleaned).

        A plain float write the callback reads each block — atomic under the
        GIL, no lock. Persists across mic switches/restarts.
        """
        self._denoise_mix = min(1.0, max(0.0, float(value)))
        if self._denoiser is not None:
            self._denoiser.mix = self._denoise_mix

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
        pipeline = build_default_pipeline(self._denoiser_choice)
        self._denoiser = next(
            (s for s in pipeline.stages if getattr(s, "IS_DENOISER", False)),
            None,
        )
        if self._denoiser is not None:
            self._denoiser.enabled = self._denoise_enabled
            self._denoiser.mix = self._denoise_mix

        block_ms = 1000.0 * config.BLOCKSIZE / config.SAMPLERATE
        self._meter = NoiseMeter(
            block_ms=block_ms,
            speech_threshold=config.NOISE_METER_SPEECH_THRESHOLD,
            floor_tau_ms=config.NOISE_METER_FLOOR_TAU_MS,
            reduction_tau_ms=config.NOISE_METER_REDUCTION_TAU_MS,
            db_floor=_DB_FLOOR,
        )

        # PortAudio cannot open a duplex stream across host APIs (-9993), so
        # use the cable device from the same host API as the chosen mic.
        output_index = self.output_index
        in_api = sd.query_devices(input_index)["hostapi"]
        if sd.query_devices(output_index)["hostapi"] != in_api:
            match = find_device(config.CABLE_NAME, "output", hostapi=in_api)
            if match is not None:
                output_index = match
        out_channels = min(
            int(sd.query_devices(output_index)["max_output_channels"]), 2
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
            # Noise meter: the room floor needs a VAD, which only RNNoise
            # exposes (DeepFilterNet has none). Feed it only when that denoiser
            # is actually running; otherwise pass None and the floor holds.
            if self.enabled and self._denoise_enabled:
                speech_prob = getattr(self._denoiser, "speech_prob", None)
            else:
                speech_prob = None
            self._meter.update(self.in_db, self.out_db, speech_prob)
            self.noise_floor_db = self._meter.noise_floor_db
            self.reduction_db = self._meter.reduction_db

        stream = sd.Stream(
            device=(input_index, output_index),
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

    @property
    def denoiser_choice(self):
        return self._denoiser_choice

    def set_denoiser(self, choice):
        """Switch the AI denoiser live (e.g. 'rnnoise' <-> 'deepfilternet').

        Rebuilds the pipeline on the current mic, the same way switching mics
        does — a brief, deliberate restart, not a hot path. If the choice can't
        load (e.g. DeepFilterNet without onnxruntime) the pipeline falls back to
        RNNoise; read ``denoiser_name`` afterwards to see what actually loaded.
        """
        self._denoiser_choice = choice
        if self.running:
            self.start(self.input_index)
