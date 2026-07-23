"""Process-wide registry of OmniTerm's background QThreads.

Qt kills the whole process - qFatal(), which on Windows looks like the app
vanishing - the instant a QThread *object* is destroyed while its run() is still
executing:

    QThread: Destroyed while thread '' is still running

Every previous fix for this chased the threads we knew about (terminal workers,
SFTP listers, the transfer worker) and joined them one list at a time. Any
thread that was missed - or whose last Python reference was dropped somewhere
unexpected - still aborted the app. This module removes the guesswork:

  * Every worker registers itself here in __init__, so the registry holds a
    strong reference for the thread's entire life. No dropped reference, garbage
    collection cycle or widget teardown can free a QThread while it is running.
  * Entries are only released by prune()/stop_all(), which run on the GUI thread
    and only let go of a thread once isFinished() is true - the one moment when
    destroying it is safe.
  * stop_all() is a catch-all for shutdown: it stops, joins and (last resort)
    terminates everything still alive, including threads no other part of the
    app remembers.

Threads also get an objectName here, so if the abort ever happens again the Qt
message names the culprit instead of printing an empty ''.
"""
import threading

_lock = threading.Lock()
_threads = set()


def register(thread, name=None):
    """Keep `thread` alive for as long as it runs. Called from a worker's
    __init__; returns the thread for convenience."""
    if name:
        try:
            thread.setObjectName(name)
        except Exception:
            pass
    with _lock:
        _threads.add(thread)
    return thread


def prune():
    """Forget threads that have fully finished (safe to destroy now).

    Call from the GUI thread only: dropping the last reference here is what
    eventually destroys the QThread, and that must not happen inside the
    thread's own finished() emission, where Qt still considers it running."""
    with _lock:
        done = [t for t in _threads if _is_done(t)]
        for t in done:
            _threads.discard(t)
    del done  # release the last references here, on the caller's thread


def active():
    """Threads still running (diagnostics/tests)."""
    with _lock:
        return [t for t in _threads if _is_running(t)]


def stop_all(graceful_ms=2000, terminate_ms=1000):
    """Stop and join every registered thread. Last-resort terminate() for any
    straggler, so shutdown can never be blocked - or aborted - by a worker stuck
    in a blocking read (e.g. a Windows ConPTY read inside pywinpty)."""
    with _lock:
        threads = list(_threads)
    for t in threads:
        stopper = getattr(t, "stop", None) or getattr(t, "cancel", None)
        if stopper is not None:
            try:
                stopper()
            except Exception:
                pass
    for t in threads:
        try:
            t.wait(graceful_ms)
        except Exception:
            pass
    for t in threads:
        try:
            if _is_running(t):
                t.terminate()
                t.wait(terminate_ms)
        except Exception:
            pass
    del threads
    prune()


def _is_running(t):
    try:
        return bool(t.isRunning())
    except RuntimeError:      # C++ object already gone
        return False


def _is_done(t):
    try:
        return bool(t.isFinished()) and not bool(t.isRunning())
    except RuntimeError:
        return True
