from PyQt6.QtWidgets import QDockWidget, QTreeView, QMenu, QFileDialog, QAbstractItemView, QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLineEdit, QCompleter, QProgressDialog, QMessageBox, QToolButton, QApplication
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction, QDrag, QDesktopServices
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QUrl, QSize, QStringListModel, QFileSystemWatcher, QTimer, QThread, QPersistentModelIndex
import os
import stat
import posixpath
import tempfile
import time
import threading
import shutil
from omniterm.core.config import get_group_folders_first, set_group_folders_first
from omniterm.core.transfer import TransferWorker
from omniterm.ui.icons import get_icon, file_icon


class _LocalAttr:
    """Mimics paramiko's SFTPAttributes for the fields the browser reads."""
    __slots__ = ("filename", "st_mode", "st_size", "st_mtime")

    def __init__(self, filename, st_mode, st_size, st_mtime):
        self.filename = filename
        self.st_mode = st_mode
        self.st_size = st_size
        self.st_mtime = st_mtime


class LocalFSAdapter:
    """Exposes the subset of the paramiko SFTP API the browser uses, backed by
    the local filesystem. Paths use forward slashes so the browser's posixpath
    logic works on Windows too (os.* accepts forward slashes there)."""

    def normalize(self, path):
        return os.path.abspath(os.path.expanduser(path)).replace("\\", "/")

    def listdir(self, path):
        return os.listdir(path)

    def listdir_attr(self, path):
        entries = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        st = entry.stat()
                        entries.append(_LocalAttr(entry.name, st.st_mode, st.st_size, st.st_mtime))
                    except OSError:
                        continue
        except OSError as e:
            raise e
        return entries

    def get(self, remote, local, callback=None):
        self._copy(remote, local, callback)

    def put(self, local, remote, callback=None):
        self._copy(local, remote, callback)

    @staticmethod
    def _copy(src, dst, callback=None):
        size = os.path.getsize(src)
        done = 0
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                chunk = fi.read(262144)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                if callback:
                    callback(done, size)
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
        if callback:
            callback(size, size)

    def stat(self, path):
        st = os.stat(path)
        return _LocalAttr(os.path.basename(path), st.st_mode, st.st_size, st.st_mtime)

    def mkdir(self, path):
        os.mkdir(path)

    def rename(self, oldpath, newpath):
        os.rename(oldpath, newpath)

    def close(self):
        pass

