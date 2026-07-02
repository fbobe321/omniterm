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


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OmniTerm")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
