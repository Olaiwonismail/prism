# Prism

**A smart microphone for any app.** Prism sits between your physical mic and
apps like Discord, Zoom, OBS, and your browser, cleans up your audio in real
time, and hands them the polished result — no audio knowledge required.

```
your mic → [ clean-up pipeline ] → virtual cable → Discord / Zoom / OBS / browser
```

Open source, Windows-first, **runs on your CPU — no GPU needed.**

> **Status:** early MVP. Working today: mic capture → high-pass filter →
> noise gate → **RNNoise AI noise removal** → virtual cable, with a minimal
> control window. Voice isolation and the full desktop UI are on the roadmap
> below.

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
| **Noise gate** | Silences the mic between words, so background hiss doesn't bleed through. |
| **RNNoise** | Neural-network noise removal: strips fan hum, keyboard clatter, and hiss from your speech in real time (10 ms frames, CPU only). |

The processed audio is written to a **virtual audio cable**, which any app can
pick as its microphone. More stages (AI noise removal, voice isolation) plug in
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
RNNoise library; if it's missing, Prism still runs without AI noise removal).

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
  config.py         # all tunable knobs (samplerate, cutoffs, thresholds)
  audio.py          # device discovery + the full-duplex stream runner
  pipeline.py       # Pipeline: chains stages, owns int16 <-> float conversion
  dsp/
    highpass.py     # HighPassFilter — Butterworth, stateful
    noise_gate.py   # NoiseGate — RMS gate with attack/release smoothing
    rnnoise_denoise.py  # RNNoiseDenoiser — neural noise removal (ctypes)
tests/
  test_pipeline.py  # offline DSP checks
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

1. **Core pipeline** *(done, tray icon pending)* — mic capture, cable routing,
   high-pass filter, noise gate, device auto-detect, system tray.
2. **AI noise removal** *(current)* — ✅ RNNoise (~10 ms) → DeepFilterNet
   (~20 ms), adjustable level, noise meter.
3. **Voice isolation** — Silero VAD for speech detection + Demucs v4 to separate
   your voice from background voices, music, and TV.
4. **Sound injection** — soundboard, hotkeys, per-sound volume.
5. **UI & distribution** — Tauri desktop UI, device picker, level visualizer;
   Windows `.exe` + Linux AppImage/`.deb`.

Intended final signal chain:
`mic → high-pass → noise gate → Silero VAD → DeepFilterNet → Demucs v4 → cable`

---

## Troubleshooting

- **"Could not find the VB-Audio Virtual Cable…"** — install VB-CABLE (see
  Setup) and reboot.
- **"Invalid sample rate" from PortAudio** — VB-Cable often runs at 44100 Hz in
  Windows. Adjust `SAMPLERATE` in [prism/config.py](prism/config.py) or change
  the cable's format in Windows device settings.
- **Apps don't hear me** — make sure they're set to **CABLE Output** (not CABLE
  Input, and not your physical mic).

---

## License

MIT.
