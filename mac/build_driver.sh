#!/usr/bin/env bash
# Build + ad-hoc sign the PrismAudio HAL driver (macOS only).
#
#   bash mac/build_driver.sh [version]
#
# Output: mac/dist/PrismAudio.driver -- copy it into the app bundle (or let
# prism/bootstrap.py install it from mac/dist/ in dev runs). Mirrors the
# upstream Krasp Makefile's `hal` target: one clang -bundle compile, a plist
# fix-up, and codesign. Ad-hoc signing (`--sign -`) is enough for coreaudiod
# to load the bundle -- no Apple Developer account needed; the app-level
# Gatekeeper warning is a separate (notarization) concern.
#
# Runs on any Mac with the Xcode Command Line Tools, including GitHub's
# macos-* runners (see .github/workflows/mac.yml).
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-0.0.0}"
SRC=PrismAudioDriver
OUT=dist/PrismAudio.driver

rm -rf dist
mkdir -p "$OUT/Contents/MacOS"

cp "$SRC/Info.plist" "$OUT/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$OUT/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$OUT/Contents/Info.plist"
plutil -lint "$OUT/Contents/Info.plist"

clang -std=c11 -Wall -Wextra -Werror -fvisibility=hidden -bundle \
    -framework CoreAudio -framework CoreFoundation \
    "$SRC/PrismHAL.c" \
    -o "$OUT/Contents/MacOS/PrismHAL"

codesign --force --sign - "$OUT"
codesign --verify --verbose=2 "$OUT"

echo "Built $OUT (version $VERSION)"
