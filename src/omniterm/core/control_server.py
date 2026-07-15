"""In-process control server for the running OmniTerm GUI.

Listens on a localhost socket and hands each request to a handler that runs on
the GUI thread (so it can safely touch widgets). The socket I/O happens on
background threads; requests are marshaled onto the Qt thread via a queued
signal, and the worker thread blocks on a threading.Event for the result.
"""
import secrets
import socket
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from omniterm.core import control


class _Call:
    __slots__ = ("cmd", "args", "event", "result")

    def __init__(self, cmd, args):
        self.cmd = cmd
        self.args = args
        self.event = threading.Event()
        self.result = None


class ControlServer(QObject):
    # Emitted from a socket thread; delivered (queued) on the GUI thread.
    _dispatch = pyqtSignal(object)

    def __init__(self, handler, parent=None):
        """handler(cmd: str, args: dict) -> dict, invoked on the GUI thread."""
        super().__init__(parent)
        self._handler = handler
        self._dispatch.connect(self._on_dispatch)   # cross-thread -> queued
        self._running = True
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self.token = secrets.token_hex(16)
        control.write_control_file(self.port, self.token)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while self._running:
            try:
                conn, _ = self._server.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,),
                             daemon=True).start()

    def _handle_conn(self, conn):
        try:
            req = control.recv_line(conn)
            import json
            try:
                obj = json.loads(req)
            except ValueError:
                control.send_json(conn, {"ok": False, "error": "bad request"})
                return
            if obj.get("token") != self.token:
                control.send_json(conn, {"ok": False, "error": "unauthorized"})
                return
            call = _Call(obj.get("cmd"), obj.get("args") or {})
            self._dispatch.emit(call)             # -> GUI thread
            if not call.event.wait(30):
                control.send_json(conn, {"ok": False, "error": "timeout"})
                return
            control.send_json(conn, call.result)
        except Exception as e:  # noqa: BLE001
            try:
                control.send_json(conn, {"ok": False, "error": str(e)})
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _on_dispatch(self, call):   # runs on the GUI thread
        try:
            result = self._handler(call.cmd, call.args)
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
            result.setdefault("ok", True)
            call.result = result
        except Exception as e:  # noqa: BLE001
            call.result = {"ok": False, "error": str(e)}
        call.event.set()

    def stop(self):
        self._running = False
        try:
            self._server.close()
        except Exception:
            pass
        control.remove_control_file()
