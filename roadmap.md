# Prism roadmap

The plan, phase by phase. This file is the single source of truth — the
landing page renders it directly, and the project docs point here.

## Phase 1 — Core pipeline

Status: done

Mic capture, virtual-cable routing, high-pass filter, noise gate with hold
time, and device auto-detect that never picks the cable as its own input
(feedback-loop guard). A minimal control window with a mic picker and live
level meter. The proper system-tray icon moved to Phase 5 with the rest of
the UI work.

## Phase 2 — AI noise removal

Status: done

Three swappable denoisers behind one interface: **RNNoise** (light, ~10 ms
added latency), **GTCRN** (ultra-light neural — a tiny ~0.5 MB model, very low
CPU, ~40 ms), and **DeepFilterNet3** (stronger, ~32 ms), all CPU-only. The
neural ONNX models run through ONNX Runtime — no GPU, no PyTorch. Models switch
live from the UI, a strength slider blends dry/wet, and a noise meter shows the
room's noise floor plus how much is being removed.

## Phase 3 — Voice isolation

Status: in progress

Separate *your* voice from background voices, music, and TV: Silero VAD
(~5 ms) for speech detection plus a streaming source-separation stage.

**Speech detection — done.** Silero VAD ships as a swappable end-of-chain gate
(`config.GATE_MODE = "vad"`): it opens on detected speech rather than loudness,
so it keeps quiet speech an RMS gate would clip and drops loud non-speech
(keyboard, fan) it would pass. It observes a 16 kHz downsampled copy, so it adds
no latency to the audio path.

**Source separation — next.** The direction is **target-speaker extraction**
(keep one enrolled voice, drop other voices/TV/music), not blind separation.
There's no off-the-shelf CPU/ONNX personalized model to drop in yet, so this
piece is a research spike (WeSep causal backbone, or training a personalized
DeepFilterNet) before it becomes integration work.


## Phase 4 — UI & distribution

Status: in progress

**Desktop UI — largely done.** This was originally planned as a Tauri (Rust
shell + web frontend) app. Switched to a **PySide6/Qt** control window
(`prism/ui_qt.py`) instead: a Tauri build means packaging and shipping two
runtimes — a Rust/webview shell plus the Python audio backend it talks to
over IPC. PySide6 keeps the UI and the audio engine in one Python process, so
the whole app bundles into a single PyInstaller `.exe` with no IPC boundary
to design around or debug. The window already has the hero on/off toggle, a
live raw-vs-cleaned level scope, model and microphone pickers, and a
noise-removal strength slider.

**Remaining:** a system tray icon (minimize-to-tray instead of quitting on
close), and packaged builds — Windows `.exe` and Linux AppImage/.deb.

Website tasks parked until the pieces exist:

- Before/after audio demo on the landing page: record a clip with real
  background noise, process it through the pipeline offline, embed both.
- App screenshots, once the new UI is worth photographing.
