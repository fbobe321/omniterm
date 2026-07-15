"""Tests for the shadow command predictor across connection transports.

The prediction logic lives entirely in NativeTerminal and is transport-agnostic;
what differs per connection is the wiring in TerminalTab.set_worker and the
echo timing. These tests cover:

  * unit: sensitivity filter, history ranking, persistence
  * a LOCAL-shaped worker (instant echo, no cwd) end to end
  * a REMOTE/SSH-shaped worker (delayed echo + cwd_changed, and interleaved
    non-echo output) end to end — this is the regression guard for the bug
    where masked-input detection latched on SSH echo latency and killed all
    suggestions on remote sessions
  * a no-echo password prompt (masked) — never suggested/recorded
  * an opt-in real-bash PTY test (skipped where openpty is unavailable)

Run:  QT_QPA_PLATFORM=offscreen python -m pytest tests/test_shadow_predictor.py
"""
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal, Qt, QEvent
from PyQt6.QtGui import QKeyEvent

from omniterm.core.command_history import CommandHistory, is_sensitive_command
from omniterm.core.predictor import HistoryPredictor


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd,sensitive", [
    ("git status", False),
    ("ls -la", False),
    ("mysql -pS3cret db", True),
    ("export API_KEY=abc123", True),
    ("psql --password=foo", True),
    ("curl -H 'Authorization: Bearer xyz'", True),
])
def test_sensitivity_filter(cmd, sensitive):
    assert is_sensitive_command(cmd) is sensitive


def test_history_predictor_recency(tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("git checkout main")
    h.record("git commit -m wip")
    h.record("git checkout feature-x")  # most recent 'git checkout'
    p = HistoryPredictor(h)
    assert p.predict("git ch") == "git checkout feature-x"   # recency wins
    assert p.predict("git co") == "git commit -m wip"


def test_history_predictor_frequency_is_mild(tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    # 'git status' run many times, but a while ago; 'git stash' run once, most
    # recently. Recency must still win (fish-like) — frequency is only a nudge.
    for _ in range(8):
        h.record("git status")
    h.record("git stash")
    p = HistoryPredictor(h)
    assert p.predict("git st") == "git stash"          # recency dominates
    # Among commands at similar recency, the more frequent one wins the tie.
    h2 = CommandHistory(path=str(tmp_path / "h2.jsonl"))
    h2.record("make test")
    h2.record("make build")
    h2.record("make test")
    h2.record("make build")
    h2.record("make test")      # 'make test' both more frequent and most recent
    assert HistoryPredictor(h2).predict("make ") == "make test"


def test_history_predictor_cwd_boost(tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("deploy alpha", cwd="/other")
    h.record("deploy target", cwd="/proj")   # directory-specific command
    h.record("deploy beta", cwd="/other")    # more recent, different directory
    p = HistoryPredictor(h)
    assert p.predict("deploy") == "deploy beta"                  # recency w/o cwd
    assert p.predict("deploy", cwd="/proj") == "deploy target"   # cwd flips it


def test_secret_never_recorded(tmp_path):
    path = str(tmp_path / "h.jsonl")
    h = CommandHistory(path=path)
    h.record("git status")
    h.record("mysql -pSECRET db")     # dropped
    h.record("export TOKEN=abc")      # dropped
    assert [e["cmd"] for e in h.entries()] == ["git status"]
    # persisted across reload, still without the secrets
    assert [e["cmd"] for e in CommandHistory(path=path).entries()] == ["git status"]


# --------------------------------------------------------------------------- #
# Fake workers matching the real worker signal contracts
# --------------------------------------------------------------------------- #
class _LocalWorker(QObject):
    """Local-PTY-shaped: instant echo, no cwd_changed signal."""
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    disconnected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sent = []

    def send_data(self, text):
        self.sent.append(text)
        self.data_received.emit(text)   # echo immediately

    def resize(self, cols, rows):
        pass


class _SSHWorker(_LocalWorker):
    """SSH-shaped: has cwd_changed (OSC 7). Echo is buffered, not instant, and
    callers can inject interleaved non-echo output to mimic real remote timing."""
    cwd_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._pending = []

    def send_data(self, text):
        self.sent.append(text)
        self._pending.append(text)      # do NOT echo yet (network in flight)

    def flush_echo(self):
        for t in self._pending:
            self.data_received.emit(t)
        self._pending.clear()

    def other_output(self, text):
        self.data_received.emit(text)   # non-echo output (e.g. OSC 7, title)


def _make_tab(worker, history, enabled=True, min_prefix=1):
    from omniterm.ui.terminal_tab import TerminalTab
    from omniterm.core.predictor import HistoryPredictor
    tab = TerminalTab("test")
    term = tab.terminal
    term._history = history
    term._predictor = HistoryPredictor(history)
    term._predict_cfg = {"enabled": enabled, "min_prefix": min_prefix}
    tab.set_worker(worker)
    return tab, term


def _type(term, s):
    for ch in s:
        code = ord(ch.upper()) if ch.isalpha() else ord(ch)
        term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code,
                                     Qt.KeyboardModifier.NoModifier, ch))


def _press(term, key, mods=Qt.KeyboardModifier.NoModifier):
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods, ""))


