"""Startup bootstrap: make sure the virtual audio device exists.

Replaces the old dead-end guard ("go install VB-Cable, then come back") with a
best-effort self-install, per platform:

  Windows -- run the bundled VB-Cable installer (one UAC prompt; VB-Cable
             still requires one reboot before the device appears).
  Linux   -- spawn a PipeWire loopback pair at runtime (no install, no
             prompt); torn down when Prism exits.
  macOS   -- copy the bundled PrismAudio HAL driver into the system HAL
             folder (one admin password prompt, no reboot) and restart
             CoreAudio. Prism then feeds the driver through a shared-memory
             ring (prism/ring_output.py) instead of an output device.

Every path is idempotent: if the device is already present nothing runs. On
any failure/decline the caller falls back to manual install instructions.
"""

import os
import shutil
import subprocess
import sys
import time

import sounddevice as sd

from . import audio, config


class BootstrapResult:
    """What the bootstrap achieved.

    ``ok``           -- a usable virtual device exists.
    ``output_index`` -- device index Prism writes to (None on macOS, where the
                        engine writes to the shared ring instead).
    ``message``      -- human-readable explanation when ``ok`` is False (or a
                        notice worth showing, e.g. "reboot needed").
    """

    def __init__(self, ok, output_index=None, message=None):
        self.ok = ok
        self.output_index = output_index
        self.message = message


def _resource_path(relative):
    """Resolve a bundled resource in both frozen and source runs.

    Frozen (PyInstaller) builds unpack next to the executable/_MEIPASS; source
    runs resolve against the repo root.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def _rescan():
    """Force PortAudio to re-enumerate devices (it caches the list at init)."""
    sd._terminate()
    sd._initialize()


def ensure_virtual_device(ask=None):
    """Return a BootstrapResult, installing/creating the device if needed.

    ``ask(message) -> bool`` is called before anything that shows an elevation
    prompt (Windows UAC, macOS admin password); ``None`` counts as declined so
    nothing privileged ever runs unprompted. Linux needs no elevation, so it
    proceeds without asking.
    """
    if sys.platform == "darwin":
        return _ensure_macos(ask)
    if sys.platform.startswith("linux"):
        return _ensure_linux()
    return _ensure_windows(ask)


# --- Windows: bundled VB-Cable installer -------------------------------------

_WINDOWS_REBOOT_MSG = """\
VB-Cable was installed, but Windows needs a reboot before the new audio
device appears. Reboot, then start Prism again."""


def _ensure_windows(ask):
    index = audio.find_device(config.CABLE_NAME, "output")
    if index is not None:
        return BootstrapResult(True, output_index=index)

    installer = _resource_path(config.VBCABLE_INSTALLER)
    if not os.path.exists(installer):
        return BootstrapResult(False)  # no installer bundled -> manual help
    if ask is None or not ask(
        "Prism needs the VB-Audio Virtual Cable driver (one-time install,\n"
        "requires administrator approval and a reboot). Install it now?"
    ):
        return BootstrapResult(False)

    # -Verb RunAs shows the UAC prompt; -Wait blocks until the installer's
    # window closes (the user clicks "Install Driver" in VB-Cable's GUI).
    # A declined UAC prompt makes Start-Process fail -> non-zero exit code.
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Start-Process -FilePath '{installer}' -Verb RunAs -Wait"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return BootstrapResult(False)

    _rescan()
    index = audio.find_device(config.CABLE_NAME, "output")
    if index is not None:  # rare pre-reboot, but take it
        return BootstrapResult(True, output_index=index)
    return BootstrapResult(False, message=_WINDOWS_REBOOT_MSG)


# --- Linux: PipeWire loopback at runtime --------------------------------------

_LINUX_HELP = """\
Could not create a virtual microphone: pw-loopback (PipeWire) not found.

