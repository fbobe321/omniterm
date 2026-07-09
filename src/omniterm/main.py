import os
import sys

# Ask Qt WebEngine (Chromium) to use the GPU so the terminal renders smoothly.
# Must be set before QtWebEngine initializes.
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--ignore-gpu-blocklist --enable-gpu-rasterization --enable-zero-copy",
)

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