class SFTPLister(QThread):
    """Lists directories off the GUI thread so a slow or dropped connection can
    never freeze the UI. One per connection, serialized (latest request wins).

    For remote sessions it opens its OWN dedicated SFTP channel from the SSH
    transport, so it never touches the browser's GUI-thread SFTP client - no
    locks, no races (mirrors what TransferWorker does). Local sessions use their
    own LocalFSAdapter, which is thread-safe (os.scandir)."""

    listed = pyqtSignal(int, str, object)   # req_id, path, entries (list of attrs)
    failed = pyqtSignal(int, str, str)      # req_id, path, error message

    def __init__(self, transport=None, adapter=None, parent=None):
        super().__init__(parent)
        self._transport = transport         # remote: paramiko Transport
        self._adapter = adapter             # local: a LocalFSAdapter
        self._sftp = adapter                # remote client opened lazily in run()
        self._cv = threading.Condition()
        self._pending = None                # (req_id, path) waiting to be listed
        self._alive = True

    def request(self, req_id, path):
        """Queue a listing; a newer request supersedes any not-yet-started one."""
        with self._cv:
            self._pending = (req_id, path)
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._alive = False
            self._cv.notify()
        # If the thread is blocked in a network call on a dead connection
        # (e.g. after a forcibly-closed socket), notifying the condition
        # variable isn't enough - it never returns to check _alive. Closing
        # its dedicated channel makes the in-flight listdir_attr raise at once,
        # so the thread exits promptly instead of waiting out the 15s timeout.
        if self._transport is not None and self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass

    def _client(self):
        if self._sftp is not None:
            return self._sftp
        import paramiko
        client = paramiko.SFTPClient.from_transport(self._transport)
        try:
            client.get_channel().settimeout(15)
        except Exception:
            pass
        self._sftp = client
        return client

    def run(self):
        while True:
            with self._cv:
                while self._alive and self._pending is None:
                    self._cv.wait()
                if not self._alive:
                    break
                req_id, path = self._pending
                self._pending = None
            try:
                entries = self._client().listdir_attr(path)
                self.listed.emit(req_id, path, entries)
            except Exception as e:
                self.failed.emit(req_id, path, str(e))
        # Close the dedicated remote channel (local adapters have a no-op close).
        if self._transport is not None and self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass


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
        # Renames are started explicitly (slow second click / F2 / context menu),
        # never by Qt's own edit triggers.
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._press_on_current = False
        self._pending_rename = None          # QPersistentModelIndex of the name cell
        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.timeout.connect(self._start_pending_rename)

    # --- Explorer-style rename: a slow second click on the selected name edits it ---
    def mousePressEvent(self, event):
        # Record whether this press landed on the already-current row BEFORE the
        # default handler moves the selection (a first click merely selects).
        index = self.indexAt(event.position().toPoint())
        self._press_on_current = (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                          | Qt.KeyboardModifier.ShiftModifier))
            and index.siblingAtColumn(0) == self.currentIndex().siblingAtColumn(0)
            and self.selectionModel().isSelected(index.siblingAtColumn(0)))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or not self._press_on_current:
            return
        self._press_on_current = False
        index = self.indexAt(event.position().toPoint())
        if (index.isValid() and index.column() == 0
                and index == self.currentIndex().siblingAtColumn(0)):
            self._pending_rename = QPersistentModelIndex(index)
            # Wait out the double-click window so double-click still opens/navigates.
            self._rename_timer.start(QApplication.doubleClickInterval() + 100)

    def mouseDoubleClickEvent(self, event):
        self._cancel_pending_rename()
        self._press_on_current = False
        super().mouseDoubleClickEvent(event)

    def _cancel_pending_rename(self):
        self._rename_timer.stop()
        self._pending_rename = None

    def _start_pending_rename(self):
        pidx = self._pending_rename
        self._pending_rename = None
        if pidx is None or not pidx.isValid():
            return  # the listing was reloaded meanwhile
        index = self.model().index(pidx.row(), 0)
        if index != self.currentIndex().siblingAtColumn(0):
            return  # selection moved on; don't rename something else
        self.browser.begin_rename(index)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            self.browser.begin_rename(self.currentIndex())
            return
        super().keyPressEvent(event)

    def closeEditor(self, editor, hint):
        super().closeEditor(editor, hint)
        self.browser.finish_rename()

    # --- Drag OUT (remote -> OS file manager): download to temp, hand over local URLs ---
    def startDrag(self, supportedActions):
        self._cancel_pending_rename()
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
            item = self.browser.model.itemFromIndex(index.siblingAtColumn(0))
            if item is not None and item.data(ISDIR_ROLE):
                target_dir = item.data(PATH_ROLE)

        event.acceptProposedAction()
        self.browser.upload_local_paths(local_paths, target_dir)


