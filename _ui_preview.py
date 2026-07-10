"""Throwaway: render ui_qt with a fake engine and screenshot it (no audio)."""
import math, random, sys
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


class FakeEngine:
    def __init__(self):
        self.enabled = True
        self.input_index = 1
        self.in_db = -70.0
        self.out_db = -70.0
        self.gate_open = True
        self.noise_floor_db = -52.0
        self.reduction_db = 14.0
        self.denoiser_available = True
        self.denoiser_name = "RNNoise"
        self.denoiser_enabled = True
        self.denoiser_mix = 1.0
        self._t = 0

    def set_denoiser(self, c): pass
    def switch_input(self, i): pass
    def stop(self): pass

    def tick(self):
        # Fake a speech-ish envelope: bursts of "speech" over a noise floor.
        self._t += 1
        speaking = (self._t // 45) % 2 == 0
        if speaking:
            env = -18 + 10 * math.sin(self._t * 0.35) + random.uniform(-6, 3)
            self.in_db = env
            self.out_db = env - random.uniform(1, 3)
            self.gate_open = True
        else:
            self.in_db = -38 + random.uniform(-4, 4)   # room noise
            self.out_db = -70 + random.uniform(-5, 5)  # stripped
            self.gate_open = False


def main():
    engine = FakeEngine()
    fake_devices = [(1, "Microphone (Blue Yeti)"), (2, "Headset Mic (USB)")]

    from prism import ui_qt

    with patch.object(ui_qt.audio, "list_input_devices", lambda: fake_devices), \
         patch.object(ui_qt.sd, "query_devices",
                      lambda i=None: {"name": "Microphone (Blue Yeti)"}):
        app = QApplication.instance() or QApplication(sys.argv)

        drive = QTimer()
        drive.timeout.connect(engine.tick)
        drive.start(33)

        def shoot():
            w = QApplication.activeWindow() or QApplication.topLevelWidgets()[0]
            w.grab().save("_ui_preview.png")
            app.quit()

        QTimer.singleShot(6000, shoot)  # let the scope fill up first
        ui_qt.run_ui(engine)


if __name__ == "__main__":
    main()
