# PrismAudio HAL driver (macOS)

The macOS virtual microphone: a CoreAudio server plug-in that publishes a
**"Prism Microphone"** input device, fed by a shared-memory ring file that
Prism writes processed audio into (`prism/ring_output.py`). No output device,
no loopback, no reboot -- one admin prompt at install.

## Provenance

Forked from [Krasp](https://github.com/pilshchikov/krasp)'s `KraspHAL`
(MIT License, Copyright (c) 2026 Stepan Pilshchikov), pinned at upstream
commit `bed68e1ff4e88cf7221034977870a2d3a37de5c1`.

- `vendor/` -- pristine upstream copies (driver source, ring header, license).
- `PrismAudioDriver/` -- our fork: renamed identifiers and device strings,
  our bundle id (`com.prism.audio.hal`), our ring file path
  (`/tmp/com.prism.audio`) and magic (`0x5052534D`, "PRSM"), fresh CFPlugIn
  factory UUID so both drivers could coexist on one machine.

If you touch the ring protocol, change **both** `PrismSharedRing.h` and
`prism/ring_output.py` -- the struct layouts must match byte-for-byte.

## Building

On any Mac (or a GitHub Actions macOS runner -- see
`.github/workflows/mac.yml`, which uploads the built driver as an artifact):

```sh
bash mac/build_driver.sh 1.2.3
```

Needs only the Xcode Command Line Tools. The output
`mac/dist/PrismAudio.driver` is **ad-hoc signed** (`codesign --sign -`):
that's enough for `coreaudiod` to load it, free, with no Apple Developer
account. (Notarizing the *app* to remove its first-launch Gatekeeper warning
is a separate, paid concern -- the driver itself shows no warning because
users never launch it directly.)

## Installing

Prism does this itself (`prism/bootstrap.py`) with one admin prompt. By hand:

```sh
sudo cp -R mac/dist/PrismAudio.driver /Library/Audio/Plug-Ins/HAL/
sudo killall coreaudiod
```

Then "Prism Microphone" appears in System Settings -> Sound -> Input, and in
Discord/Zoom/OBS mic pickers.

## Status

**Built but unverified on hardware.** CI proves it compiles and signs; nobody
has yet installed it on a real Mac and routed audio through it. Do not cut a
macOS release on CI-green alone.
