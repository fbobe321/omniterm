from PyQt6.QtWidgets import QDockWidget, QTreeView, QMenu, QFileDialog
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction
from PyQt6.QtCore import Qt, pyqtSignal
import os

class SFTPBrowser(QDockWidget):
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Remote Files", parent)
        self.tree_view = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Remote Path"])
        self.tree_view.setModel(self.model)
        self.tree_view.doubleClicked.connect(self.on_item_double_clicked)
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self.show_context_menu)
        self.setWidget(self.tree_view)
        self.sftp = None

    def _ensure_connected(self):
        if self.sftp is None:
            self.error_occurred.emit("Not connected to any session")
            return False
        try:
            # Simple heartbeat check: try to list the current directory
            self.sftp.listdir('.')
            return True
        except Exception:
            self.sftp = None # Mark as disconnected
            self.model.clear()
            self.error_occurred.emit("SFTP session lost. Please reconnect.")
            return False

    def connect_sftp(self, ssh_worker):
        try:
            if hasattr(ssh_worker, 'client'):
                self.sftp = ssh_worker.client.open_sftp()
                self.refresh_root()
            else:
                self.error_occurred.emit("SSHWorker client not found")
        except Exception as e:
            self.error_occurred.emit(f"SFTP Connection Error: {e}")

    def refresh_root(self):
        if not self._ensure_connected():
            return
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Remote Path"])
        root_item = QStandardItem(".")
        root_item.setData(".", 32)
        self.model.appendRow(root_item)
        self.expand_item(root_item, ".")

    def expand_item(self, parent_item, path):
        if not self._ensure_connected():
            return
        try:
            files = self.sftp.listdir_attr(path)
            for attr in files:
                name = attr.filename
                full_path = os.path.join(path, name).replace('\\', '/')

                item = QStandardItem(name)
                item.setData(full_path, 32)

                parent_item.appendRow(item)
        except Exception as e:
            self.error_occurred.emit(f"SFTP List Error: {e}")

    def on_item_double_clicked(self, index):
        if not self._ensure_connected():
            return
        item = self.model.itemFromIndex(index)
        path = item.data(32)
        if path:
            try:
                self.sftp.listdir(path)
                self.expand_item(item, path)
            except Exception:
                pass

    def show_context_menu(self, position):
        index = self.tree_view.indexAt(position)
        if not index.isValid():
            return

        menu = QMenu()
        download_action = QAction("Download", self)
        upload_action = QAction("Upload to Here", self)

        download_action.triggered.connect(lambda: self.download_file(index))
        upload_action.triggered.connect(lambda: self.upload_file(index))

        menu.addAction(download_action)
        menu.addAction(upload_action)
        menu.exec(self.tree_view.mapToGlobal(position))

    def download_file(self, index):
        if not self._ensure_connected():
            return
        item = self.model.itemFromIndex(index)
        remote_path = item.data(32)
        if not remote_path:
            return

        try:
            # Check if it's a directory
            self.sftp.listdir(remote_path)
            return # Cannot download directory directly with get()
        except Exception:
            pass

        local_path, _ = QFileDialog.getSaveFileName(self, "Save File", os.path.basename(remote_path))
        if local_path:
            try:
                self.sftp.get(remote_path, local_path)
            except Exception as e:
                self.error_occurred.emit(f"Download Error: {e}")

    def upload_file(self, index):
        if not self._ensure_connected():
            return
        item = self.model.itemFromIndex(index)
        remote_dir = item.data(32)
        if not remote_dir:
            return

        try:
            self.sftp.listdir(remote_dir)
        except Exception:
            self.error_occurred.emit("Selected item is not a directory")
            return

        local_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if local_path:
            try:
                filename = os.path.basename(local_path)
                remote_path = os.path.join(remote_dir, filename).replace('\\', '/')
                self.sftp.put(local_path, remote_path)
                # Refresh the directory to show the new file
                self.expand_item(item, remote_dir)
            except Exception as e:
                self.error_occurred.emit(f"Upload Error: {e}")
