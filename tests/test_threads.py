"""Tests for the process-wide worker-thread registry (core/threads.py).

These guard the crash that has killed OmniTerm repeatedly on Windows:
Qt aborts the process ("QThread: Destroyed while thread '...' is still
running") whenever a QThread object is destroyed before its run() returns. A
regression here does not fail politely - it takes the test process down with it,
which is exactly the signal we want.
"""
import gc
import os
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtCore import QThread
from omniterm.core import threads as reg


class PoliteWorker(QThread):
    """Stops when asked, like a well-behaved terminal worker."""

    def __init__(self):
        super().__init__()
        self._running = True
        reg.register(self, "polite")

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            time.sleep(0.01)


class StubbornWorker(QThread):
    """Ignores stop() and blocks in a read that releases the GIL - the Windows
    ConPTY case that made previous graceful-join-only fixes insufficient."""

    def __init__(self):
        super().__init__()
        self._r, self._w = os.pipe()
        reg.register(self, "stubborn")

    def stop(self):
        pass  # deliberately useless

    def run(self):
        os.read(self._r, 1)   # nothing is ever written


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    reg.stop_all(graceful_ms=500)


def test_dropping_the_last_reference_cannot_destroy_a_running_thread():
    worker = PoliteWorker()
    worker.start()
    assert worker.isRunning()

    del worker            # what a closed tab / torn-down widget does
    gc.collect()          # ...and what would otherwise abort the process

    running = reg.active()
    assert len(running) == 1
    assert running[0].isRunning()


def test_stop_all_joins_a_cooperative_worker():
    worker = PoliteWorker()
    worker.start()
    reg.stop_all()
    assert not worker.isRunning()
    assert reg.active() == []


def test_stop_all_terminates_a_worker_that_ignores_stop():
    worker = StubbornWorker()
    worker.start()
    while not worker.isRunning():
        time.sleep(0.01)
    reg.stop_all(graceful_ms=300)
    assert not worker.isRunning()
    assert reg.active() == []


def test_prune_releases_only_finished_threads():
    finished, running = PoliteWorker(), PoliteWorker()
    finished.start()
    running.start()
    finished.stop()
    finished.wait(2000)

    before = len(reg.active())
    reg.prune()
    assert before == 1                      # only `running` was still running
    assert reg.active() == [running]
    assert finished.isFinished()            # dropped by prune, still usable here


def test_registered_threads_are_named_for_diagnostics():
    worker = PoliteWorker()
    assert worker.objectName() == "polite"


def test_register_is_thread_safe():
    workers = []
    barrier = threading.Barrier(4)

    def make():
        barrier.wait()
        for _ in range(25):
            workers.append(PoliteWorker())

    ts = [threading.Thread(target=make) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(workers) == 100
