import os
import sys

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
