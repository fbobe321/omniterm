from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSplitter
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl, Qt
import os
import json
from omniterm.core.serial_client import SerialWorker


class SplitContainer(QWidget):
    """Holds 1, 2, or 4 terminal panes in resizable splitters. `terminals`
    lists the TerminalTab widgets so the window can manage/close them."""

    def __init__(self, count, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.terminals = []

        if count <= 1:
            self.root = QSplitter(Qt.Orientation.Horizontal)
            self._targets = [self.root]
        elif count == 2:
            self.root = QSplitter(Qt.Orientation.Horizontal)
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

class TerminalTab(QWidget):
    def __init__(self, session_name, parent=None):
        super().__init__(parent)
        self.session_name = session_name
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

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
        self.web_view.setUrl(QUrl.fromLocalFile(file_path))

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
        worker.data_received.connect(self.bridge.onDataReceived)
        worker.error_occurred.connect(self.handle_error)

    def handle_error(self, error_msg):
        # Ensure the error is visible in the terminal
        self.bridge.onDataReceived.emit(f"\r\n\x1b[31m[ERROR]: {error_msg}\x1b[0m\r\n")