class SFTPBrowser(QDockWidget):
    error_occurred = pyqtSignal(str)
    status_message = pyqtSignal(str)   # informational (success) — not an error

    def __init__(self, parent=None):
        super().__init__("FILES", parent)
        self.tree_view = SFTPTreeView(self)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setIconSize(QSize(18, 18))
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Name", "Size", "Modified"])
        self.tree_view.setModel(self.model)
        self.tree_view.doubleClicked.connect(self.on_item_double_clicked)
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self.show_context_menu)

        # Clickable headers for sorting (sorting is applied manually in list_directory)
        header = self.tree_view.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self.on_header_clicked)

        # Editable current-path bar (type/paste a path + Enter to navigate)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Path — type or paste, then Enter")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self._on_path_entered)

        # Path autocomplete: suggest directory entries as you type
        self._completer_model = QStringListModel()
        self._path_completer = QCompleter(self._completer_model, self)
        self._path_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._path_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.path_edit.setCompleter(self._path_completer)
        self.path_edit.textEdited.connect(self._update_completions)
        self._completer_dir = None  # last directory we listed for completion

        # "Follow terminal folder" checkbox above the tree
        self.follow_check = QCheckBox("Follow terminal folder")
        self.follow_check.setToolTip(
            "Keep this panel in sync with the shell's current directory as you "
            "cd around. Local terminals follow automatically; SSH sessions get a "
            "one-time shell prompt setup (sent invisibly, nothing to clean up).")
        self.follow_check.toggled.connect(self._on_follow_toggled)

        # Refresh button, next to the follow checkbox: reload the current folder.
        self.refresh_btn = QToolButton()
        self.refresh_btn.setText("↻")   # ↻ clockwise arrow
        self.refresh_btn.setToolTip("Refresh the file list")
        self.refresh_btn.setAutoRaise(True)
        self.refresh_btn.clicked.connect(self.refresh)

        controls = QWidget()
        hbox = QHBoxLayout(controls)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(self.follow_check)
        hbox.addStretch(1)
        hbox.addWidget(self.refresh_btn)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)
        vbox.addWidget(self.path_edit)
        vbox.addWidget(controls)
        vbox.addWidget(self.tree_view)
        self.setWidget(container)

        self.sftp = None
        self.current_path = "."
        self.sort_column = 0   # 0=name, 1=size, 2=modified
        self.sort_desc = False
        self.group_folders_first = get_group_folders_first()

        # Per-connection SFTP state, keyed by id(ssh_worker), so each tab keeps
        # its own session and browsing location.
        self._states = {}
        self.active_worker = None
        self._active_state = None
        self._latest_cwd = {}    # id(worker) -> last reported shell cwd
        self._bootstrapped = set()  # workers we've configured for OSC 7
        self._list_req = 0       # monotonic id; only the latest listing is applied

        # Background transfers + double-click-to-edit sync.
        self._transfer = None            # active TransferWorker
        self._progress = None            # active QProgressDialog
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_edited_file_changed)
        self._edits = {}                 # local temp path -> {remote, mtime}
        self._prompting = set()          # local paths with an open overwrite prompt

        # In-place rename (slow second click / F2 / context menu)
        self._rename_item = None         # QStandardItem being edited
        self._rename_old = ""            # its name before editing
        self._rename_after_list = None   # entry name to auto-rename once listed

    def _ensure_connected(self):
        # Non-blocking presence check only. Never probe the network on the GUI
        # thread - a dead connection would hang the whole app. Directory listings
        # go through the async lister; user-initiated transfers surface their own
        # errors (and are bounded by the SFTP channel timeout).
        if self.sftp is None:
            self.error_occurred.emit("Not connected to any session")
            return False
        return True

    def _make_lister(self, state, transport=None, adapter=None):
        """Create, wire, and start the async directory lister for a connection."""
        lister = SFTPLister(transport=transport, adapter=adapter, parent=self)
        lister.listed.connect(
            lambda rid, path, entries, st=state: self._on_listed(st, rid, path, entries))
        lister.failed.connect(
            lambda rid, path, err, st=state: self._on_list_failed(st, rid, path, err))
        lister.finished.connect(lister.deleteLater)
        lister.start()
        state["lister"] = lister

    def attach_sftp(self, ssh_worker, sftp, home_path="."):
        """Register a ready SFTP session (opened by the worker thread) for a
        worker and, if its tab is the active one, display it."""
        state = {"sftp": sftp, "path": home_path or ".", "worker": ssh_worker}
        try:
            transport = sftp.get_channel().get_transport()
        except Exception:
            transport = None
        self._make_lister(state, transport=transport)
        self._states[id(ssh_worker)] = state
        if ssh_worker is self.active_worker:
            self._activate_state(state)

    def attach_local(self, worker, start_path=None):
        """Register a local-filesystem browser for a local/home terminal worker."""
        adapter = LocalFSAdapter()
        path = adapter.normalize(start_path or "~")
        state = {"sftp": adapter, "path": path, "worker": worker}
        # The lister lists on its own LocalFSAdapter (thread-safe), off the GUI thread.
        self._make_lister(state, adapter=LocalFSAdapter())
        self._states[id(worker)] = state
        if worker is self.active_worker:
            self._activate_state(state)

    def show_worker(self, worker):
        """Switch the panel to display the given SSH worker's files. Pass None
        (or a non-SSH/unauthenticated worker) to clear the panel."""
        self.active_worker = worker
        state = self._states.get(id(worker)) if worker is not None else None
        if state is None:
            # Nothing to show for this tab yet (non-SSH or not authenticated)
            self.sftp = None
            self._active_state = None
            self.current_path = "."
            self.path_edit.clear()
            self.cancel_rename()
            self.model.clear()
            self.model.setHorizontalHeaderLabels(["Name", "Size", "Modified"])
            return
        self._activate_state(state)

    # Shell setup that makes an SSH session report its directory via OSC 7 each
    # prompt, sent once when "Follow terminal folder" is enabled. Local/home
    # terminals don't need this - their cwd is tracked at spawn (see local_pty.py),
    # so nothing is ever typed into them.
    #
    # Both hooks call one function, __omniterm_cwd:
    #   - bash: PREPEND it to PROMPT_COMMAND (keeping any existing hook) instead
    #     of overwriting - overwriting broke the user's prompt and was fragile.
    #   - zsh: add it to precmd_functions, which survives frameworks like
    #     oh-my-zsh / powerlevel10k. A bare `precmd` (the old approach) gets
    #     clobbered by those frameworks, so following silently never fired.
    # The zsh array append is behind eval + a $ZSH_VERSION guard so POSIX shells
    # (dash/sh) don't hit a parse error that would abort the whole line.
    # It's sent with a leading space (kept out of history) via send_invisible(),
    # which hides everything until the first OSC 7 so no echo appears.
    FOLLOW_CMD = (
        " __omniterm_cwd() { printf '\\033]7;file://%s\\007' \"$PWD\"; }; "
        "case \"$PROMPT_COMMAND\" in *__omniterm_cwd*) ;; "
        "*) PROMPT_COMMAND=\"__omniterm_cwd${PROMPT_COMMAND:+;$PROMPT_COMMAND}\" ;; esac; "
        "[ -n \"$ZSH_VERSION\" ] && eval 'precmd_functions+=(__omniterm_cwd)'; true\n"
    )

    def _bootstrap_follow(self, worker):
        """Configure the shell to emit OSC 7 so the panel can follow it.

        Local/home terminals already report their cwd (environment/procfs), so
        nothing is injected for them. Only SSH sessions - where we can't preset
        the remote environment - get the one-time FOLLOW_CMD, sent with its echo
        suppressed so it never shows in the terminal."""
        if worker is None or id(worker) in self._bootstrapped:
            return
        # Local (and serial) workers need no injected command; mark them done.
        if worker.__class__.__name__ != "SSHWorker":
            self._bootstrapped.add(id(worker))
            return
        try:
            if hasattr(worker, "send_invisible"):
                worker.send_invisible(self.FOLLOW_CMD)
                self._bootstrapped.add(id(worker))
            elif hasattr(worker, "send_data"):
                worker.send_data(self.FOLLOW_CMD)
                self._bootstrapped.add(id(worker))
        except Exception:
            pass

    def _activate_state(self, state):
        self._active_state = state
        self.sftp = state["sftp"]
        self._completer_dir = None  # refresh path completion for the new connection
        # Clear the previous tab's files now; the async listing repopulates it
        # (so we don't show one connection's files under another while loading).
        self.cancel_rename()
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Name", "Size", "Modified"])
        # When following, jump to the shell's last-known dir for this worker
        path = state["path"]
        if self.follow_check.isChecked():
            self._bootstrap_follow(state["worker"])
            path = self._latest_cwd.get(id(state["worker"]), path)
        self.current_path = path
        self.list_directory(self.current_path)

    def on_terminal_cwd(self, worker, path):
        """Called when a worker's shell reports its working directory."""
        self._latest_cwd[id(worker)] = path
        if (self.follow_check.isChecked() and worker is self.active_worker
                and path and path != self.current_path):
            self.list_directory(path)

    def _on_follow_toggled(self, enabled):
        if enabled and self.active_worker is not None:
            self._bootstrap_follow(self.active_worker)
            path = self._latest_cwd.get(id(self.active_worker))
            if path and path != self.current_path:
                self.list_directory(path)

    def forget_worker(self, worker):
        """Drop cached SFTP state for a worker whose tab was closed."""
        self._latest_cwd.pop(id(worker), None)
        self._bootstrapped.discard(id(worker))
        state = self._states.pop(id(worker), None)
        if state is not None:
            lister = state.get("lister")
            if lister is not None:
                lister.stop()  # exits its loop; deleteLater fires on finished
            try:
                state["sftp"].close()
            except Exception:
                pass
        if worker is self.active_worker:
            self.show_worker(None)

    def shutdown(self):
        """Stop every background QThread (directory listers and any in-flight
        transfer) and wait for them to actually exit.

        Listers and the transfer worker are Qt children of this dock, so if the
        window is torn down while any is still running, Qt aborts the whole
        process ("QThread: Destroyed while thread is still running"). An idle
        lister blocks forever on its condition variable, so it is always
        "running" - it must be stopped and joined explicitly on shutdown."""
        listers = self.findChildren(SFTPLister)
        transfer = self._transfer
        for lister in listers:
            try:
                lister.stop()
            except Exception:
                pass
        if transfer is not None:
            try:
                transfer.cancel()
            except Exception:
                pass
        for thread in [*listers, transfer]:
            if thread is not None:
                try:
                    thread.wait(2000)
                except Exception:
                    pass
        # Last resort: a lister/transfer still running here would be destroyed
        # with the dock and abort the whole process ("QThread: Destroyed while
        # thread is still running"). Force-terminate it - these block in network
        # I/O / condition waits that release the GIL, so this is safe at teardown
        # and strictly better than the abort it prevents.
        for thread in [*listers, transfer]:
            if thread is not None and thread.isRunning():
                try:
                    thread.terminate()
                    thread.wait()
                except Exception:
                    pass

    def refresh(self):
        """Reload the listing for the current directory."""
        self.list_directory(self.current_path)

    # ---- new folder ----
    def create_folder(self):
        """Create a new directory in the current folder with a placeholder name,
        then open an inline rename on it (Explorer-style)."""
        if not self._ensure_connected():
            return
        existing = {self.model.item(r, 0).text()
                    for r in range(self.model.rowCount())
                    if self.model.item(r, 0) is not None}
        name, n = "New Folder", 2
        while name in existing:
            name = f"New Folder ({n})"
            n += 1
        try:
            self.sftp.mkdir(posixpath.join(self.current_path, name))
        except Exception as e:
            self.error_occurred.emit(f"Create Folder Error: {e}")
            return
        self._rename_after_list = name  # start renaming once the listing lands
        self.refresh()

    # ---- in-place rename ----
    def begin_rename(self, index):
        """Open an inline editor on the name cell of `index`."""
        if self.sftp is None or index is None or not index.isValid():
            return
        if self._rename_item is not None:
            return  # an edit is already open
        item = self.model.itemFromIndex(index.siblingAtColumn(0))
        if item is None or item.data(PARENT_ROLE) or not item.data(PATH_ROLE):
            return
        item.setEditable(True)
        # Move focus first: this closes any stale open editor (whose closeEditor
        # callback runs finish_rename) before we record the new edit's state.
        self.tree_view.setCurrentIndex(item.index())
        self._rename_item = item
        self._rename_old = item.text()
        self.tree_view.edit(item.index())

    def cancel_rename(self):
        """Abandon an in-progress rename (the listing is about to be replaced,
        so the edited item is going away)."""
        item, self._rename_item = self._rename_item, None
        if item is not None:
            try:
                item.setEditable(False)
            except RuntimeError:
                pass  # item already deleted with the old model contents

    def finish_rename(self):
        """Apply (or discard) the name edit once its editor closes."""
        item = self._rename_item
        if item is None:
            return
        self._rename_item = None
        item.setEditable(False)
        old_name = self._rename_old
        new_name = item.text().strip()
        old_path = item.data(PATH_ROLE)
        if not new_name or new_name == old_name:
            item.setText(old_name)
            return
        if "/" in new_name or new_name in (".", ".."):
            item.setText(old_name)
            self.error_occurred.emit(f"Invalid name: {new_name}")
            return
        new_path = posixpath.join(posixpath.dirname(old_path), new_name)
        try:
            self.sftp.rename(old_path, new_path)
        except Exception as e:
            item.setText(old_name)
            self.error_occurred.emit(f"Rename Error: {e}")
            return
        item.setText(new_name)
        item.setData(new_path, PATH_ROLE)
        self.status_message.emit(f"Renamed {old_name} to {new_name}")
        self.refresh()

    def _on_path_entered(self):
        if self.sftp is None:
            return
        target = self.path_edit.text().strip()
        if not target:
            return
        try:
            target = self.sftp.normalize(target)
        except Exception:
            pass
        self.list_directory(target)

    def _update_completions(self, text):
        """Populate the path completer with the entries of the directory being
        typed. Lists a directory at most once per directory change."""
        if self.sftp is None:
            return
        # The directory portion is everything up to the last '/'
        head = text.rsplit("/", 1)[0] if "/" in text else ""
        directory = head if head else ("/" if text.startswith("/") else self.current_path)
        if directory == self._completer_dir:
            return
        try:
            entries = self.sftp.listdir_attr(directory)
        except Exception:
            return
        self._completer_dir = directory
        suggestions = []
        for attr in entries:
            is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode is not None else False
            full = posixpath.join(directory, attr.filename)
            suggestions.append(full + "/" if is_dir else full)
        suggestions.sort()
        self._completer_model.setStringList(suggestions)
        self._path_completer.complete()

    def on_header_clicked(self, column):
        if column == self.sort_column:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = column
            self.sort_desc = False
        order = Qt.SortOrder.DescendingOrder if self.sort_desc else Qt.SortOrder.AscendingOrder
        self.tree_view.header().setSortIndicator(column, order)
        self.list_directory(self.current_path)

    @staticmethod
    def _format_size(num_bytes):
        if num_bytes is None:
            return ""
        size = float(num_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024

    @staticmethod
    def _format_mtime(mtime):
        if not mtime:
            return ""
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        except Exception:
            return ""

    def list_directory(self, path):
        """Request the contents of `path` from the async lister (off the GUI
        thread). The view is repopulated later, in _on_listed, so a slow or
        dropped connection never blocks the UI."""
        state = self._active_state
        if state is None or state.get("lister") is None:
            return
        self._list_req += 1
        state["lister"].request(self._list_req, path)

    def _on_list_failed(self, state, req_id, path, err):
        # Ignore stale results (superseded request or the user switched tabs).
        if state is not self._active_state or req_id != self._list_req:
            return
        self.error_occurred.emit(f"SFTP List Error: {err}")

    def _on_listed(self, state, req_id, path, entries):
        """Populate the view from a completed async listing, unless it's stale."""
        if state is not self._active_state or req_id != self._list_req:
            return
        self.current_path = path
        state["path"] = path  # remember location per connection
        self.path_edit.setText(path)
        self.cancel_rename()
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Name", "Size", "Modified"])

        # Parent ("..") entry, unless we are at the filesystem root
        if path not in ("/", ""):
            up_item = QStandardItem("..")
            up_item.setEditable(False)
            up_item.setIcon(get_icon("folder"))
            up_item.setData(posixpath.normpath(posixpath.join(path, "..")), PATH_ROLE)
            up_item.setData(True, ISDIR_ROLE)
            up_item.setData(True, PARENT_ROLE)
            self.model.appendRow([up_item, QStandardItem(""), QStandardItem("")])

        def is_dir_of(attr):
            return stat.S_ISDIR(attr.st_mode) if attr.st_mode is not None else False

        # Sort by the chosen column...
        if self.sort_column == 1:
            entries.sort(key=lambda a: a.st_size or 0, reverse=self.sort_desc)
        elif self.sort_column == 2:
            entries.sort(key=lambda a: a.st_mtime or 0, reverse=self.sort_desc)
        else:
            entries.sort(key=lambda a: a.filename.lower(), reverse=self.sort_desc)
        # ...then optionally keep directories grouped before files
        # (stable sort preserves the ordering above within each group)
        if self.group_folders_first:
            entries.sort(key=lambda a: 0 if is_dir_of(a) else 1)

        for attr in entries:
            name = attr.filename
            is_dir = is_dir_of(attr)
            full_path = posixpath.join(path, name)

            name_item = QStandardItem(name)
            name_item.setEditable(False)
            name_item.setIcon(file_icon(name, is_dir))
            name_item.setData(full_path, PATH_ROLE)
            name_item.setData(is_dir, ISDIR_ROLE)
            name_item.setData(False, PARENT_ROLE)

            size_text = "" if is_dir else self._format_size(attr.st_size)
            size_item = QStandardItem(size_text)
            size_item.setEditable(False)

            mtime_item = QStandardItem(self._format_mtime(attr.st_mtime))
            mtime_item.setEditable(False)

            self.model.appendRow([name_item, size_item, mtime_item])

        self.tree_view.resizeColumnToContents(0)

        # A freshly created folder gets renamed as soon as it appears.
        pending, self._rename_after_list = self._rename_after_list, None
        if pending:
            for r in range(self.model.rowCount()):
                item = self.model.item(r, 0)
                if item is not None and item.text() == pending:
                    self.tree_view.scrollTo(item.index())
                    self.begin_rename(item.index())
                    break

    def on_item_double_clicked(self, index):
        item = self.model.itemFromIndex(index.siblingAtColumn(0))
        if item is None:
            return
        path = item.data(PATH_ROLE)
        if not path:
            return
        if item.data(ISDIR_ROLE):
            # Navigate into the directory (or up, for "..")
            self.list_directory(path)
        elif isinstance(self.sftp, LocalFSAdapter):
            # Local file: open it directly with the OS default application.
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            # Remote file: download to a temp copy, open it, and sync back on save.
            self.open_for_edit(path)

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
            item = self.model.itemFromIndex(index.siblingAtColumn(0))
            is_dir = bool(item.data(ISDIR_ROLE))
            if is_dir and not item.data(PARENT_ROLE):
                open_action = QAction("Open", self)
                open_action.triggered.connect(lambda: self.list_directory(item.data(PATH_ROLE)))
                menu.addAction(open_action)
            if not item.data(PARENT_ROLE):
                rename_action = QAction("Rename", self)
                rename_action.setShortcut("F2")
                rename_action.triggered.connect(
                    lambda checked=False, ix=index.siblingAtColumn(0): self.begin_rename(ix))
                menu.addAction(rename_action)

        if len(selected_files) > 1:
            download_action = QAction(f"Download {len(selected_files)} Files...", self)
            download_action.triggered.connect(lambda: self.download_files(selected_files))
            menu.addAction(download_action)
        elif len(selected_files) == 1:
            if isinstance(self.sftp, LocalFSAdapter):
                open_action = QAction("Open", self)
                open_action.triggered.connect(
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(selected_files[0])))
                menu.addAction(open_action)
            else:
                edit_action = QAction("Edit (open && sync on save)", self)
                edit_action.triggered.connect(lambda: self.open_for_edit(selected_files[0]))
                menu.addAction(edit_action)
            download_action = QAction("Download...", self)
            download_action.triggered.connect(lambda: self.download_path(selected_files[0]))
            menu.addAction(download_action)

        new_folder_action = QAction("New Folder", self)
        new_folder_action.triggered.connect(self.create_folder)
        menu.addAction(new_folder_action)

        # Upload always targets the current directory (supports multiple files)
        upload_action = QAction("Upload Files Here...", self)
        upload_action.triggered.connect(self.upload_to_current)
        menu.addAction(upload_action)

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh)
        menu.addAction(refresh_action)

        menu.addSeparator()
        group_action = QAction("Group Folders First", self)
        group_action.setCheckable(True)
        group_action.setChecked(self.group_folders_first)
        group_action.toggled.connect(self.set_group_folders_first)
        menu.addAction(group_action)

        menu.exec(self.tree_view.mapToGlobal(position))

    def set_group_folders_first(self, enabled):
        self.group_folders_first = bool(enabled)
        set_group_folders_first(self.group_folders_first)
        self.list_directory(self.current_path)

    # ---- progress-tracked transfers ----
    def _transport_or_none(self):
        """paramiko Transport for a remote SFTP client, or None for local."""
        if isinstance(self.sftp, LocalFSAdapter):
            return None
        try:
            return self.sftp.get_channel().get_transport()
        except Exception:
            return None

    def _remote_size(self, remote_path):
        try:
            return int(self.sftp.stat(remote_path).st_size)
        except Exception:
            return 0

    def _run_transfer(self, jobs, title, on_success=None):
        """Run get/put jobs in a worker thread behind a modal progress dialog."""
        if not jobs:
            return
        if self._transfer is not None:
            self.error_occurred.emit("A file transfer is already in progress.")
            return
        transport = self._transport_or_none()
        local_adapter = self.sftp if transport is None else None
        worker = TransferWorker(jobs, transport=transport, local_adapter=local_adapter)

        dlg = QProgressDialog(title, "Cancel", 0, 100, self)
        dlg.setWindowTitle("File Transfer")
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        def on_progress(done, total, name):
            if total > 0:
                dlg.setLabelText(f"{title}\n{name}\n"
                                 f"{self._format_size(done)} / {self._format_size(total)}")
                dlg.setValue(int(done * 100 / total))
            else:
                dlg.setLabelText(f"{title}\n{name}")

        def on_finished(ok, errors):
            dlg.close()
            self._transfer = None
            self._progress = None
            real_errors = [e for e in errors if e != "Cancelled"]
            if real_errors:
                self.error_occurred.emit("; ".join(real_errors[:3]))
            elif "Cancelled" not in errors and on_success:
                on_success()

        worker.progress.connect(on_progress)
        worker.finished_all.connect(on_finished)
        dlg.canceled.connect(worker.cancel)
        self._transfer = worker
        self._progress = dlg
        worker.start()
        dlg.show()

    def download_files(self, remote_paths):
        """Download several files at once into a chosen local directory."""
        if not self._ensure_connected() or not remote_paths:
            return
        target_dir = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if not target_dir:
            return
        jobs = [{"kind": "download", "src": rp,
                 "dst": os.path.join(target_dir, posixpath.basename(rp)),
                 "size": self._remote_size(rp), "name": posixpath.basename(rp)}
                for rp in remote_paths]
        self._run_transfer(jobs, f"Downloading {len(jobs)} file(s)…")

    def download_path(self, remote_path):
        if not self._ensure_connected() or not remote_path:
            return
        local_path, _ = QFileDialog.getSaveFileName(self, "Save File", os.path.basename(remote_path))
        if not local_path:
            return
        name = posixpath.basename(remote_path)
        job = {"kind": "download", "src": remote_path, "dst": local_path,
               "size": self._remote_size(remote_path), "name": name}
        self._run_transfer([job], f"Downloading {name}…")

    def upload_to_current(self):
        if not self._ensure_connected():
            return
        local_paths, _ = QFileDialog.getOpenFileNames(self, "Select File(s) to Upload")
        if local_paths:
            self.upload_local_paths(local_paths, self.current_path)

    def upload_local_paths(self, local_paths, remote_dir):
        """Upload one or more local files/folders into remote_dir, recursing into
        directories. Remote dirs are created up front; files transfer with a
        progress bar. Refreshes the view if the upload landed in the current dir."""
        if not self._ensure_connected():
            return
        jobs = []
        try:
            for local_path in local_paths:
                self._collect_upload_jobs(local_path, remote_dir, jobs)
        except Exception as e:
            self.error_occurred.emit(f"Upload Error: {e}")
            return
        if not jobs:
            return
        should_refresh = (remote_dir == self.current_path)
        self._run_transfer(
            jobs, f"Uploading {len(jobs)} file(s)…",
            on_success=(self.refresh if should_refresh else None))

    def _collect_upload_jobs(self, local_path, remote_dir, jobs):
        name = os.path.basename(local_path.rstrip("/\\")) or local_path
        remote_path = posixpath.join(remote_dir, name)
        if os.path.isdir(local_path):
            try:
                self.sftp.mkdir(remote_path)
            except Exception:
                pass  # directory may already exist
            for entry in sorted(os.listdir(local_path)):
                self._collect_upload_jobs(os.path.join(local_path, entry), remote_path, jobs)
        else:
            jobs.append({"kind": "upload", "src": local_path, "dst": remote_path,
                         "size": os.path.getsize(local_path) if os.path.exists(local_path) else 0,
                         "name": name})

    # ---- double-click to edit: download, open, sync back on save ----
    def open_for_edit(self, remote_path):
        if not self._ensure_connected() or not remote_path:
            return
        name = posixpath.basename(remote_path)
        editdir = os.path.join(tempfile.gettempdir(), "omniterm_edit")
        try:
            os.makedirs(editdir, exist_ok=True)
        except OSError as e:
            self.error_occurred.emit(f"Cannot create edit folder: {e}")
            return
        # Keep the real name but avoid collisions between dirs/sessions.
        local_path = os.path.join(editdir, f"{abs(hash(remote_path)) % 10**8}_{name}")
        job = {"kind": "download", "src": remote_path, "dst": local_path,
               "size": self._remote_size(remote_path), "name": name}
        self._run_transfer(
            [job], f"Opening {name} for editing…",
            on_success=lambda: self._start_editing(local_path, remote_path))

    def _start_editing(self, local_path, remote_path):
        if not os.path.exists(local_path):
            return
        try:
            mtime = os.path.getmtime(local_path)
        except OSError:
            mtime = 0
        self._edits[local_path] = {"remote": remote_path, "mtime": mtime}
        self._watcher.addPath(local_path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(local_path))

    def _on_edited_file_changed(self, local_path):
        if local_path in self._prompting:
            return
        # Editors that save via atomic replace briefly remove the file; wait a
        # beat before handling, then re-arm the watch.
        QTimer.singleShot(150, lambda: self._handle_edit_change(local_path))

    def _handle_edit_change(self, local_path):
        info = self._edits.get(local_path)
        if not info:
            return
        if not os.path.exists(local_path):
            QTimer.singleShot(300, lambda: self._rearm_watch(local_path))
            return
        try:
            mtime = os.path.getmtime(local_path)
        except OSError:
            return
        if mtime == info["mtime"]:
            self._rearm_watch(local_path)
            return
        info["mtime"] = mtime
        name = posixpath.basename(info["remote"])
        self._prompting.add(local_path)
        reply = QMessageBox.question(
            self, "Upload Changes",
            f"“{name}” changed.\n\nUpload and overwrite it on the remote?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        self._prompting.discard(local_path)
        if reply == QMessageBox.StandardButton.Yes:
            job = {"kind": "upload", "src": local_path, "dst": info["remote"],
                   "size": os.path.getsize(local_path) if os.path.exists(local_path) else 0,
                   "name": name}
            self._run_transfer(
                [job], f"Uploading {name}…",
                on_success=lambda: self._after_edit_upload(info))
        self._rearm_watch(local_path)

    def _rearm_watch(self, local_path):
        if os.path.exists(local_path) and local_path not in self._watcher.files():
            self._watcher.addPath(local_path)

    def _after_edit_upload(self, info):
        name = posixpath.basename(info["remote"])
        self.status_message.emit(f"Uploaded changes to {name}")
        if posixpath.dirname(info["remote"]) == self.current_path:
            self.refresh()
