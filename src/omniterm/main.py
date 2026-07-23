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

    rc = app.exec()

    # Leave the process here, before Qt/Python tear down the widget tree.
    #
    # Qt calls qFatal() - killing the app with "QThread: Destroyed while thread
    # '...' is still running" - if any QThread object is destroyed while its
    # run() has not returned. MainWindow.closeEvent stops and joins every worker
    # it can, but teardown gives a single stuck thread (a Windows ConPTY read
    # inside pywinpty, a paramiko socket wedged on a dead link) the chance to
    # turn a clean exit into a crash, right in front of the user. There is
    # nothing left to save at this point - config and sessions are written as
    # they change - so skip destruction entirely: os._exit ends the process
    # immediately with the event loop's exit code and no C++ teardown to abort.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc)


if __name__ == "__main__":
    main()
