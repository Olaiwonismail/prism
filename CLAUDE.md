# CLAUDE.md

Guidance for working in the **Prism** repository.

## What Prism is

Prism is an open-source, cross-platform **virtual audio middleware** that sits
between a physical microphone and any application. It captures mic input,
processes it in real time (AI noise removal, voice isolation), and routes the
result to a **virtual audio cable** so apps like Discord, Zoom, OBS, and
browsers can use the processed stream as their microphone.

**Positioning:** the only open-source, Windows-first tool combining noise
removal + voice isolation in one pipeline, aimed at non-technical users
(gamers, party chat, remote workers, streamers).

**Core principles that should guide design decisions:**
- Zero config / non-technical friendly — works out of the box, no audio
  knowledge required, complexity hidden behind one on/off toggle.
- Hardware agnostic — **CPU only, no GPU required**.
- Low latency — target **< 20ms end-to-end**, **< 5% CPU at idle**.
- Windows first; Linux and macOS later.

## Current state

Early/MVP. **Phases 1–2 done; Phase 3 (voice isolation) in progress** — the
Silero VAD speech gate shipped, target-speaker extraction is next. Chain today:
mic capture →
high-pass → AI denoiser → noise gate → virtual cable routing. The denoiser is
**swappable** (`config.DENOISER`): RNNoise (light, the Windows default), GTCRN
(ultra-light neural, tiny model; the default off-Windows), or DeepFilterNet3
(stronger, heavier). The UI adds a live
**strength** slider (dry/wet) and a **noise meter** (room-noise floor + how much
is being stripped).

```
physical mic → [high-pass → AI denoiser → noise gate] → CABLE Input (VB-Audio)
```

