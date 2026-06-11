"""Audio device discovery and the full-duplex stream runner."""

import numpy as np
import sounddevice as sd

from . import config


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


def _make_callback(pipeline, out_channels):
    def callback(indata, outdata, frames, time, status):
        if status:
            print("Audio status:", status)
        processed = pipeline.process_int16(indata)  # (frames,) int16
        if out_channels == 1:
            outdata[:, 0] = processed
        else:
            # Fan mono out to every output channel (e.g. a stereo cable).
            outdata[:] = np.repeat(processed[:, None], out_channels, axis=1)

    return callback


def run(pipeline, input_index, output_index):
    """Open a duplex stream: mic (input_index) -> pipeline -> output_index."""
    input_info = sd.query_devices(input_index)
    output_info = sd.query_devices(output_index)
    out_channels = min(int(output_info["max_output_channels"]), 2)

    print(f"Mic input : [{input_index}] {input_info['name']}")
    print(f"Output    : [{output_index}] {output_info['name']} ({out_channels} ch)")

    with sd.Stream(
        device=(input_index, output_index),
        samplerate=config.SAMPLERATE,
        blocksize=config.BLOCKSIZE,
        dtype=config.DTYPE,
        channels=(1, out_channels),
        callback=_make_callback(pipeline, out_channels),
    ):
        print("Pipeline running. Press Ctrl+C to stop.")
        try:
            while True:
                sd.sleep(1000)
        except KeyboardInterrupt:
            print("\nStopped.")