Install PipeWire (most modern distros ship it: pipewire + pipewire-pulse),
or create a loopback yourself and set CABLE_NAME in prism/config.py."""

_loopback_proc = None  # kept for teardown; one per process


def _ensure_linux():
    global _loopback_proc
    index = audio.find_device(config.CABLE_NAME, "output")
    if index is not None:
        return BootstrapResult(True, output_index=index)
    if shutil.which("pw-loopback") is None:
        return BootstrapResult(False, message=_LINUX_HELP)

    # A sink apps can play into, looped to a virtual source apps record from.
    # No elevation, no install; dies with Prism (atexit below).
    _loopback_proc = subprocess.Popen([
        "pw-loopback",
        "--capture-props",
        f"media.class=Audio/Sink node.name={config.LINUX_SINK_NODE} "
        f'node.description="{config.LINUX_SINK_DESCRIPTION}"',
        "--playback-props",
        f"media.class=Audio/Source node.name={config.LINUX_SOURCE_NODE} "
        f'node.description="{config.LINUX_SOURCE_DESCRIPTION}"',
    ])
    import atexit
    atexit.register(_teardown_linux)

    # The node takes a moment to appear in PortAudio's device list.
    for _ in range(20):
        time.sleep(0.1)
        _rescan()
        index = audio.find_device(config.CABLE_NAME, "output")
        if index is not None:
            return BootstrapResult(True, output_index=index)
    return BootstrapResult(False, message=_LINUX_HELP)


def _teardown_linux():
    global _loopback_proc
    if _loopback_proc is not None and _loopback_proc.poll() is None:
        _loopback_proc.terminate()
    _loopback_proc = None


# --- macOS: bundled PrismAudio HAL driver -------------------------------------

_MAC_HAL_DIR = "/Library/Audio/Plug-Ins/HAL"

_MAC_STILL_MISSING_MSG = """\
The Prism Microphone driver was installed but the device did not appear.
Try restarting CoreAudio manually (sudo killall coreaudiod) or reboot, then
start Prism again."""


def _ensure_macos(ask):
    if audio.find_device(config.MAC_VIRTUAL_MIC_NAME, "input") is not None:
        return BootstrapResult(True, output_index=None)

    driver = _find_mac_driver()
    if driver is None:
        return BootstrapResult(False)  # not bundled -> manual help
    if ask is None or not ask(
        "Prism needs to install its virtual microphone (one-time, requires\n"
        "your administrator password, no reboot). Install it now?"
    ):
        return BootstrapResult(False)

    # One elevated shell: copy the driver in, strip the download-quarantine
    # flag so coreaudiod will load the ad-hoc-signed bundle, and kick
    # CoreAudio so it rescans its plug-ins. Paths are single-quoted for the
    # shell inside the AppleScript double-quoted string (they never contain
    # quotes or backslashes).
    dest = f"{_MAC_HAL_DIR}/{config.MAC_DRIVER_BUNDLE}"
    shell = (
        f"mkdir -p '{_MAC_HAL_DIR}' && rm -rf '{dest}' && "
        f"cp -R '{driver}' '{_MAC_HAL_DIR}/' && "
        f"xattr -dr com.apple.quarantine '{dest}' 2>/dev/null; "
        "launchctl kickstart -kp system/com.apple.audio.coreaudiod "
        "2>/dev/null || killall coreaudiod"
    )
    proc = subprocess.run(
        ["osascript", "-e",
         f'do shell script "{shell}" with administrator privileges '
         'with prompt "Prism wants to install its virtual microphone."'],
        capture_output=True,
    )
    if proc.returncode != 0:  # user hit Cancel on the password prompt
        return BootstrapResult(False)

    # coreaudiod takes a moment to come back up and load the plug-in.
    for _ in range(30):
        time.sleep(0.2)
        _rescan()
        if audio.find_device(config.MAC_VIRTUAL_MIC_NAME, "input") is not None:
            return BootstrapResult(True, output_index=None)
    return BootstrapResult(False, message=_MAC_STILL_MISSING_MSG)


def _find_mac_driver():
    """Locate the built PrismAudio.driver bundle (frozen app or dev checkout)."""
    candidates = [
        _resource_path(config.MAC_DRIVER_BUNDLE),               # frozen bundle
        _resource_path(os.path.join("mac", "dist",
                                    config.MAC_DRIVER_BUNDLE)),  # dev build/CI
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None
