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

Early/MVP. **Phase 1 functional, Phase 2 largely complete**: mic capture →
high-pass → AI denoiser → noise gate → virtual cable routing. The denoiser is
**swappable** (`config.DENOISER`): RNNoise (light default) or DeepFilterNet3
(stronger, heavier). The UI adds a live **strength** slider (dry/wet) and a
**noise meter** (room-noise floor + how much is being stripped).

```
physical mic → [high-pass → AI denoiser → noise gate] → CABLE Input (VB-Audio)
```

Note the gate runs **after** RNNoise (not the PRD's gate-first order): on the
cleaned signal its threshold can sit low (-45 dBFS) and gate true silence
without clipping soft speech onsets. A gate on the raw mic had to sit above
the ~-35 dBFS noise floor (-25) and chopped quiet consonants. The gate also
has a hold time so word ends/brief gaps aren't cut.

RNNoise notes ([prism/dsp/rnnoise_denoise.py](prism/dsp/rnnoise_denoise.py)):
binds via ctypes to the shared library bundled in the `pyrnnoise` wheel; the
package's own Python wrapper is **never imported** (broken `audiolab`/`av`
dependency chain, and unneeded for streaming). The stream runs at 48 kHz with
480-sample blocks so one block == one RNNoise frame (no rechunk latency). If
pyrnnoise is missing the pipeline degrades gracefully to Phase 1. Measured on
this machine: ~1.3 ms per 10 ms block during speech, ~0.55 ms on silence —
the gate sitting before RNNoise keeps idle CPU low.

DeepFilterNet notes ([prism/dsp/deepfilternet.py](prism/dsp/deepfilternet.py)):
the torch-free streaming DFN3 export runs via `onnxruntime` (CPU, ~13 MB wheel,
no torch). The whole DSP chain (STFT, ERB/spec features, GRU net, deep
filtering, ISTFT) lives **inside the ONNX graph** — the stage just feeds one
512-sample frame and carries the model's 12 recurrent state tensors across
calls. A FIFO rechunks the 480-sample blocks to 512 and back (one frame of
buffering); the model adds ~32 ms of its own latency (measured impulse delay =
3 frames), so a partial `mix` delays the dry path by 3 frames to stay phase-
aligned. Measured ~5.7 ms per 10.7 ms frame: real-time but ~5x RNNoise's CPU —
hence the "stronger but heavier" positioning. The 13 MB model isn't committed;
fetch it with `scripts/fetch_deepfilternet.py`. If onnxruntime or the model is
missing, `build_denoiser()` prints why and falls back to RNNoise.

Both denoiser stages set `IS_DENOISER = True` and expose `.enabled`, `.mix`,
and `.name`, so the engine finds and drives whichever one is active without
caring about its type.

### Layout

```
app.py              # thin entry point: find cable, build pipeline, run stream
prism/
  config.py         # all tunable constants (samplerate, cutoffs, denoiser choice)
  audio.py          # find_device() + AudioEngine duplex stream runner
  pipeline.py       # Pipeline (chains stages) + build_denoiser() (RNNoise/DFN)
  ui.py             # Tkinter window (toggle, model picker, strength, mic, meters)
  meters.py         # NoiseMeter — display-only room-noise + reduction readings
  dsp/
    highpass.py     # HighPassFilter — Butterworth, stateful (scipy sosfilt)
    noise_gate.py   # NoiseGate — RMS gate with attack/release smoothing
    rnnoise_denoise.py  # RNNoiseDenoiser — neural denoiser (ctypes -> rnnoise.dll)
    deepfilternet.py    # DeepFilterNetDenoiser — DFN3 streaming via onnxruntime
scripts/
  fetch_deepfilternet.py  # download the DFN3 ONNX model into models/ (one-time)
tests/
  test_pipeline.py  # offline DSP/meter checks (no audio devices needed)
roadmap.md          # phase statuses — single source of truth for the roadmap
docs/               # landing page (GitHub Pages serves this folder; no build step)
  index.html / style.css / app.js   # static site; pulls releases via GitHub API
  devlog/v*.md      # per-release build stories, paired to releases by version
  devlog.json       # manifest listing devlog entries the page should load
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

**Single source of truth: [roadmap.md](roadmap.md)** (the landing page in
`docs/` renders it directly — update statuses there, not here). Summary:
1. **Core pipeline** (current) — mic capture, virtual cable routing, high-pass
   filter, noise gate, device auto-detect, system tray.
2. **AI noise removal** (done) — RNNoise (~10ms) + DeepFilterNet3 (~32ms),
   swappable via `config.DENOISER`; adjustable level (strength slider) and
   noise meter both shipped.
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
- Samplerate caveat: the stream runs at 48000 Hz (RNNoise requirement; do not
  change it casually — RNNoise quality degrades off 48 kHz). WASAPI shared mode
  resamples if the device format differs; if PortAudio raises "Invalid sample
  rate", change the cable's Windows device format rather than `SAMPLERATE`.
- Licensed under **MIT**.
