# CLAUDE.md

Guidance for working in the **Prism** repository.

## What Prism is

Prism is an open-source, cross-platform **virtual audio middleware** that sits
between a physical microphone and any application. It captures mic input,
processes it in real time (AI noise removal, voice isolation, sound injection),
and routes the result to a **virtual audio cable** so apps like Discord, Zoom,
OBS, and browsers can use the processed stream as their microphone.

**Positioning:** the only open-source, Windows-first tool combining noise
removal + voice isolation + sound injection in one pipeline, aimed at
non-technical users (gamers, party chat, remote workers, streamers).

**Core principles that should guide design decisions:**
- Zero config / non-technical friendly — works out of the box, no audio
  knowledge required, complexity hidden behind one on/off toggle.
- Hardware agnostic — **CPU only, no GPU required**.
- Low latency — target **< 20ms end-to-end**, **< 5% CPU at idle**.
- Windows first; Linux and macOS later.

## Current state

Early/MVP. **Phase 1 is functional**: mic capture → high-pass → noise gate →
virtual cable routing, verified end-to-end.

```
physical mic → [high-pass → noise gate] → CABLE Input (VB-Audio)
```

### Layout

```
app.py              # thin entry point: find cable, build pipeline, run stream
prism/
  config.py         # all tunable constants (samplerate, cutoffs, thresholds)
  audio.py          # find_device() + run() duplex stream runner
  pipeline.py       # Pipeline (chains stages; owns int16<->float conversion)
  dsp/
    highpass.py     # HighPassFilter — Butterworth, stateful (scipy sosfilt)
    noise_gate.py   # NoiseGate — RMS gate with attack/release smoothing
tests/
  test_pipeline.py  # offline DSP checks (no audio devices needed)
```

### Key extension point

`Pipeline` ([prism/pipeline.py](prism/pipeline.py)) is an ordered list of
**stages**. Each stage is an object with `process(block) -> block` operating on a
1-D float32 mono array in [-1.0, 1.0], and may hold state across blocks. Future
phases (RNNoise, Silero VAD, DeepFilterNet, Demucs) plug in as new stage classes
appended in `build_default_pipeline()`. The audio callback in
[prism/audio.py](prism/audio.py) stays trivial — all processing lives in stages.

Tunables (cutoff, gate threshold, attack/release, samplerate, blocksize) live in
[prism/config.py](prism/config.py). The startup guard in `app.py` prints
VB-Cable install instructions and exits if the cable isn't found.

## How audio routing works (important context)

VB-Audio Virtual Cable creates two Windows devices:
- **CABLE Input** — a *playback/output* device. Prism **writes** processed audio
  here.
- **CABLE Output** — a *recording/input* device. Other apps select this as their
  **microphone**.

VB-Cable must be installed separately (https://vb-audio.com/Cable/, run as admin,
reboot). Whether to bundle vs. user-install is an open question (see PRD §13).

## Roadmap (phases)

The full PRD lives in the conversation history / project docs. Phases:
1. **Core pipeline** (current) — mic capture, virtual cable routing, high-pass
   filter, noise gate, device auto-detect, system tray.
2. **AI noise removal** — RNNoise (starter, ~10ms) → DeepFilterNet (~20ms),
   adjustable level, noise meter.
3. **Voice isolation** — Silero VAD (~5ms) for speech detection + Demucs v4
   (streaming) to separate user's voice from background voices/music/TV.
4. **Sound injection** — soundboard, hotkeys, per-sound volume.
5. **UI & distribution** — Tauri desktop UI (Rust shell + web frontend), tray
   icon, device picker, level visualizer; Windows `.exe` + Linux AppImage/.deb.

Intended signal-chain order once built:
`mic → high-pass → noise gate → Silero VAD → DeepFilterNet → Demucs v4 → cable`

## Tech stack

| Layer | Technology |
|---|---|
| Audio I/O | `sounddevice` (PortAudio) |
| Signal processing | `numpy`, `scipy` |
| AI models | RNNoise, DeepFilterNet, Silero VAD, Demucs v4 |
| Virtual device | VB-Audio Virtual Cable |
| Desktop UI (later) | Tauri (Rust + HTML/CSS/JS) |
| Languages | Python (backend), Rust (UI shell) |

## Environment & running

- Windows 11, PowerShell. A `venv` exists at `venv/`; deps in `requirements.txt`
  (`numpy`, `scipy`, `sounddevice`).
- Run: `./venv/Scripts/python.exe app.py`
- Offline DSP test (no devices needed):
  `./venv/Scripts/python.exe -m tests.test_pipeline`
- The app prints the selected mic + cable, then `Pipeline running.`
  Ctrl+C stops cleanly.
- **Verify routing**: in Windows Sound settings or any app (Discord/Audacity),
  select **CABLE Output** as the mic and confirm its level meter responds to
  your voice.

## Conventions & constraints

- Keep latency and CPU budgets front of mind — prefer streaming/chunked
  processing over anything that buffers large windows. No GPU dependencies.
- All real-time processing belongs in a pipeline **stage**; keep the audio
  callback fast and non-blocking (no heavy allocation or I/O inside it).
- Samplerate caveat: VB-Cable is often 44100 Hz in Windows. WASAPI shared mode
  resamples, so 16000 usually works; if PortAudio raises "Invalid sample rate",
  adjust `SAMPLERATE` in [prism/config.py](prism/config.py) or the cable's
  Windows device format.
- Licensed under **MIT**.
