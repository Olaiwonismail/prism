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

Two swappable denoisers behind one interface: **RNNoise** (light, ~10 ms
added latency) and **DeepFilterNet3** (stronger, ~32 ms), both CPU-only.
DeepFilterNet runs through ONNX Runtime — no GPU, no PyTorch, a 13 MB model.
Models switch live from the UI, a strength slider blends dry/wet, and a
noise meter shows the room's noise floor plus how much is being removed.

## Phase 3 — Voice isolation

Status: next

Separate *your* voice from background voices, music, and TV: Silero VAD
(~5 ms) for speech detection plus a streaming source-separation stage.

## Phase 4 — Sound injection

Status: planned

Soundboard: play clips into the mic stream, with hotkeys and per-sound
volume.

## Phase 5 — UI & distribution

Status: planned

Tauri desktop UI (tray icon, device picker, level visualizer), packaged
Windows `.exe`, Linux AppImage/.deb.
