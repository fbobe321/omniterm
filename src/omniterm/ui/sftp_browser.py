from PyQt6.QtWidgets import QDockWidget, QTreeView, QMenu, QFileDialog, QAbstractItemView
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction, QDrag
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QUrl
import os
import stat
import posixpath
import tempfile

# Custom item data roles
PATH_ROLE = 32      # full remote path for the entry
ISDIR_ROLE = 33     # bool: True if the entry is a directory
PARENT_ROLE = 34    # bool: True for the ".." navigation entry


class SFTPTreeView(QTreeView):
    """Tree view that supports dragging remote files out to the OS file manager
    and dropping local files in from it."""

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.setRootIsDecorated(False)  # flat directory listing, not a lazy tree
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        # Drops are delivered to the viewport for item views; it must accept them too.
        self.viewport().setAcceptDrops(True)

    # --- Drag OUT (remote -> OS file manager): download to temp, hand over local URLs ---
    def startDrag(self, supportedActions):
        if self.browser.sftp is None:
            return

        remote_files = []
        seen = set()
        for index in self.selectedIndexes():
            if index.column() != 0:
                continue
            item = self.browser.model.itemFromIndex(index)
            if item is None or item.data(ISDIR_ROLE):
                continue  # only files can be dragged out (directories skipped)
            path = item.data(PATH_ROLE)
            if path and path not in seen:
                seen.add(path)
                remote_files.append(path)

        if not remote_files:
            return

        if not self.browser._ensure_connected():
            return

        temp_dir = tempfile.mkdtemp(prefix="omniterm_sftp_")
        local_urls = []
        for remote_path in remote_files:
            local_path = os.path.join(temp_dir, posixpath.basename(remote_path))
            try:
                self.browser.sftp.get(remote_path, local_path)
                local_urls.append(QUrl.fromLocalFile(local_path))
            except Exception as e:
                self.browser.error_occurred.emit(f"Download Error: {e}")

        if not local_urls:
            return

        mime = QMimeData()
        mime.setUrls(local_urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    # --- Drag IN (OS file manager -> remote): upload dropped files ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.source() is not self:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and event.source() is not self:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if not mime.hasUrls():
            super().dropEvent(event)
            return

        # Ignore drops originating from within this view (those are drag-outs)
        if event.source() is self:
            event.ignore()
            return

        local_paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        local_paths = [p for p in local_paths if p]
        if not local_paths:
            return

        # Determine the target directory: a folder under the cursor, else the current dir
        target_dir = self.browser.current_path
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            item = self.browser.model.itemFromIndex(index)
            if item is not None and item.data(ISDIR_ROLE):
                target_dir = item.data(PATH_ROLE)

        event.acceptProposedAction()
        self.browser.upload_local_paths(local_paths, target_dir)


class SFTPBrowser(QDockWidget):
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Remote Files", parent)
        self.tree_view = SFTPTreeView(self)
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

    def selected_file_paths(self):
        """Remote paths of every selected entry that is a regular file."""
        paths = []
        seen = set()
        for index in self.tree_view.selectionModel().selectedRows():
            item = self.model.itemFromIndex(index)
            if item is None or item.data(ISDIR_ROLE):
                continue
            path = item.data(PATH_ROLE)
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    def show_context_menu(self, position):
        index = self.tree_view.indexAt(position)
        menu = QMenu()

        selected_files = self.selected_file_paths()

        if index.isValid():
            item = self.model.itemFromIndex(index)
            is_dir = bool(item.data(ISDIR_ROLE))
            if is_dir and not item.data(PARENT_ROLE):
                open_action = QAction("Open", self)
                open_action.triggered.connect(lambda: self.list_directory(item.data(PATH_ROLE)))
                menu.addAction(open_action)

        if len(selected_files) > 1:
            download_action = QAction(f"Download {len(selected_files)} Files...", self)
            download_action.triggered.connect(lambda: self.download_files(selected_files))
            menu.addAction(download_action)
        elif len(selected_files) == 1:
            download_action = QAction("Download...", self)
            download_action.triggered.connect(lambda: self.download_path(selected_files[0]))
            menu.addAction(download_action)

        # Upload always targets the current directory (supports multiple files)
        upload_action = QAction("Upload Files Here...", self)
        upload_action.triggered.connect(self.upload_to_current)
        menu.addAction(upload_action)

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh)
        menu.addAction(refresh_action)

        menu.exec(self.tree_view.mapToGlobal(position))

    def download_files(self, remote_paths):
        """Download several files at once into a chosen local directory."""
        if not self._ensure_connected():
            return
        if not remote_paths:
            return
        target_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not target_dir:
            return
        errors = 0
        for remote_path in remote_paths:
            local_path = os.path.join(target_dir, posixpath.basename(remote_path))
            try:
                self.sftp.get(remote_path, local_path)
            except Exception as e:
                errors += 1
                self.error_occurred.emit(f"Download Error ({posixpath.basename(remote_path)}): {e}")
        if errors == 0:
            self.error_occurred.emit(f"Downloaded {len(remote_paths)} file(s) to {target_dir}")

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
        local_paths, _ = QFileDialog.getOpenFileNames(self, "Select File(s) to Upload")
        if local_paths:
            self.upload_local_paths(local_paths, self.current_path)

    def upload_local_paths(self, local_paths, remote_dir):
        """Upload one or more local files/folders into remote_dir, recursing into
        directories. Refreshes the view if the upload landed in the current dir."""
        if not self._ensure_connected():
            return
        for local_path in local_paths:
            try:
                self._upload_recursive(local_path, remote_dir)
            except Exception as e:
                self.error_occurred.emit(f"Upload Error: {e}")
        if remote_dir == self.current_path:
            self.refresh()

    def _upload_recursive(self, local_path, remote_dir):
        name = os.path.basename(local_path.rstrip("/\\")) or local_path
        remote_path = posixpath.join(remote_dir, name)
        if os.path.isdir(local_path):
            try:
                self.sftp.mkdir(remote_path)
            except Exception:
                pass  # directory may already exist
            for entry in sorted(os.listdir(local_path)):
                self._upload_recursive(os.path.join(local_path, entry), remote_path)
        else:
            self.sftp.put(local_path, remote_path)
