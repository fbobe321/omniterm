from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl
import os
from omniterm.core.serial_client import SerialWorker

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

        # Load local index.html
        file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "xterm", "index.html"))
        self.web_view.setUrl(QUrl.fromLocalFile(file_path))

        # Worker management
        self.worker = None

    def set_worker(self, worker):
        self.worker = worker
        self.bridge.worker = worker
        worker.data_received.connect(self.bridge.onDataReceived)
        worker.error_occurred.connect(self.handle_error)

    def handle_error(self, error_msg):
        # Ensure the error is visible in the terminal
        self.bridge.onDataReceived.emit(f"\r\n\x1b[31m[ERROR]: {error_msg}\x1b[0m\r\n")

