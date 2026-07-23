from PyQt6.QtCore import QThread, pyqtSignal
import subprocess
import os
import re
import shutil
import time
import urllib.parse
from omniterm.core.config import HOME_DIR
from omniterm.core.threads import register

# Make bash report its working directory via OSC 7 on every prompt. This is set
# in the shell's *environment* at spawn (below), so nothing is ever typed into
# the terminal - the "Follow terminal folder" panel just works, with no visible
# command for the user to delete. Non-bash shells ignore this variable harmlessly.
OSC7_PROMPT_COMMAND = r'''printf '\033]7;file://%s%s\007' "$HOSTNAME" "$PWD"'''

# OSC 7 as emitted above: ESC ] 7 ; file://<host><path> BEL (or ST). We ignore
# the host and capture the path. Mirrors the scanner in ssh_client.py.
_OSC7_RE = re.compile(r'\x1b\]7;file://[^/]*(/[^\x07\x1b]*)(?:\x07|\x1b\\)')


class LocalPTYWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    disconnected = pyqtSignal(str)
    cwd_changed = pyqtSignal(str)   # shell's cwd, parsed from OSC 7

    def __init__(self, prefer_unix=False, inshellisense=False, startup=None):
        super().__init__()
        register(self, "local-pty-worker")
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
        # OSC 7 cwd tracking state (see _scan_cwd)
        self._osc_buffer = ""
        self._last_cwd = None
        self._osc7_seen = False   # disables the /proc fallback once OSC 7 works

    def _scan_cwd(self, data):
        """Detect the shell's working directory from OSC 7 sequences and emit
        cwd_changed when it changes (mirrors SSHWorker)."""
        self._osc_buffer += data
        if len(self._osc_buffer) > 4096:
            self._osc_buffer = self._osc_buffer[-4096:]
        matches = _OSC7_RE.findall(self._osc_buffer)
        if matches:
            self._osc7_seen = True
            path = urllib.parse.unquote(matches[-1])
            self._osc_buffer = ""
            if path and path != self._last_cwd:
                self._last_cwd = path
                self.cwd_changed.emit(path)

    def _poll_proc_cwd(self, pid):
        """Read the shell's cwd from /proc (Linux); fallback when OSC 7 is
        unavailable. Emits cwd_changed just like the OSC 7 path."""
        try:
            path = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return
        if path and path != self._last_cwd:
            self._last_cwd = path
            self.cwd_changed.emit(path)

    def _run_startup(self):
        if self.startup:
            self.send_data(self.startup + "\r")

    def _maybe_start_inshellisense(self):
        """If enabled, launch Inshellisense ('is') in the shell for autocomplete.
        The check runs INSIDE the shell (not against OmniTerm's PATH) so it works
        when 'is' is on the shell's PATH but not the launcher process's PATH -
        which is the common case on Windows (npm global bin)."""
        if not self.inshellisense:
            return
        hint = "install: npm i -g @microsoft/inshellisense (if already installed, run: is reinit)"
        if os.name == "nt" and not self.prefer_unix:
            # cmd.exe
            self.send_data(
                f"where is >nul 2>nul && is || echo [OmniTerm] Inshellisense not found: {hint}\r")
        else:
            # bash / zsh (Home terminal / WSL / Linux / macOS)
            self.send_data(
                f'command -v is >/dev/null 2>&1 && is || '
                f'echo "[OmniTerm] Inshellisense not found: {hint}"\r')

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
        env["PROMPT_COMMAND"] = OSC7_PROMPT_COMMAND
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
                            self._scan_cwd(data)
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
                    # Report cwd via OSC 7 so the Files panel can follow, without
                    # typing a visible command into the shell.
                    os.environ["PROMPT_COMMAND"] = OSC7_PROMPT_COMMAND
                    try:
                        os.execv(shell, [shell])
                    except Exception:
                        os._exit(1)

                os.close(slave)
                if self.prefer_unix:
                    self._emit_rsync_status()
                self._run_startup()
                self._maybe_start_inshellisense()

                # Fallback cwd tracking via /proc for shells that don't emit
                # OSC 7 (zsh; bash whose rc files clobber PROMPT_COMMAND).
                # Stops permanently once a real OSC 7 sequence is seen.
                can_poll_cwd = os.path.isdir(f"/proc/{pid}")
                last_cwd_poll = 0.0

                while self._running:
                    # select wakes as soon as data is available (low latency).
                    r, w, e = select.select([self.master_fd], [], [], 0.1)
                    if can_poll_cwd and not self._osc7_seen:
                        now = time.monotonic()
                        if now - last_cwd_poll >= 1.0:
                            last_cwd_poll = now
                            self._poll_proc_cwd(pid)
                    if r:
                        # Drain what's available now and emit immediately.
                        chunk = b""
                        eof = False
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
                            more, _, _ = select.select([self.master_fd], [], [], 0)
                            if not more or len(chunk) >= 262144:
                                break
                        if chunk:
                            text = chunk.decode('utf-8', errors='replace')
                            self.data_received.emit(text)
                            self._scan_cwd(text)
                        if eof:
                            break

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