# --------------------------------------------------------------------------- #
# Local transport
# --------------------------------------------------------------------------- #
def test_local_suggest_and_accept(qapp, tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("git checkout feature-x")
    w = _LocalWorker()
    tab, term = _make_tab(w, h)
    w.data_received.emit("user@host:~$ ")   # prompt
    _type(term, "git c")
    assert term._shadow == "heckout feature-x"
    # Ctrl-F accepts the whole suggestion (Tab is left for shell completion)
    w.sent.clear()
    _press(term, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert w.sent == ["heckout feature-x"]


def test_tab_is_left_for_shell_completion(qapp, tmp_path):
    """Tab must NOT be swallowed by the predictor — it's required for filename
    and path completion. With a suggestion showing, Tab still sends \\t."""
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("git checkout feature-x")
    w = _LocalWorker()
    tab, term = _make_tab(w, h)
    w.data_received.emit("user@host:~$ ")
    _type(term, "git c")
    assert term._shadow == "heckout feature-x"   # suggestion is showing
    w.sent.clear()
    _press(term, Qt.Key.Key_Tab)
    assert w.sent == ["\t"]                        # Tab passes through to shell


def test_scroll_stays_pinned_when_output_arrives(qapp):
    """Scrolling up to read history must not be yanked back to the bottom by new
    output — the view stays pinned to the same lines as content flows into
    scrollback."""
    from omniterm.ui.native_terminal import NativeTerminal

    def top_line(t):
        line = t._visible_lines()[0]
        return "".join((line[i].data if i in line and line[i].data else " ")
                       for i in range(t._cols)).rstrip()

    t = NativeTerminal()
    t.resize(640, 384)
    for i in range(100):
        t.feed(f"line{i:03d}\r\n")
    t._scroll = 15
    pinned = top_line(t)
    for i in range(100, 120):          # 20 more lines while scrolled up
        t.feed(f"line{i:03d}\r\n")
    assert top_line(t) == pinned       # same line still shown (not snapped down)
    assert t._scroll == 35             # offset advanced by the 20 new lines


def test_ctrl_wheel_zooms_font(qapp):
    """Ctrl+wheel changes font size (and the grid); plain wheel does not."""
    from omniterm.ui.native_terminal import NativeTerminal
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtCore import QPointF, QPoint

    def wheel(t, dy, ctrl):
        mods = (Qt.KeyboardModifier.ControlModifier if ctrl
                else Qt.KeyboardModifier.NoModifier)
        t.wheelEvent(QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                                 QPoint(0, dy), Qt.MouseButton.NoButton, mods,
                                 Qt.ScrollPhase.NoScrollPhase, False))

    t = NativeTerminal()
    t.resize(800, 480)
    size0, cols0 = t._font.pointSizeF(), t._cols
    wheel(t, 120, ctrl=True)
    assert t._font.pointSizeF() == size0 + 1 and t._cols < cols0   # zoomed in
    wheel(t, -120, ctrl=True)
    assert t._font.pointSizeF() == size0                            # back to start
    # clamps
    for _ in range(60):
        wheel(t, -120, ctrl=True)
    assert t._font.pointSizeF() == t._MIN_FONT
    for _ in range(80):
        wheel(t, 120, ctrl=True)
    assert t._font.pointSizeF() == t._MAX_FONT
    # plain wheel leaves the font alone
    size = t._font.pointSizeF()
    wheel(t, 120, ctrl=False)
    assert t._font.pointSizeF() == size


def test_paste_normalizes_newlines(qapp):
    """A multi-line paste sends carriage returns (what Enter sends), not \\n."""
    from omniterm.ui.native_terminal import NativeTerminal
    from PyQt6.QtGui import QGuiApplication
    t = NativeTerminal()
    sent = []
    t.send_input.connect(lambda s: sent.append(s))
    QGuiApplication.clipboard().setText("line1\nline2\r\nline3")
    t._paste()
    assert sent == ["line1\rline2\rline3"]


def test_paste_is_bracketed_when_app_enables_it(qapp):
    """When the app enables bracketed paste (vim insert, bash/readline), the
    paste is wrapped in ESC[200~ / ESC[201~ so vim skips autoindent — this is
    the fix for large pastes 'staircasing'. When disabled, paste is raw."""
    from omniterm.ui.native_terminal import NativeTerminal
    from PyQt6.QtGui import QGuiApplication
    t = NativeTerminal()
    sent = []
    t.send_input.connect(lambda s: sent.append(s))
    t.feed("\x1b[?2004h")                       # app turns bracketed paste ON
    QGuiApplication.clipboard().setText("def f():\n    return 1\n")
    t._paste()
    assert sent == ["\x1b[200~def f():\r    return 1\r\x1b[201~"]
    t.feed("\x1b[?2004l")                       # app turns it OFF
    sent.clear()
    QGuiApplication.clipboard().setText("x\ny")
    t._paste()
    assert sent == ["x\ry"]


def test_tab_reaches_shell_through_event_system(qapp):
    """Regression: Qt must not steal Tab for focus traversal. Sent through the
    REAL event() path (not keyPressEvent directly), Tab must keep focus on the
    terminal and deliver \\t to the shell; Shift+Tab delivers back-tab."""
    from omniterm.ui.native_terminal import NativeTerminal
    from PyQt6.QtWidgets import QTabWidget, QMainWindow
    win = QMainWindow()
    tabs = QTabWidget()
    win.setCentralWidget(tabs)
    term = NativeTerminal()
    tabs.addTab(term, "one")
    tabs.addTab(NativeTerminal(), "two")
    win.show()
    term.setFocus()
    qapp.processEvents()
    assert term.focusNextPrevChild(True) is False
    sent = []
    term.send_input.connect(lambda s: sent.append(s))
    qapp.sendEvent(term, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                                   Qt.KeyboardModifier.NoModifier, ""))
    assert qapp.focusWidget() is term      # focus did NOT jump to the tab bar
    assert sent == ["\t"]
    sent.clear()
    qapp.sendEvent(term, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backtab,
                                   Qt.KeyboardModifier.ShiftModifier, ""))
    assert sent == ["\x1b[Z"]
    win.close()


