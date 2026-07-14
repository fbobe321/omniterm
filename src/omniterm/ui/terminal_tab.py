from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel, QPushButton
from PyQt6.QtCore import pyqtSignal, Qt
from omniterm.core.serial_client import SerialWorker
from omniterm.core.config import log_terminal_io
from omniterm.ui.native_terminal import NativeTerminal


class SplitContainer(QWidget):
    """Holds 1, 2, or 4 terminal panes in resizable splitters. `terminals`
    lists the TerminalTab widgets so the window can manage/close them."""

    def __init__(self, count, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.terminals = []

        if count <= 1:
            self.root = QSplitter(orientation)
            self._targets = [self.root]
        elif count == 2:
            # Horizontal orientation = side by side; Vertical = stacked
            self.root = QSplitter(orientation)
            self._targets = [self.root, self.root]
        else:  # 4 -> 2x2
            self.root = QSplitter(Qt.Orientation.Vertical)
            top = QSplitter(Qt.Orientation.Horizontal)
            bottom = QSplitter(Qt.Orientation.Horizontal)
            self.root.addWidget(top)
            self.root.addWidget(bottom)
            self._targets = [top, top, bottom, bottom]

        outer.addWidget(self.root)

    def add_terminal(self, widget):
        idx = len(self.terminals)
        if idx < len(self._targets):
            self._targets[idx].addWidget(widget)
            self.terminals.append(widget)


class TerminalTab(QWidget):
    reconnect_requested = pyqtSignal()
    close_requested = pyqtSignal()
    activity = pyqtSignal()  # emitted when output arrives (for tab activity dot)

    # windows_mode / renderer are accepted for call-site compatibility but the
    # native terminal doesn't need them.
    def __init__(self, session_name, parent=None, windows_mode=False, renderer="dom"):
        super().__init__(parent)
        self.session_name = session_name
        self.worker = None

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # Disconnect banner (hidden until the connection drops)
        self.disconnect_bar = QWidget()
        self.disconnect_bar.setStyleSheet("background-color: #5a1d1d; color: #fff;")
        bar_layout = QHBoxLayout(self.disconnect_bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)
        self.disconnect_label = QLabel("Connection closed.")
        btn_reconnect = QPushButton("Reconnect")
        btn_close = QPushButton("Close Tab")
        btn_reconnect.clicked.connect(self._on_reconnect_clicked)
        btn_close.clicked.connect(lambda: self.close_requested.emit())
        bar_layout.addWidget(self.disconnect_label)
        bar_layout.addStretch(1)
        bar_layout.addWidget(btn_reconnect)
        bar_layout.addWidget(btn_close)
        self.disconnect_bar.hide()
        self.layout.addWidget(self.disconnect_bar)

        self.terminal = NativeTerminal()
        self.terminal.send_input.connect(self._on_send)
        self.terminal.resized.connect(self._on_resize)
        self.layout.addWidget(self.terminal)

    def _on_send(self, text):
        log_terminal_io("TX", text)
        if self.worker and hasattr(self.worker, "send_data"):
            self.worker.send_data(text)

    def _on_resize(self, cols, rows):
        if self.worker and hasattr(self.worker, "resize"):
            self.worker.resize(cols, rows)

    def _on_output(self, data):
        log_terminal_io("RX", data)
        self.terminal.feed(data)
        self.activity.emit()

    def apply_settings(self, settings):
        self.terminal.apply_appearance(
            settings.get("fontFamily"), settings.get("fontSize"),
            settings.get("foreground"), settings.get("background"))

    def set_worker(self, worker):
        self.worker = worker
        self.disconnect_bar.hide()
        worker.data_received.connect(self._on_output)
        worker.error_occurred.connect(self.handle_error)
        if hasattr(worker, "disconnected"):
            worker.disconnected.connect(self.on_disconnected)
        if hasattr(worker, "resize"):
            worker.resize(self.terminal._cols, self.terminal._rows)

    def detach_worker(self):
        """Disconnect the current worker from this view (for split/unsplit
        transplants) without stopping it."""
        w = self.worker
        if w is None:
            return
        for sig, slot in ((w.data_received, self._on_output),
                          (w.error_occurred, self.handle_error)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        try:
            w.disconnected.disconnect(self.on_disconnected)
        except (TypeError, RuntimeError, AttributeError):
            pass

    def _write(self, text):
        self.terminal.feed(text)

    def handle_error(self, error_msg):
        self._write(f"\r\n\x1b[31m[ERROR]: {error_msg}\x1b[0m\r\n")

    def on_disconnected(self, message):
        self._write(f"\r\n\x1b[33m[{message} Use Reconnect or Close Tab.]\x1b[0m\r\n")
        self.disconnect_label.setText(message)
        self.disconnect_bar.show()

    def _on_reconnect_clicked(self):
        self.disconnect_bar.hide()
        self.reconnect_requested.emit()

    def hide_disconnect_bar(self):
        self.disconnect_bar.hide()
