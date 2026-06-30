from PyQt6.QtWidgets import QDockWidget, QTreeView, QMenu, QFileDialog
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction
from PyQt6.QtCore import Qt, pyqtSignal
import os
import stat
import posixpath

# Custom item data roles
PATH_ROLE = 32      # full remote path for the entry
ISDIR_ROLE = 33     # bool: True if the entry is a directory
PARENT_ROLE = 34    # bool: True for the ".." navigation entry


class SFTPBrowser(QDockWidget):
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Remote Files", parent)
        self.tree_view = QTreeView()
        self.tree_view.setRootIsDecorated(False)  # flat directory listing, not a lazy tree
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Remote Path"])
        self.tree_view.setModel(self.model)
        self.tree_view.doubleClicked.connect(self.on_item_double_clicked)
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self.show_context_menu)
        self.setWidget(self.tree_view)
        self.sftp = None
        self.current_path = "."

    def _ensure_connected(self):
        if self.sftp is None:
            self.error_occurred.emit("Not connected to any session")
            return False
        try:
            # Simple heartbeat check: try to list the current directory
            self.sftp.listdir(self.current_path)
            return True
        except Exception:
            self.sftp = None  # Mark as disconnected
            self.model.clear()
            self.error_occurred.emit("SFTP session lost. Please reconnect.")
            return False

    def connect_sftp(self, ssh_worker):
        try:
            if hasattr(ssh_worker, 'client'):
                self.sftp = ssh_worker.client.open_sftp()
                # Resolve the home directory to an absolute path so navigation is stable
                try:
                    self.current_path = self.sftp.normalize('.')
                except Exception:
                    self.current_path = '.'
                self.list_directory(self.current_path)
            else:
                self.error_occurred.emit("SSHWorker client not found")
        except Exception as e:
            self.error_occurred.emit(f"SFTP Connection Error: {e}")

    def refresh(self):
        """Reload the listing for the current directory."""
        self.list_directory(self.current_path)

    def list_directory(self, path):
        """Replace the view with the contents of `path` (a single directory level)."""
        if not self._ensure_connected():
            return
        try:
            entries = self.sftp.listdir_attr(path)
        except Exception as e:
            self.error_occurred.emit(f"SFTP List Error: {e}")
            return

        self.current_path = path
        self.model.clear()
        self.model.setHorizontalHeaderLabels([f"Remote: {path}"])

        # Parent ("..") entry, unless we are at the filesystem root
        if path not in ("/", ""):
            up_item = QStandardItem("📁 ..")
            up_item.setEditable(False)
            up_item.setData(posixpath.normpath(posixpath.join(path, "..")), PATH_ROLE)
            up_item.setData(True, ISDIR_ROLE)
            up_item.setData(True, PARENT_ROLE)
            self.model.appendRow(up_item)

        # Directories first, then files, each alphabetical
        def sort_key(attr):
            is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode is not None else False
            return (0 if is_dir else 1, attr.filename.lower())

        for attr in sorted(entries, key=sort_key):
            name = attr.filename
            is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode is not None else False
            full_path = posixpath.join(path, name)

            label = f"📁 {name}" if is_dir else f"   {name}"
            item = QStandardItem(label)
            item.setEditable(False)
            item.setData(full_path, PATH_ROLE)
            item.setData(is_dir, ISDIR_ROLE)
            item.setData(False, PARENT_ROLE)
            self.model.appendRow(item)

    def on_item_double_clicked(self, index):
        item = self.model.itemFromIndex(index)
        if item is None:
            return
        path = item.data(PATH_ROLE)
        if not path:
            return
        if item.data(ISDIR_ROLE):
            # Navigate into the directory (or up, for "..")
            self.list_directory(path)
        else:
            # Double-clicking a file downloads it
            self.download_path(path)

    def show_context_menu(self, position):
        index = self.tree_view.indexAt(position)
        menu = QMenu()

        if index.isValid():
            item = self.model.itemFromIndex(index)
            is_dir = bool(item.data(ISDIR_ROLE))
            if is_dir and not item.data(PARENT_ROLE):
                open_action = QAction("Open", self)
                open_action.triggered.connect(lambda: self.list_directory(item.data(PATH_ROLE)))
                menu.addAction(open_action)
            elif not is_dir:
                download_action = QAction("Download", self)
                download_action.triggered.connect(lambda: self.download_path(item.data(PATH_ROLE)))
                menu.addAction(download_action)

        # Upload always targets the current directory
        upload_action = QAction("Upload Here", self)
        upload_action.triggered.connect(self.upload_to_current)
        menu.addAction(upload_action)

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh)
        menu.addAction(refresh_action)

        menu.exec(self.tree_view.mapToGlobal(position))

    def download_path(self, remote_path):
        if not self._ensure_connected():
            return
        if not remote_path:
            return
        local_path, _ = QFileDialog.getSaveFileName(self, "Save File", os.path.basename(remote_path))
        if local_path:
            try:
                self.sftp.get(remote_path, local_path)
            except Exception as e:
                self.error_occurred.emit(f"Download Error: {e}")

    def upload_to_current(self):
        if not self._ensure_connected():
            return
        local_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if local_path:
            try:
                filename = os.path.basename(local_path)
                remote_path = posixpath.join(self.current_path, filename)
                self.sftp.put(local_path, remote_path)
                self.refresh()  # show the new file
            except Exception as e:
                self.error_occurred.emit(f"Upload Error: {e}")