def test_end_and_right_also_accept(qapp, tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("npm run build")
    for accept_key in (Qt.Key.Key_End, Qt.Key.Key_Right):
        w = _LocalWorker()
        tab, term = _make_tab(w, h)
        w.data_received.emit("$ ")
        _type(term, "npm ")
        assert term._shadow == "run build"
        w.sent.clear()
        _press(term, accept_key)
        assert w.sent == ["run build"]


# --------------------------------------------------------------------------- #
# Remote / SSH transport  (regression: latency must not disable suggestions)
# --------------------------------------------------------------------------- #
def test_remote_cwd_is_wired(qapp, tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    w = _SSHWorker()
    tab, term = _make_tab(w, h)
    w.cwd_changed.emit("/remote/project")
    assert term._cwd == "/remote/project"


def test_remote_suggest_despite_latency_and_interleaving(qapp, tmp_path):
    """Type over an SSH-shaped link where echo lags and non-echo output arrives
    in between. Before the fix, masked-input detection latched and killed all
    suggestions on remote; now it must still suggest once the echo lands."""
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("git checkout feature-x")
    w = _SSHWorker()
    tab, term = _make_tab(w, h)
    w.data_received.emit("user@host:~$ ")   # prompt
    # user types fast; echoes are still in flight
    _type(term, "git c")
    assert term._typed_count >= 3
    # meanwhile the remote emits non-echo output (e.g. OSC 7) -> a feed with an
    # empty buffer while typed_count is high (this is what used to latch masked)
    w.other_output("\x1b]7;file://host/remote/project\x07")
    # now the echo finally arrives
    w.flush_echo()
    assert term._masked is False
    assert term._shadow == "heckout feature-x"


def test_password_prompt_not_predicted(qapp, tmp_path):
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("supersecretpassword")   # even if in history, must not surface
    w = _LocalWorker()
    # password prompt: echo disabled
    w.send_data = lambda text, _w=w: _w.sent.append(text)  # swallow echo
    tab, term = _make_tab(w, h)
    w.data_received.emit("Password: ")
    _type(term, "supersecret")
    w.data_received.emit("")   # a feed with no echo
    assert term._masked is True
    assert term._shadow == ""


# --------------------------------------------------------------------------- #
# Opt-in real bash PTY
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not hasattr(os, "openpty") or sys.platform.startswith("win"),
                    reason="requires a Unix PTY")
def test_local_real_bash(qapp, tmp_path):
    from omniterm.core.local_pty import LocalPTYWorker
    h = CommandHistory(path=str(tmp_path / "h.jsonl"))
    h.record("git checkout feature-x")
    from omniterm.ui.native_terminal import NativeTerminal
    from omniterm.core.predictor import HistoryPredictor
    term = NativeTerminal()
    term._history = h
    term._predictor = HistoryPredictor(h)
    term._predict_cfg = {"enabled": True, "min_prefix": 2}
    w = LocalPTYWorker(prefer_unix=False)
    w.data_received.connect(term.feed)
    term.send_input.connect(w.send_data)
    w.start()

    def pump(ms):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.005)

    pump(800)
    w.send_data("export PS1='$ '\n"); pump(500)
    w.send_data("clear\n"); pump(400)
    _type(term, "git c"); pump(300)
    try:
        assert term._shadow == "heckout feature-x"
    finally:
        try:
            w.stop()
        except Exception:
            w.terminate()
        w.wait(1500)
