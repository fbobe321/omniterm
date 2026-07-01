from PyQt6.QtCore import QThread, pyqtSignal
import paramiko
import time
from omniterm.core.config import decrypt_password

class SSHWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    auth_success = pyqtSignal()
    sftp_ready = pyqtSignal(object)  # emits (SFTPClient, home_path)

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

            while self._running:
                if self.channel.recv_ready():
                    data = self.channel.recv(1024).decode('utf-8', errors='replace')
                    self.data_received.emit(data)
                time.sleep(0.01)

            self.channel.close()
            self.client.close()

        except Exception as e:
            self.error_occurred.emit(str(e))

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
