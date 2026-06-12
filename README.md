# Prism

**A smart microphone for any app.** Prism sits between your physical mic and
apps like Discord, Zoom, OBS, and your browser, cleans up your audio in real
time, and hands them the polished result — no audio knowledge required.

```
your mic → [ clean-up pipeline ] → virtual cable → Discord / Zoom / OBS / browser
```

Open source, Windows-first, **runs on your CPU — no GPU needed.**

> **Status:** early MVP. Working today: mic capture → high-pass filter →
> **AI noise removal** (RNNoise or DeepFilterNet3, switchable live) → noise
> gate → virtual cable, with a control window: on/off toggle, model picker,
> strength slider, and a live noise meter. Voice isolation and the full
> desktop UI are on the roadmap below.

---

## Why Prism?

Most "clean up my mic" tools are closed-source, GPU-hungry, or do only one
thing. Prism aims to be the **one open-source tool** that combines noise
removal, voice isolation, and sound injection in a single pipeline — built for
non-technical users (gamers, party chat, remote workers, streamers).

Design principles that guide every decision:

- **Zero config** — works out of the box, complexity hidden behind one toggle.
- **CPU only** — no GPU, hardware agnostic.
- **Low latency** — target < 20 ms end-to-end, < 5% CPU at idle.
- **Windows first** — Linux and macOS later.

---

## How it works

Audio cleanup happens in a **pipeline** of small stages. Today the chain is:

| Stage | What it does |
|---|---|
| **High-pass filter** | Trims low rumble/hum below ~90 Hz without thinning your voice. |
| **AI denoiser** | Neural noise removal, pick one in the UI: **RNNoise** (light, ~10 ms) or **DeepFilterNet3** (stronger, ~32 ms). Both CPU only. A strength slider blends how much is applied, and a noise meter shows your room's noise floor plus how much is being removed. |
| **Noise gate** | Silences the stream between words. It runs *after* the denoiser, so its threshold can sit low and gate true silence without clipping soft speech. |

The processed audio is written to a **virtual audio cable**, which any app can
pick as its microphone. More stages (voice isolation, sound injection) plug in
later without touching the rest of the code.

### About the virtual cable

Prism routes audio through [VB-Audio Virtual Cable](https://vb-audio.com/Cable/),
which creates two Windows devices:

- **CABLE Input** — Prism *writes* your processed audio here.
- **CABLE Output** — other apps select this as their *microphone*.

So the flow is: **your mic → Prism → CABLE Input → (CABLE Output) → Discord/Zoom/etc.**

---

## Setup

### 1. Install VB-Audio Virtual Cable (one-time)

1. Download VB-CABLE from <https://vb-audio.com/Cable/>
2. Unzip, right-click `VBCABLE_Setup_x64.exe` → **Run as administrator**
3. Click **Install Driver**, then **reboot**
4. Confirm **CABLE Input** appears under Windows Sound → Playback

Prism prints these same instructions and exits if it can't find the cable.

### 2. Install Prism

Requires Python 3 on Windows. A virtual environment is recommended:

```powershell
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt
```

Dependencies: `numpy`, `scipy`, `sounddevice`, `pyrnnoise` (bundles the
RNNoise library), and `onnxruntime` (runs DeepFilterNet3 on CPU). If a
denoiser's pieces are missing, Prism falls back gracefully instead of crashing.

**Optional — DeepFilterNet3** (stronger denoising, more CPU): fetch its ~13 MB
model once, then pick it in the UI or set `DENOISER = "deepfilternet"` in
[prism/config.py](prism/config.py):

```powershell
./venv/Scripts/python.exe scripts/fetch_deepfilternet.py
```

---

## Running

```powershell
./venv/Scripts/python.exe app.py
```

Prism prints the mic and cable it selected, then `Pipeline running.` Press
**Ctrl+C** to stop.

**To use the cleaned audio:** in any app (or Windows Sound settings), pick
**CABLE Output** as the microphone. Speak — its level meter should respond to
your voice.

### Building the Windows .exe

```powershell
./venv/Scripts/python.exe -m pip install pyinstaller
./venv/Scripts/python.exe scripts/fetch_deepfilternet.py   # model gets bundled
./venv/Scripts/python.exe -m PyInstaller Prism.spec
```

This produces a folder build at `dist/Prism/` (~190 MB) with **both denoiser
models bundled** — no downloads at runtime. Zip the folder to distribute it.
VB-Cable still has to be installed separately (it's a driver).

### Offline test (no audio devices needed)

```powershell
./venv/Scripts/python.exe -m tests.test_pipeline
```

This runs the DSP checks without touching real hardware.

---

## Project layout

```
app.py              # entry point: find cable, build pipeline, run the stream
prism/
  config.py         # all tunable knobs (samplerate, cutoffs, denoiser choice)
  audio.py          # device discovery + the full-duplex stream runner
  pipeline.py       # Pipeline (chains stages) + build_denoiser()
  ui.py             # control window: toggle, model picker, strength, meters
  meters.py         # NoiseMeter — room-noise floor + reduction readout
  dsp/
    highpass.py     # HighPassFilter — Butterworth, stateful
    noise_gate.py   # NoiseGate — RMS gate with attack/release smoothing
    rnnoise_denoise.py  # RNNoiseDenoiser — neural noise removal (ctypes)
    deepfilternet.py    # DeepFilterNetDenoiser — DFN3 streaming via onnxruntime
scripts/
  fetch_deepfilternet.py  # one-time download of the DFN3 ONNX model
tests/
  test_pipeline.py  # offline DSP checks
docs/               # static product site (GitHub Pages serves this folder)
```

### Tweaking the sound

All tunables live in [prism/config.py](prism/config.py) — filter cutoff, gate
threshold, attack/release times, samplerate, and block size. Start there if your
voice sounds too thin or the gate cuts you off mid-word.

### Adding a processing stage

A **stage** is any object with a `process(block) -> block` method, where `block`
is a 1-D float32 mono array in `[-1.0, 1.0]`. Stages may keep state across
blocks. To add one, write the class and append it in `build_default_pipeline()`
in [prism/pipeline.py](prism/pipeline.py) — the audio callback stays untouched.

---

## Roadmap

1. **Core pipeline** *(done)* — mic capture, cable routing, high-pass filter,
   noise gate, device auto-detect. Tray icon moved to Phase 5.
2. **AI noise removal** *(done)* — RNNoise (~10 ms) and DeepFilterNet3
   (~32 ms), switchable live, strength slider, noise meter.
3. **Voice isolation** *(next)* — Silero VAD for speech detection + Demucs v4 to
   separate your voice from background voices, music, and TV.
4. **Sound injection** — soundboard, hotkeys, per-sound volume.
5. **UI & distribution** — Tauri desktop UI, tray icon, device picker, level
   visualizer; Windows `.exe` + Linux AppImage/`.deb`.

Today's chain is `mic → high-pass → AI denoiser → noise gate → cable`; the
Phase 3 voice-isolation stages slot in after the gate. Full plan with statuses:
[roadmap.md](roadmap.md).

---

## Troubleshooting

- **"Could not find the VB-Audio Virtual Cable…"** — install VB-CABLE (see
  Setup) and reboot.
- **"Invalid sample rate" from PortAudio** — set the cable's format to
  48000 Hz in Windows Sound → device properties. Don't lower `SAMPLERATE` in
  config to match the device: RNNoise quality degrades off 48 kHz.
- **Apps don't hear me** — make sure they're set to **CABLE Output** (not CABLE
  Input, and not your physical mic).

---

## License

MIT.
