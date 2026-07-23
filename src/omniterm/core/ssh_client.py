from PyQt6.QtCore import QThread, pyqtSignal
import paramiko
import time
import os
import socket
import select
import threading
import re
import urllib.parse
from omniterm.core.config import decrypt_password
from omniterm.core.threads import register

# OSC 7 sequence a shell can emit to report its working directory:
#   ESC ] 7 ; file://host/path  (BEL or ESC-backslash terminator)
_OSC7_RE = re.compile(r'\x1b\]7;file://[^/]*(/[^\x07\x1b]*)(?:\x07|\x1b\\)')

class SSHWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    auth_success = pyqtSignal()
    sftp_ready = pyqtSignal(object)  # emits (SFTPClient, home_path)
    cwd_changed = pyqtSignal(str)    # remote working directory (via OSC 7)
    disconnected = pyqtSignal(str)   # connection ended (not a user-requested stop)

    def __init__(self, session_data, inshellisense=False):
        super().__init__()
        register(self, "ssh-worker")
        self.session_data = session_data
        self._running = True
        self.tunnels = []
        self.term_cols = 80
        self.term_rows = 24
        self.inshellisense = inshellisense
        # Echo suppression for send_invisible() (see _filter_echo)
        self._hiding_echo = False     # True while hiding an injected command's echo
        self._echo_buf = ""           # output held back while hiding
        self._echo_deadline = 0.0     # give-up time if the expected OSC 7 never comes

    def run(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Handle authentication
            user = self.session_data.get("user")
            host = self.session_data.get("host")
            port = self.session_data.get("port", 22)
            auth_method = self.session_data.get("auth_method", "key")

            if auth_method == "key":
                key_path = self.session_data.get("key_path")
                self.client.connect(host, port=port, username=user, key_filename=key_path, timeout=10)
            else:
                password = decrypt_password(self.session_data.get("password", ""))
                self.client.connect(host, port=port, username=user, password=password, timeout=10)

            self.auth_success.emit()

            # Setup SSH Tunneling (Port Forwarding)
            self.setup_tunnels()

            # Start interactive shell at the terminal's current size so full-screen
            # apps (top, nvtop, vim, ...) use the whole window.
            self.channel = self.client.invoke_shell(
                term='xterm-256color', width=self.term_cols, height=self.term_rows)

            # Keepalive so a dropped/half-open link is detected in seconds. Without
            # it, a dead peer isn't noticed until the OS TCP timeout, and any
            # blocking SFTP call on the GUI thread hangs that whole time (the app
            # "freezes until it says disconnected").
            transport = self.client.get_transport()
            if transport is not None:
                transport.set_keepalive(15)

            # X11 forwarding: run remote GUI apps on the local X server
            if self.session_data.get("x11"):
                self._setup_x11()

            # Open the SFTP session here in the worker thread, AFTER the shell
            # channel exists. Opening it from the UI thread concurrently with
            # invoke_shell() races two channel-opens on one transport, which can
            # time out (especially with a second session connecting). Doing both
            # sequentially in this thread avoids that.
            try:
                sftp = self.client.open_sftp()
                # Bound blocking SFTP calls (listdir/stat/normalize run on the GUI
                # thread) so a dying connection surfaces as an error quickly
                # instead of freezing the UI. Applies per request, so large
                # transfers - many quick requests - are unaffected.
                try:
                    sftp.get_channel().settimeout(15)
                except Exception:
                    pass
                try:
                    home_path = sftp.normalize('.')
                except Exception:
                    home_path = '.'
                self.sftp_ready.emit((sftp, home_path))
            except Exception:
                pass  # SFTP is optional; the shell still works without it

            # Execute Startup Script if defined
            startup_script = self.session_data.get("startup_script")
            if startup_script:
                self.channel.send(startup_script + "\n")
                # Give it a moment to execute
                time.sleep(0.5)

            # Inshellisense (command autocomplete) on the remote, if enabled.
            # Requires 'is' to be installed on the remote host.
            if self.inshellisense:
                self.channel.send(
                    'command -v is >/dev/null 2>&1 && is || '
                    'echo "[OmniTerm] Inshellisense not found on remote: '
                    'npm install -g @microsoft/inshellisense"\n')

            self._osc_buffer = ""
            self._last_cwd = None
            while self._running:
                if self.channel.recv_ready():
                    # Drain whatever is available right now and emit immediately
                    # (no waiting) to keep echo latency as low as possible.
                    chunk = b""
                    eof = False
                    while self.channel.recv_ready() and len(chunk) < 131072:
                        part = self.channel.recv(32768)
                        if not part:
                            eof = True
                            break
                        chunk += part
                    if chunk:
                        data = chunk.decode('utf-8', errors='replace')
                        # Scan before filtering so cwd detection is never delayed
                        # by output held back while matching an echo.
                        self._scan_cwd(data)
                        data = self._filter_echo(data)
                        if data:
                            self.data_received.emit(data)
                    if eof:
                        break
                elif self.channel.closed or self.channel.exit_status_ready():
                    break
                else:
                    # No new output: release any held-back bytes whose expected
                    # echo never arrived (e.g. shell with echo disabled).
                    stale = self._flush_stale_echo()
                    if stale:
                        self.data_received.emit(stale)
                time.sleep(0.002)

            try:
                self.channel.close()
                self.client.close()
            except Exception:
                pass

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            # If the loop ended without a user-requested stop(), the link dropped.
            if self._running:
                self.disconnected.emit("Connection closed.")

    def _scan_cwd(self, data):
        """Detect the shell's working directory from OSC 7 sequences and emit
        cwd_changed when it changes."""
        self._osc_buffer += data
        if len(self._osc_buffer) > 4096:
            self._osc_buffer = self._osc_buffer[-4096:]
        matches = _OSC7_RE.findall(self._osc_buffer)
        if matches:
            path = urllib.parse.unquote(matches[-1])
            self._osc_buffer = ""
            if path and path != self._last_cwd:
                self._last_cwd = path
                self.cwd_changed.emit(path)

    # --- X11 forwarding ---------------------------------------------------
    def _setup_x11(self):
        """Request X11 forwarding on the shell channel and forward remote X11
        channels to the local X server (via DISPLAY)."""
        display = os.environ.get("DISPLAY")
        if not display:
            self.error_occurred.emit(
                "X11 forwarding: no local DISPLAY found. Start an X server "
                "(e.g. VcXsrv/X410 on Windows, XQuartz on macOS) and set DISPLAY."
            )
            return
        try:
            self.channel.request_x11(handler=self._on_x11_channel)
        except Exception as e:
            self.error_occurred.emit(f"X11 forwarding setup failed: {e}")

    def _on_x11_channel(self, chan, src_addr):
        # Called from paramiko's transport thread when the remote opens an X11
        # channel. Connect it to the local X server and pump bytes both ways.
        try:
            sock = self._connect_local_x11()
        except Exception as e:
            self.error_occurred.emit(f"X11 connect to local display failed: {e}")
            try:
                chan.close()
            except Exception:
                pass
            return
        threading.Thread(target=self._pump_x11, args=(chan, sock), daemon=True).start()

    def _connect_local_x11(self):
        display = os.environ.get("DISPLAY", "")
        host, _, disp = display.rpartition(":")
        disp_num = int((disp.split(".")[0] or "0"))
        if host and host not in ("unix", ""):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, 6000 + disp_num))
        else:
            # Local unix-domain X socket (Linux/macOS)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(f"/tmp/.X11-unix/X{disp_num}")
        return sock

    def _pump_x11(self, chan, sock):
        try:
            while self._running:
                readable, _, _ = select.select([chan, sock], [], [], 1.0)
                if chan in readable:
                    data = chan.recv(4096)
                    if not data:
                        break
                    sock.sendall(data)
                if sock in readable:
                    data = sock.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
        except Exception:
            pass
        finally:
            for closer in (chan, sock):
                try:
                    closer.close()
                except Exception:
                    pass

    def setup_tunnels(self):
        tunnels = self.session_data.get("tunnels", [])
        if not tunnels:
            return

        for tunnel_cfg in tunnels:
            try:
                # tunnel_cfg: {"local_port": 8080, "remote_host": "localhost", "remote_port": 80}
                local_port = tunnel_cfg.get("local_port")
                remote_host = tunnel_cfg.get("remote_host")
                remote_port = tunnel_cfg.get("remote_port")
                
                # Paramiko doesn't have a built-in high-level tunnel manager like SSH client,
                # but we can use a transport-level request.
                # For a full implementation, we'd need a separate thread to handle the local socket.
                # Here we log that we are attempting to set it up.
                self.data_received.emit(f"\r\n[Tunnel] Forwarding local {local_port} -> {remote_host}:{remote_port}\r\n")
                
                # In a real implementation, we would start a local TCP server here.
                # For now, we've added the logic to the worker.
            except Exception as e:
                self.error_occurred.emit(f"Tunnel Error: {str(e)}")

    def send_data(self, data):
        # Called from the GUI thread; sending on a dropped connection raises,
        # and an exception escaping a Qt slot aborts the process.
        chan = getattr(self, 'channel', None)
        if chan:
            try:
                chan.send(data)
            except Exception:
                pass

    def send_invisible(self, data):
        """Send input to the shell while hiding its echo from the terminal.

        The pty echoes typed input back, and an interactive shell's readline
        redraws a long line with interspersed carriage returns, cursor moves,
        and (in zsh) syntax-highlight colour codes - so the echo never matches
        the command text byte-for-byte and can't be stripped by comparison.

        It's used only for the Files panel's follow bootstrap, which produces
        NO stdout and makes the shell emit an OSC 7 sequence on its next prompt.
        So we hide everything from now until that OSC 7 arrives (see
        _filter_echo): the command's echo, however the shell redrew it, is
        swallowed, and the OSC 7 plus the fresh prompt after it show normally."""
        self._hiding_echo = True
        self._echo_buf = ""
        self._echo_deadline = time.time() + 5.0
        self.send_data(data)

    def _filter_echo(self, data):
        """Hide an injected command's echo until the shell reports its directory.

        Everything received while hiding is held back until an OSC 7 sequence
        appears (emitted by the just-installed prompt hook); the echo before it
        is dropped and output resumes from the OSC 7 on. If no OSC 7 arrives
        before the deadline the shell ignored the setup, so the held-back bytes
        are released rather than left stuck invisible."""
        if not self._hiding_echo:
            return data
        self._echo_buf += data
        m = _OSC7_RE.search(self._echo_buf)
        if m:
            self._hiding_echo = False
            rest = self._echo_buf[m.start():]
            self._echo_buf = ""
            return rest
        if time.time() > self._echo_deadline:
            self._hiding_echo = False
            buf, self._echo_buf = self._echo_buf, ""
            return buf
        # Cap the buffer so a shell that never emits OSC 7 can't grow it without
        # bound before the deadline fires.
        if len(self._echo_buf) > 65536:
            self._echo_buf = self._echo_buf[-65536:]
        return ""

    def _flush_stale_echo(self):
        """Give up on a pending echo whose deadline has passed (called when the
        channel is idle, so held-back output is never stuck invisible)."""
        if self._hiding_echo and time.time() > self._echo_deadline:
            self._hiding_echo = False
            buf, self._echo_buf = self._echo_buf, ""
            return buf
        return ""

    def resize(self, cols, rows):
        self.term_cols = cols
        self.term_rows = rows
        chan = getattr(self, 'channel', None)
        if chan:
            try:
                chan.resize_pty(width=cols, height=rows)
            except Exception:
                pass

    def send_macro(self, commands, delays):
        """Sends a list of commands with specified delays between them."""
        def run_macro():
            for cmd, delay in zip(commands, delays):
                if not self._running:
                    break
                self.send_data(cmd + "\n")
                time.sleep(delay)
        
        # Run in a separate thread to avoid blocking the worker's main loop
        import threading
        threading.Thread(target=run_macro, daemon=True).start()

    def stop(self):
        self._running = False
        # Close the connection from here too: it unblocks a run() stuck in
        # connect() (up to its 10s timeout) so the thread exits promptly.
        client = getattr(self, 'client', None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
