"""Tests for progress-tracked transfers and double-click-to-edit sync."""
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication
from omniterm.core.transfer import TransferWorker
from omniterm.ui import sftp_browser as sb


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _pump_until(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.01)


def test_local_adapter_copy_reports_progress(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes(b"x" * 1000)
    dst = tmp_path / "b.txt"
    seen = []
    sb.LocalFSAdapter().get(str(src), str(dst), callback=lambda d, t: seen.append((d, t)))
    assert dst.read_bytes() == b"x" * 1000
    assert seen[-1] == (1000, 1000)     # final callback is 100%


def test_transfer_worker_copies_and_finishes(qapp, tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"y" * 5000)
    (tmp_path / "out").mkdir()
    dst = tmp_path / "out" / "a.bin"
    jobs = [{"kind": "download", "src": str(src), "dst": str(dst),
             "size": 5000, "name": "a.bin"}]
    prog, done = [], {}
    w = TransferWorker(jobs, transport=None, local_adapter=sb.LocalFSAdapter())
    w.progress.connect(lambda d, t, n: prog.append((d, t, n)))
    w.finished_all.connect(lambda ok, errs: done.update(ok=ok, errs=errs))
    w.start()
    _pump_until(qapp, lambda: "ok" in done)
    w.wait(2000)
    assert done["ok"] == 1 and done["errs"] == []
    assert dst.read_bytes() == b"y" * 5000
    assert prog and prog[-1][0] == 5000 and prog[-1][1] == 5000


def test_transfer_worker_reports_errors(qapp, tmp_path):
    jobs = [{"kind": "download", "src": str(tmp_path / "nope"),
             "dst": str(tmp_path / "x"), "size": 0, "name": "nope"}]
    done = {}
    w = TransferWorker(jobs, local_adapter=sb.LocalFSAdapter())
    w.finished_all.connect(lambda ok, errs: done.update(ok=ok, errs=errs))
    w.start()
    _pump_until(qapp, lambda: "ok" in done)
    w.wait(2000)
    assert done["ok"] == 0 and done["errs"]


def test_edit_change_declined_advances_baseline(qapp, tmp_path, monkeypatch):
    br = sb.SFTPBrowser()
    br.sftp = sb.LocalFSAdapter()
    lp = tmp_path / "edit.txt"
    lp.write_text("v1")
    br._edits[str(lp)] = {"remote": "/r/edit.txt", "mtime": os.path.getmtime(lp)}
    time.sleep(0.02)
    lp.write_text("v2 changed")
    monkeypatch.setattr(sb.QMessageBox, "question",
                        lambda *a, **k: sb.QMessageBox.StandardButton.No)
    uploads = []
    monkeypatch.setattr(br, "_run_transfer",
                        lambda jobs, title, on_success=None: uploads.append(jobs))
    br._handle_edit_change(str(lp))
    assert uploads == []                                   # declined -> no upload
    assert br._edits[str(lp)]["mtime"] == os.path.getmtime(lp)  # won't re-ask same change


def test_edit_change_accepted_uploads_to_remote(qapp, tmp_path, monkeypatch):
    br = sb.SFTPBrowser()
    br.sftp = sb.LocalFSAdapter()
    lp = tmp_path / "e.txt"
    lp.write_text("v1")
    br._edits[str(lp)] = {"remote": "/r/e.txt", "mtime": os.path.getmtime(lp)}
    time.sleep(0.02)
    lp.write_text("v2")
    monkeypatch.setattr(sb.QMessageBox, "question",
                        lambda *a, **k: sb.QMessageBox.StandardButton.Yes)
    calls = []
    monkeypatch.setattr(br, "_run_transfer",
                        lambda jobs, title, on_success=None: calls.append((jobs, title)))
    br._handle_edit_change(str(lp))
    assert calls
    job = calls[0][0][0]
    assert job["kind"] == "upload" and job["dst"] == "/r/e.txt" and job["src"] == str(lp)


def test_unchanged_file_does_not_prompt(qapp, tmp_path, monkeypatch):
    br = sb.SFTPBrowser()
    br.sftp = sb.LocalFSAdapter()
    lp = tmp_path / "same.txt"
    lp.write_text("v1")
    br._edits[str(lp)] = {"remote": "/r/same.txt", "mtime": os.path.getmtime(lp)}
    asked = []
    monkeypatch.setattr(sb.QMessageBox, "question",
                        lambda *a, **k: asked.append(1) or sb.QMessageBox.StandardButton.No)
    br._handle_edit_change(str(lp))          # mtime unchanged
    assert asked == []                        # no prompt when nothing changed
