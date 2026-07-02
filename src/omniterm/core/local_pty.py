from PyQt6.QtCore import QThread, pyqtSignal
import subprocess
import os
import shutil
import time
from omniterm.core.config import HOME_DIR

class LocalPTYWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    disconnected = pyqtSignal(str)

    def __init__(self, prefer_unix=False, inshellisense=False, startup=None):
        super().__init__()
        self._running = True
        self.process = None
        self.master_fd = None
        self.pty = None
        self.cols = 80
        self.rows = 24
        # "Home" terminal: prefer a Unix-like shell (Git Bash/WSL/BusyBox on Windows)
        self.prefer_unix = prefer_unix
        # Inshellisense (Microsoft 'is'): IDE-style command autocomplete
        self.inshellisense = inshellisense
        # One-shot command(s) sent after the shell starts (e.g. cd; conda activate)
        self.startup = startup

    def _run_startup(self):
        if self.startup:
            self.send_data(self.startup + "\r")

    def _maybe_start_inshellisense(self):
        """If enabled and 'is' is installed, start it in the shell for
        autocomplete; otherwise show a one-time hint."""
        if not self.inshellisense:
            return
        if shutil.which("is"):
            self.send_data("is\r")
        else:
            self.data_received.emit(
                "\x1b[33m[OmniTerm] Inshellisense enabled but 'is' was not found. "
                "Install it: npm install -g @microsoft/inshellisense\x1b[0m\r\n")

    def _tools_dir(self):
        """OmniTerm's own bin dir, added to the Home terminal PATH so users can
        drop tools (e.g. rsync.exe) there and have them available."""
        path = os.path.join(str(HOME_DIR), "bin")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        return path

    def _env_with_tools(self):
        env = dict(os.environ)
        env["PATH"] = self._tools_dir() + os.pathsep + env.get("PATH", "")
        return env

    def _find_rsync(self):
        """Return a path to an available rsync, or None."""
        candidates = [os.path.join(self._tools_dir(), "rsync.exe" if os.name == "nt" else "rsync")]
        if os.name == "nt":
            candidates += [
                r"C:\Program Files\Git\usr\bin\rsync.exe",
                r"C:\Program Files\Git\mingw64\bin\rsync.exe",
                r"C:\Program Files (x86)\Git\usr\bin\rsync.exe",
            ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return shutil.which("rsync")

    def _emit_rsync_status(self):
        if self._find_rsync():
            self.data_received.emit(
                "\x1b[36m[OmniTerm Home] rsync: available\x1b[0m\r\n")
        else:
            tools = self._tools_dir()
            self.data_received.emit(
                "\x1b[33m[OmniTerm Home] rsync not found. To enable file syncing: "
                "use WSL, run 'pacman -S rsync' in a Git-for-Windows SDK, or drop "
                f"rsync.exe (with its msys-*.dll) into:\r\n  {tools}\r\n"
                "That folder is on this terminal's PATH.\x1b[0m\r\n"
                if os.name == "nt" else
                "\x1b[33m[OmniTerm Home] rsync not found. Install it via your package "
                f"manager, or drop an rsync binary into {tools} (it's on PATH).\x1b[0m\r\n")

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
                spawn_env = self._env_with_tools() if self.prefer_unix else None
                self.pty = PtyProcess.spawn(
                    command, dimensions=(self.rows, self.cols), env=spawn_env)
                if self.prefer_unix:
                    if backend:
                        self.data_received.emit(
                            f"\x1b[36m[OmniTerm Home] Unix environment: {backend}\x1b[0m\r\n")
                    else:
                        self.data_received.emit(
                            "\x1b[33m[OmniTerm Home] No Unix environment found (Git Bash / WSL / "
                            "BusyBox). Falling back to cmd. Install Git for Windows or WSL for "
                            "ls/grep/awk/scp/rsync.\x1b[0m\r\n")
                    self._emit_rsync_status()
                self._run_startup()
                self._maybe_start_inshellisense()
                while self._running:
                    try:
                        data = self.pty.read(65536)
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
                    if self.prefer_unix:
                        os.environ["PATH"] = self._tools_dir() + os.pathsep + os.environ.get("PATH", "")
                    try:
                        os.execv(shell, [shell])
                    except Exception:
                        os._exit(1)

                os.close(slave)
                if self.prefer_unix:
                    self._emit_rsync_status()
                self._run_startup()
                self._maybe_start_inshellisense()

                while self._running:
                    r, w, e = select.select([self.master_fd], [], [], 0.1)
                    if r:
                        # Coalesce a burst into one write so a line redraw renders
                        # atomically. Wait a few ms for more data after each read
                        # (to catch a redraw arriving in pieces), capped so latency
                        # stays imperceptible.
                        chunk = b""
                        eof = False
                        deadline = time.monotonic() + 0.02
                        while True:
                            try:
                                part = os.read(self.master_fd, 65536)
                            except OSError:
                                eof = True
                                break
                            if not part:
                                eof = True
                                break
                            chunk += part
                            if len(chunk) >= 262144 or time.monotonic() >= deadline:
                                break
                            more, _, _ = select.select([self.master_fd], [], [], 0.004)
                            if not more:
                                break
                        if chunk:
                            self.data_received.emit(chunk.decode('utf-8', errors='replace'))
                        if eof:
                            break
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


