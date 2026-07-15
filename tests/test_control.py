"""Tests for the GUI control socket (Phase 2) and its Qt-free transport."""
import json
import os
import socket
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from omniterm.core import control


@pytest.fixture(autouse=True)
def temp_ctl_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNITERM_CTL_FILE", str(tmp_path / "ctl.json"))


# --------------------------------------------------------------------------- #
# Transport (Qt-free)
# --------------------------------------------------------------------------- #
def test_control_file_roundtrip():
    control.write_control_file(12345, "tok")
    info = control.read_control_file()
    assert info == {"port": 12345, "token": "tok"}
    control.remove_control_file()
    assert control.read_control_file() is None


def test_send_command_no_server_raises():
    control.remove_control_file()
    with pytest.raises(control.ControlError):
        control.send_command("ping")


def test_recv_line_and_send_json_over_socketpair():
    a, b = socket.socketpair()
    control.send_json(a, {"hello": "world"})
    assert json.loads(control.recv_line(b)) == {"hello": "world"}
    a.close()
    b.close()


def test_send_command_against_fake_server():
    """A minimal echo server that speaks the protocol; checks the token flows."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    control.write_control_file(port, "SECRET")
    seen = {}

    def serve():
        conn, _ = srv.accept()
        req = json.loads(control.recv_line(conn))
        seen.update(req)
        control.send_json(conn, {"ok": True, "echo": req.get("cmd")})
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    resp = control.send_command("ping", {"x": 1})
    t.join(2)
    assert seen["token"] == "SECRET" and seen["cmd"] == "ping" and seen["args"] == {"x": 1}
    assert resp == {"ok": True, "echo": "ping"}
    srv.close()


# --------------------------------------------------------------------------- #
# ControlServer end to end (real socket + queued dispatch to the Qt thread)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_control_server_dispatches_on_gui_thread(qapp):
    from PyQt6.QtCore import QThread
    from omniterm.core.control_server import ControlServer

    gui_thread = QThread.currentThread()
    calls = []

    def handler(cmd, args):
        # must run on the GUI thread (same as where the server was created)
        assert QThread.currentThread() is gui_thread
        calls.append((cmd, args))
        if cmd == "echo":
            return {"got": args.get("v")}
        if cmd == "boom":
            raise RuntimeError("kaboom")
        return {"ok": True}

    server = ControlServer(handler)
    try:
        assert control.read_control_file()["port"] == server.port

        # Run the blocking client call on a worker thread while we pump Qt events.
        result = {}

        def client():
            result["ok"] = control.send_command("echo", {"v": 42})
            result["err"] = control.send_command("boom")

        ct = threading.Thread(target=client, daemon=True)
        ct.start()
        deadline = time.time() + 5
        while ct.is_alive() and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        ct.join(1)

        assert result["ok"] == {"ok": True, "got": 42}
        assert result["err"]["ok"] is False and "kaboom" in result["err"]["error"]
        assert ("echo", {"v": 42}) in calls
    finally:
        server.stop()
    assert control.read_control_file() is None       # cleaned up on stop


def test_control_server_rejects_bad_token(qapp):
    from omniterm.core.control_server import ControlServer
    server = ControlServer(lambda c, a: {"ok": True})
    try:
        info = control.read_control_file()
        sock = socket.create_connection(("127.0.0.1", info["port"]), timeout=3)
        control.send_json(sock, {"token": "WRONG", "cmd": "ping", "args": {}})
        resp = json.loads(control.recv_line(sock))
        sock.close()
        assert resp["ok"] is False and "unauth" in resp["error"].lower()
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# MainWindow command verbs (dispatch runs directly on the main thread)
# --------------------------------------------------------------------------- #
def test_mainwindow_control_verbs(qapp, monkeypatch):
    monkeypatch.setenv("OMNITERM_NO_CONTROL", "1")   # don't start a real server
    from omniterm.ui.main_window import MainWindow
    win = MainWindow()
    h = win.handle_control_command
    try:
        assert h("ping", {})["app"] == "omniterm"
        assert h("nonsense", {})["ok"] is False
        assert h("list-tabs", {})["tabs"] == []

        opened = h("open", {"type": "local"})
        idx = opened["index"]
        assert idx == 0
        tabs = h("list-tabs", {})["tabs"]
        assert len(tabs) == 1 and tabs[0]["index"] == 0

        cap = h("capture", {"tab": idx})
        assert isinstance(cap["text"], str)   # server adds ok=True on the wire

        assert h("focus-tab", {"tab": idx})["focused"] == 0
        assert h("send-keys", {"tab": idx, "text": "echo hi", "enter": True})["sent"] == 8

        assert h("open", {"type": "ssh", "session": "nope"})["ok"] is False
        with pytest.raises(Exception):     # bad index -> raises; server wraps it
            h("capture", {"tab": 99})
        h("close-tab", {"tab": idx})
        assert h("list-tabs", {})["tabs"] == []
    finally:
        win.close()


def test_mainwindow_split_control(qapp, monkeypatch):
    monkeypatch.setenv("OMNITERM_NO_CONTROL", "1")
    from omniterm.ui.main_window import MainWindow
    win = MainWindow()
    h = win.handle_control_command
    try:
        h("open", {"type": "local"})
        h("open", {"type": "local"})
        assert len(h("list-tabs", {})["tabs"]) == 2

        # needs >= 2 distinct tabs
        assert h("split", {"tabs": [0]})["ok"] is False

        res = h("split", {"tabs": [0, 1], "orientation": "horizontal"})
        assert res["panes"] == 2
        sidx = res["index"]
        tabs = h("list-tabs", {})["tabs"]
        assert len(tabs) == 1 and tabs[0]["type"] == "split" and tabs[0]["panes"] == 2

        # per-pane targeting
        assert isinstance(h("capture", {"tab": sidx, "pane": 1})["text"], str)
        assert h("send-keys", {"tab": sidx, "pane": 1, "text": "x"})["sent"] == 1
        with pytest.raises(Exception):
            h("capture", {"tab": sidx, "pane": 5})     # no such pane

        # splitting an already-split tab is rejected
        assert h("split", {"tabs": [sidx, sidx]})["ok"] is False

        un = h("unsplit", {"tab": sidx})
        assert un["panes"] == 2
        assert len(h("list-tabs", {})["tabs"]) == 2    # back to two tabs
        assert h("unsplit", {"tab": 0})["ok"] is False  # not a split anymore
    finally:
        win.close()