Note the gate runs **after** RNNoise (not the PRD's gate-first order): on the
cleaned signal its threshold can sit low (-45 dBFS) and gate true silence
without clipping soft speech onsets. A gate on the raw mic had to sit above
the ~-35 dBFS noise floor (-25) and chopped quiet consonants. The gate also
has a hold time so word ends/brief gaps aren't cut.

The end-of-chain gate is swappable via `config.GATE_MODE`: **"rms"** (the
default `NoiseGate`, opens on loudness) or **"vad"** (`SileroVAD`, opens on
detected speech). The VAD gate keeps quiet speech the RMS gate would clip and
drops loud non-speech (keyboard, fan) it would pass. Missing onnxruntime/model
→ `build_gate()` falls back to the RMS gate.

Silero VAD notes ([prism/dsp/silero_vad.py](prism/dsp/silero_vad.py)): a tiny
~2.3 MB MIT ONNX detector (Phase 3's speech-detection piece). It runs at 16 kHz
on fixed 512-sample windows, so the stage **observes a downsampled copy** to
update a running speech probability while the audio passes through untouched —
the model's ~32 ms window adds **no latency to the signal** (the detection lag
is absorbed by the gate's hold). It reuses GTCRN's `_Decimator` for 48→16 kHz
and the noise-gate attack/release/hold envelope, gated on `speech_prob` instead
of RMS. Input names differ by model version (v4: input/sr/h/c; v5:
input/state/sr), so the stage **introspects the graph at load** rather than
hardcoding. Model isn't committed; fetch with `scripts/fetch_silero_vad.py`.

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

GTCRN notes ([prism/dsp/gtcrn.py](prism/dsp/gtcrn.py)): a tiny ~48 K-param,
~0.5 MB ONNX model — the lightest-CPU neural option. Unlike the other two it
runs at **16 kHz with the STFT outside the model**, so the stage owns the bits
they don't: stateful 48↔16 kHz resampling (clean 3:1, anti-alias FIRs) and a
streaming STFT/overlap-add (512 fft, 256 hop, sqrt-Hann; COLA so plain OLA
reconstructs) wrapped around the model's three per-frame cache tensors. The
model is frame-synchronous (no latency of its own); the FIR group delay (~3 ms)
and the STFT fill (~30 ms) are measured once at import to size the wet FIFO
pre-fill and the dry delay line — so it keeps the block contract and stays
phase-aligned for partial `mix`. Total latency ~40 ms but very low CPU. Model
isn't committed; fetch with `scripts/fetch_gtcrn.py`. Missing onnxruntime/model
→ `build_denoiser()` falls back to RNNoise.

All three denoiser stages set `IS_DENOISER = True` and expose `.enabled`,
`.mix`, and `.name`, so the engine finds and drives whichever one is active
without caring about its type.

### Layout

```
app.py              # thin entry point: bootstrap device, build pipeline, run
prism/
  config.py         # all tunable constants (samplerate, cutoffs, denoiser choice)
  audio.py          # find_device() + AudioEngine stream runner (duplex, or
                    #   input-only + ring on macOS)
  bootstrap.py      # ensure_virtual_device() — self-install/spawn the virtual
                    #   device per platform (VB-Cable / pw-loopback / HAL driver)
  ring_output.py    # SharedRingWriter — macOS shared-memory feed to the driver
  pipeline.py       # Pipeline (chains stages) + build_denoiser() (RNNoise/DFN)
  ui_qt.py          # PySide6 "hero toggle" window (power button, meters, settings)
  ui.py             # legacy Tkinter window (kept as a fallback; app.py uses ui_qt)
  meters.py         # NoiseMeter — display-only room-noise + reduction readings
  dsp/
    highpass.py     # HighPassFilter — Butterworth, stateful (scipy sosfilt)
    noise_gate.py   # NoiseGate — RMS gate with attack/release smoothing
    silero_vad.py   # SileroVAD — speech-driven gate (onnxruntime, observes 16 kHz copy)
    rnnoise_denoise.py  # RNNoiseDenoiser — neural denoiser (ctypes -> rnnoise.dll)
    deepfilternet.py    # DeepFilterNetDenoiser — DFN3 streaming via onnxruntime
    gtcrn.py            # GTCRNDenoiser — ultra-light 16 kHz NN (onnxruntime + resample)
mac/                # macOS virtual-microphone driver (see mac/README.md)
  PrismAudioDriver/ # our MIT fork of Krasp's HAL plugin ("Prism Microphone")
  vendor/           # pristine upstream KraspHAL source + license (provenance)
  build_driver.sh   # clang build + ad-hoc codesign -> mac/dist/PrismAudio.driver
scripts/
  fetch_deepfilternet.py  # download the DFN3 ONNX model into models/ (one-time)
  fetch_gtcrn.py          # download the GTCRN ONNX model into models/ (one-time)
  fetch_silero_vad.py     # download the Silero VAD ONNX model into models/ (one-time)
  fetch_vbcable.py        # download the VB-Cable installer into installers/ (one-time)
tests/
  test_pipeline.py  # offline DSP/meter/ring checks (no audio devices needed)
roadmap.md          # phase statuses — single source of truth for the roadmap
.github/workflows/mac.yml  # CI: build + ad-hoc sign the mac driver artifact
docs/               # static site (GitHub Pages serves this folder; no build step)
  index.html        # product page: hero, use cases, how it works, comparison
  roadmap/index.html   # renders roadmap.md as a phase list + detail panel
  releases/index.html  # GitHub releases paired with devlog build notes
  style.css / app.js   # shared by all pages; app.js inits per-page by element
  devlog/v*.md      # per-release build stories, paired to releases by version
  devlog.json       # manifest listing devlog entries the page should load
```

### Key extension point

`Pipeline` ([prism/pipeline.py](prism/pipeline.py)) is an ordered list of
**stages**. Each stage is an object with `process(block) -> block` operating on a
1-D float32 mono array in [-1.0, 1.0], and may hold state across blocks. New
processing (e.g. Phase 3's target-speaker extraction stage) plugs in as new
stage classes appended in `build_default_pipeline()`. The audio callback in
[prism/audio.py](prism/audio.py) stays trivial — all processing lives in stages.

Tunables (cutoff, gate threshold, attack/release, samplerate, blocksize) live in
[prism/config.py](prism/config.py). At startup `app.py` calls
`bootstrap.ensure_virtual_device()` ([prism/bootstrap.py](prism/bootstrap.py)),
which self-installs/spawns the virtual device if it's missing (below); only if
that fails does it print manual instructions and exit.

## How audio routing works (important context)

Per platform (all handled by [prism/bootstrap.py](prism/bootstrap.py)):

**Windows** — VB-Audio Virtual Cable creates two devices:
- **CABLE Input** — a *playback/output* device. Prism **writes** processed audio
  here.
- **CABLE Output** — a *recording/input* device. Other apps select this as their
  **microphone**.

If the cable is missing, the bootstrap offers to run the **bundled VB-Cable
installer** (fetch it once with `scripts/fetch_vbcable.py`; bundled unmodified
— mere aggregation, VB-Cable stays VB-Audio's donationware). One UAC prompt,
then VB-Cable still needs **one reboot**. Without the bundled installer it
falls back to the old manual instructions.

**Linux** — no install at all: the bootstrap spawns a PipeWire loopback pair
(`pw-loopback`) at startup — Prism writes to the **"Prism Virtual Cable"**
sink, apps record from the **"Prism Microphone"** source — and tears it down
on exit.

**macOS** — no output device, different architecture: the bundled
**PrismAudio HAL driver** (`mac/`, an MIT fork of Krasp's KraspHAL) publishes
a **"Prism Microphone"** input device that reads from a shared-memory ring
file (`/tmp/com.prism.audio`). Prism opens an *input-only* stream and writes
processed blocks into the ring ([prism/ring_output.py](prism/ring_output.py))
— no loopback, no feedback risk, no reboot. Install is one admin prompt (copy
into `/Library/Audio/Plug-Ins/HAL/` + restart coreaudiod). The driver is
**ad-hoc signed** (free, loads fine); only the *app's* first-launch Gatekeeper
warning would need paid notarization. The ring protocol lives in
`mac/PrismAudioDriver/PrismSharedRing.h` and must stay byte-identical to
`prism/ring_output.py`. **Status: compiles in CI, unverified on real Mac
hardware** — the whole macOS path (driver, ring, Core Audio stream) has never
run on a Mac; do not cut a Mac release on CI-green alone. RNNoise is also
Windows-first: off-Windows the default and fallback denoiser is GTCRN
(`config.DENOISER`).

## Roadmap (phases)

**Single source of truth: [roadmap.md](roadmap.md)** (the landing page in
`docs/` renders it directly — update statuses there, not here). Summary:
1. **Core pipeline** (done) — mic capture, virtual cable routing, high-pass
   filter, noise gate, device auto-detect (tray icon moved to Phase 4).
2. **AI noise removal** (done) — RNNoise (~10ms) + GTCRN (~40ms, ultra-light) +
   DeepFilterNet3 (~32ms), swappable via `config.DENOISER`; adjustable level
   (strength slider) and noise meter both shipped.
3. **Voice isolation** (in progress) — speech detection is done (Silero VAD
   speech gate, see above). Next: **target-speaker extraction** — keep one
   enrolled voice, drop other voices/music/TV — *not* blind separation (the
   earlier Demucs plan is dropped). No off-the-shelf CPU/ONNX personalized
   model exists yet, so this is a research spike (WeSep causal backbone, or
   training a personalized DeepFilterNet) before it becomes integration work.
4. **UI & distribution** (in progress) — PySide6/Qt desktop UI
   (`prism/ui_qt.py`, already the running control window: hero toggle, live
   scope, strength slider, model + mic pickers) is largely done. Remaining:
   system tray icon (minimize-to-tray) and packaged builds — Windows `.exe` +
   Linux AppImage/.deb. Originally planned as a Tauri (Rust shell + web
   frontend) app — switched to PySide6 so the whole app ships as one
   Python+Qt package instead of a Rust shell driving a separate Python audio
   backend over IPC (see Tech stack below).

Intended signal-chain order once built:
`mic → high-pass → AI denoiser → target-speaker extraction → gate (RMS or VAD) → cable`

## Tech stack

| Layer | Technology |
|---|---|
| Audio I/O | `sounddevice` (PortAudio) |
| Signal processing | `numpy`, `scipy` |
| AI models | RNNoise, GTCRN, DeepFilterNet, Silero VAD; target-speaker extraction model TBD (Phase 3) |
| Virtual device | VB-Audio Virtual Cable |
| Desktop UI | PySide6 / Qt for Python (`prism/ui_qt.py`) |
| Languages | Python |

**Why PySide6 instead of Tauri:** the roadmap originally called for a Tauri
(Rust shell + web frontend) desktop UI. That would mean packaging and
distributing two runtimes — a Rust/webview shell plus the Python audio
backend it drives over IPC. PySide6 keeps the whole app one Python process,
so it packages as a single PyInstaller `.exe` (see `Prism.spec`) with no
cross-language IPC boundary between UI and audio.

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
