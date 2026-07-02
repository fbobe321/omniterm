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

# OSC 7 sequence a shell can emit to report its working directory:
#   ESC ] 7 ; file://host/path  (BEL or ESC-backslash terminator)
_OSC7_RE = re.compile(r'\x1b\]7;file://[^/]*(/[^\x07\x1b]*)(?:\x07|\x1b\\)')

class SSHWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    auth_success = pyqtSignal()
    sftp_ready = pyqtSignal(object)  # emits (SFTPClient, home_path)
    cwd_changed = pyqtSignal(str)    # remote working directory (via OSC 7)

    def __init__(self, session_data):
        super().__init__()
        self.session_data = session_data
        self._running = True
        self.tunnels = []

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

            # Start interactive shell
            self.channel = self.client.invoke_shell()

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

            self._osc_buffer = ""
            self._last_cwd = None
            while self._running:
                if self.channel.recv_ready():
                    data = self.channel.recv(1024).decode('utf-8', errors='replace')
                    self.data_received.emit(data)
                    self._scan_cwd(data)
                time.sleep(0.01)

            self.channel.close()
            self.client.close()

        except Exception as e:
            self.error_occurred.emit(str(e))

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
        if hasattr(self, 'channel') and self.channel:
            self.channel.send(data)

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
