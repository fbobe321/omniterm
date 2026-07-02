from PyQt6.QtCore import QThread, pyqtSignal
import subprocess
import os
import time

class LocalPTYWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running = True
        self.process = None
        self.master_fd = None
        self.pty = None
        self.cols = 80
        self.rows = 24

    def run(self):
        try:
            if os.name == 'nt':
                from pywinpty import PtyProcess
                self.pty = PtyProcess.spawn('cmd.exe', dimensions=(self.rows, self.cols))
                while self._running:
                    try:
                        data = self.pty.read(1024)
                        if data:
                            self.data_received.emit(data)
                    except EOFError:
                        break
                    except Exception:
                        time.sleep(0.01)
            else:
                # Linux/macOS PTY implementation
                import pty
                import select

                master, slave = pty.openpty()
                self.master_fd = master
                self._set_winsize(self.rows, self.cols)

                pid = os.fork()
                if pid == 0:
                    os.setsid()
                    os.dup2(slave, 0)
                    os.dup2(slave, 1)
                    os.dup2(slave, 2)
                    os.execv('/bin/bash', ['/bin/bash'])

                os.close(slave)

                while self._running:
                    r, w, e = select.select([self.master_fd], [], [], 0.1)
                    if r:
                        data = os.read(self.master_fd, 1024).decode('utf-8', errors='replace')
                        self.data_received.emit(data)
                    time.sleep(0.01)

                os.close(self.master_fd)
        except Exception as e:
            self.error_occurred.emit(str(e))

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


