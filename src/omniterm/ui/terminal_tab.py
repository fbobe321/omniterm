from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel, QPushButton
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl, Qt
import os
import json
from omniterm.core.serial_client import SerialWorker


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

class PyBridge(QObject):
    onDataReceived = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

    @pyqtSlot(str)
    def sendData(self, data):
        if self.worker and hasattr(self.worker, 'send_data'):
            self.worker.send_data(data)

    @pyqtSlot(int, int)
    def resize(self, cols, rows):
        if self.worker and hasattr(self.worker, 'resize'):
            self.worker.resize(cols, rows)

    @pyqtSlot(str)
    def copyToClipboard(self, text):
        if not text:
            return
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

class TerminalTab(QWidget):
    reconnect_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, session_name, parent=None):
        super().__init__(parent)
        self.session_name = session_name
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # Disconnect banner (hidden until the connection drops)
        self.disconnect_bar = QWidget()
        self.disconnect_bar.setStyleSheet(
            "background-color: #5a1d1d; color: #fff;")
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

        self.web_view = QWebEngineView()
        self.layout.addWidget(self.web_view)

        # Setup WebChannel
        self.channel = QWebChannel()
        self.bridge = PyBridge()
        self.channel.registerObject("pybridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # Appearance settings, applied once the page has loaded
        self._settings = None
        self._page_loaded = False
        self.web_view.loadFinished.connect(self._on_load_finished)

        # Load local index.html
        file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "xterm", "index.html"))
        self._index_url = QUrl.fromLocalFile(file_path)
        self.web_view.setUrl(self._index_url)

        # Worker management
        self.worker = None

    def _on_load_finished(self, ok):
        self._page_loaded = bool(ok)
        if self._page_loaded and self._settings is not None:
            self._run_apply(self._settings)

    def apply_settings(self, settings):
        """Apply terminal appearance (font size / family / colors). Stored and
        re-applied automatically once the web page finishes loading."""
        self._settings = settings
        if self._page_loaded:
            self._run_apply(settings)

    def _run_apply(self, settings):
        payload = json.dumps(settings)
        self.web_view.page().runJavaScript(
            f"if (window.applyTerminalSettings) {{ window.applyTerminalSettings({payload}); }}"
        )

    def set_worker(self, worker):
        self.worker = worker
        self.bridge.worker = worker
        self.disconnect_bar.hide()
        worker.data_received.connect(self.bridge.onDataReceived)
        worker.error_occurred.connect(self.handle_error)
        if hasattr(worker, "disconnected"):
            worker.disconnected.connect(self.on_disconnected)

    def handle_error(self, error_msg):
        # Ensure the error is visible in the terminal
        self.bridge.onDataReceived.emit(f"\r\n\x1b[31m[ERROR]: {error_msg}\x1b[0m\r\n")

    def on_disconnected(self, message):
        self.bridge.onDataReceived.emit(
            f"\r\n\x1b[33m[{message} Use Reconnect or Close Tab.]\x1b[0m\r\n")
        self.disconnect_label.setText(message)
        self.disconnect_bar.show()

    def _on_reconnect_clicked(self):
        self.disconnect_bar.hide()
        self.reconnect_requested.emit()

    def hide_disconnect_bar(self):
        self.disconnect_bar.hide()


