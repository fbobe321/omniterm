import os
import sys

# Configure Qt WebEngine (Chromium) rendering BEFORE it initializes.
# We intentionally do NOT force the GPU / ignore the GPU blocklist: on machines
# with flaky or virtual GPUs (common at work / VDI) that causes the screen to
# blank under heavy full-screen redraws (htop/btop). Let Chromium decide, and
# offer a hard software-rendering switch for machines that still misbehave.
from omniterm.core.config import get_disable_gpu

_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
if get_disable_gpu():
    _flags = (_flags + " --disable-gpu --disable-gpu-compositing").strip()
if _flags:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _flags

from PyQt6.QtWidgets import QApplication
from omniterm.ui.main_window import MainWindow
from omniterm.ui.icons import app_icon


def main():
    # On Windows, give the app its own taskbar identity so it uses our icon
    # instead of the generic Python one.
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OmniTerm")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("OmniTerm")
    app.setWindowIcon(app_icon())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
