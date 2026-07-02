from PyQt6.QtCore import QThread, pyqtSignal
import subprocess
import os
import shutil
import time

class LocalPTYWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    disconnected = pyqtSignal(str)

    def __init__(self, prefer_unix=False):
        super().__init__()
        self._running = True
        self.process = None
        self.master_fd = None
        self.pty = None
        self.cols = 80
        self.rows = 24
        # "Home" terminal: prefer a Unix-like shell (Git Bash/WSL/BusyBox on Windows)
        self.prefer_unix = prefer_unix

    def _windows_command(self):
        """Pick the best available shell on Windows. Returns (argv_list, label).
        A list avoids quoting problems with paths that contain spaces. When
        prefer_unix is set, look for a Unix environment before cmd."""
        if not self.prefer_unix:
            return ['cmd.exe'], 'cmd.exe'

        for path in (r"C:\Program Files\Git\bin\bash.exe",
                     r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(path):
                return [path, '--login', '-i'], "Git Bash"

        bash = shutil.which('bash')
        if bash:
            return [bash, '--login', '-i'], "bash"

        wsl = shutil.which('wsl')
        if wsl:
            return [wsl], "WSL"

        busybox = shutil.which('busybox')
        if busybox:
            return [busybox, 'sh'], "BusyBox"

        return ['cmd.exe'], None  # None -> no unix env found

    def run(self):
        try:
            if os.name == 'nt':
                command, backend = self._windows_command()
                # The 'pywinpty' pip package is imported as 'winpty'
                try:
                    from winpty import PtyProcess
                except ImportError:
                    from pywinpty import PtyProcess  # older/alternate layouts
                self.pty = PtyProcess.spawn(command, dimensions=(self.rows, self.cols))
                if self.prefer_unix:
                    if backend:
                        self.data_received.emit(
                            f"\x1b[36m[OmniTerm Home] Unix environment: {backend}\x1b[0m\r\n")
                    else:
                        self.data_received.emit(
                            "\x1b[33m[OmniTerm Home] No Unix environment found (Git Bash / WSL / "
                            "BusyBox). Falling back to cmd. Install Git for Windows or WSL for "
                            "ls/grep/awk/scp/rsync.\x1b[0m\r\n")
                while self._running:
                    try:
                        data = self.pty.read(1024)
                        if data:
                            self.data_received.emit(data)
                        elif not self.pty.isalive():
                            break
                    except EOFError:
                        break
                    except Exception:
                        time.sleep(0.01)
            else:
                # Linux/macOS: launch the user's shell
                import pty
                import select

                shell = os.environ.get('SHELL') or '/bin/bash'

                master, slave = pty.openpty()
                self.master_fd = master
                self._set_winsize(self.rows, self.cols)

                pid = os.fork()
                if pid == 0:
                    os.setsid()
                    os.dup2(slave, 0)
                    os.dup2(slave, 1)
                    os.dup2(slave, 2)
                    try:
                        os.execv(shell, [shell])
                    except Exception:
                        os._exit(1)

                os.close(slave)

                while self._running:
                    r, w, e = select.select([self.master_fd], [], [], 0.1)
                    if r:
                        raw = os.read(self.master_fd, 1024)
                        if not raw:  # EOF: the shell exited
                            break
                        self.data_received.emit(raw.decode('utf-8', errors='replace'))
                    time.sleep(0.01)

                try:
                    os.close(self.master_fd)
                except Exception:
                    pass
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            if self._running:
                self.disconnected.emit("Session ended.")

    def stop(self):
        self._running = False
        if self.pty:
            try:
                self.pty.close()
            except:
                pass
        if self.process:
            self.process.terminate()
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except:
                pass

    def _set_winsize(self, rows, cols):
        if self.master_fd is None:
            return
        try:
            import fcntl
            import termios
            import struct
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ,
                        struct.pack('HHHH', rows, cols, 0, 0))
        except Exception:
            pass

    def resize(self, cols, rows):
        self.cols = cols
        self.rows = rows
        if os.name == 'nt' and self.pty:
            try:
                self.pty.setwinsize(rows, cols)
            except Exception:
                pass
        else:
            self._set_winsize(rows, cols)

    def send_data(self, data):
        if os.name == 'nt' and self.pty:
            try:
                self.pty.write(data)
            except Exception as e:
                self.error_occurred.emit(f"Windows PTY Write Error: {e}")
        elif self.master_fd:
            try:
                os.write(self.master_fd, data.encode('utf-8'))
            except Exception as e:
                self.error_occurred.emit(f"PTY Write Error: {e}")


