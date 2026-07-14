import os
import re
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QVBoxLayout, QWidget, QDialog, QFormLayout, QLineEdit, QPushButton, QComboBox, QFileDialog, QMessageBox, QSpinBox, QColorDialog, QInputDialog, QMenu, QCheckBox, QToolBar, QToolButton, QKeySequenceEdit
from PyQt6.QtGui import QColor, QDesktopServices, QAction, QIcon, QPixmap, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QUrl, QSize, QTimer
from omniterm.ui.icons import get_icon, app_icon
from omniterm.ui.theme import APP_STYLESHEET
from omniterm.ui.session_dock import SessionDock
from omniterm.ui.terminal_tab import TerminalTab, SplitContainer
from omniterm.ui.sftp_browser import SFTPBrowser
from omniterm.core.ssh_client import SSHWorker
from omniterm.core.serial_client import SerialWorker
from omniterm.core.local_pty import LocalPTYWorker
from omniterm.core.config import HOME_DIR, set_home_dir, init_cipher, set_shared_sessions_file, get_terminal_settings, set_terminal_settings, export_sessions, import_sessions, get_use_inshellisense, set_use_inshellisense, get_layouts, save_layout, delete_layout, find_session, get_renderer, set_renderer

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OmniTerm")
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)

        # Modern dark theme
        self.setStyleSheet(APP_STYLESHEET)

        # Main Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)  # drag tabs to reorder
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)

        # Rename tabs: double-click a tab, or right-click for a menu
        self.tabs.tabBarDoubleClicked.connect(self.rename_tab)
        tab_bar = self.tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self.show_tab_context_menu)

        # Session Dock
        self.session_dock = SessionDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.session_dock)

        # SFTP Browser
        self.sftp_browser = SFTPBrowser(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sftp_browser)
        self.sftp_browser.error_occurred.connect(self.show_sftp_error)

        # Show the active tab's remote files when the selected tab changes
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # Background-activity indicator (blinking dot on inactive tabs).
        # A fixed-size icon slot is always reserved (transparent when idle) so
        # the tab width never changes when the dot blinks on/off.
        _sz = 12
        self.tabs.setIconSize(QSize(_sz, _sz))
        _blank = QPixmap(_sz, _sz)
        _blank.fill(Qt.GlobalColor.transparent)
        self._blank_icon = QIcon(_blank)
        self._dot_icon = QIcon(get_icon("dot").pixmap(_sz, _sz))
        self._activity = {}      # top-level tab -> blink ticks remaining
        self._blink_on = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._blink_activity)
        self._blink_timer.start()

        # Connect session selection to tab creation
        self.session_dock.tree_view.clicked.connect(self.on_session_selected)

        # Setup Keyboard Shortcuts
        self.setup_shortcuts()

        # Build the menus (attached to toolbar buttons below, not a menu bar)
        self.menuBar().hide()

        self.session_menu = QMenu(self)
        self.home_terminal_action = self.session_menu.addAction("New Home Terminal (Local Unix)")
        self.home_terminal_action.triggered.connect(self.open_home_terminal)
        self.session_menu.addSeparator()
        self.add_session_action = self.session_menu.addAction("Add Session")
        self.add_session_action.triggered.connect(self.show_add_session_dialog)
        self.export_sessions_action = self.session_menu.addAction("Export Sessions...")
        self.export_sessions_action.triggered.connect(self.export_sessions_to_file)
        self.import_sessions_action = self.session_menu.addAction("Import Sessions...")
        self.import_sessions_action.triggered.connect(self.import_sessions_from_file)
        self.session_menu.addSeparator()
        self.save_layout_action = self.session_menu.addAction("Save Current Layout...")
        self.save_layout_action.triggered.connect(self.show_save_layout_dialog)
        self.open_layout_action = self.session_menu.addAction("Open Layout...")
        self.open_layout_action.triggered.connect(self.show_open_layout_dialog)

        self.split_menu = QMenu(self)
        self.split_single_action = self.split_menu.addAction("Single Terminal")
        self.split_single_action.triggered.connect(self.unsplit_current_tab)
        self.split_2h_action = self.split_menu.addAction("2 Panes (Horizontal)")
        self.split_2h_action.triggered.connect(lambda: self.show_split_view_dialog("2h"))
        self.split_2v_action = self.split_menu.addAction("2 Panes (Vertical)")
        self.split_2v_action.triggered.connect(lambda: self.show_split_view_dialog("2v"))
        self.split_4_action = self.split_menu.addAction("4 Panes")
        self.split_4_action.triggered.connect(lambda: self.show_split_view_dialog("4"))

        self.settings_menu = QMenu(self)
        self.terminal_appearance_action = self.settings_menu.addAction("Terminal Appearance...")
        self.terminal_appearance_action.triggered.connect(self.show_terminal_appearance_dialog)
        self.inshellisense_action = self.settings_menu.addAction("Command Autocomplete (Inshellisense)")
        self.inshellisense_action.setCheckable(True)
        self.inshellisense_action.setChecked(get_use_inshellisense())
        self.inshellisense_action.toggled.connect(self._toggle_inshellisense)
        self.shortcuts_action = self.settings_menu.addAction("Keyboard Shortcuts...")
        self.shortcuts_action.triggered.connect(self.show_shortcuts_dialog)
        # Terminal renderer (DOM is most stable; WebGL/canvas faster but can blank)
        from PyQt6.QtGui import QActionGroup
        self.renderer_menu = self.settings_menu.addMenu("Terminal Renderer")
        self._renderer_group = QActionGroup(self)
        current_renderer = get_renderer()
        for key, label in (("dom", "DOM (most stable, recommended)"),
                           ("canvas", "Canvas (faster)"),
                           ("webgl", "WebGL (fastest, may blank on htop/btop)")):
            act = self.renderer_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current_renderer == key)
            act.triggered.connect(lambda _checked, k=key: self._set_renderer(k))
            self._renderer_group.addAction(act)
        from omniterm.core.config import get_debug_logging
        self.debug_log_action = self.settings_menu.addAction("Debug: Log Terminal I/O")
        self.debug_log_action.setCheckable(True)
        self.debug_log_action.setChecked(get_debug_logging())
        self.debug_log_action.toggled.connect(self._toggle_debug_logging)
        self.open_tools_action = self.settings_menu.addAction("Open Home Tools Folder (rsync, etc.)...")
        self.open_tools_action.triggered.connect(self.open_tools_folder)
        self.set_home_dir_action = self.settings_menu.addAction("Set Persistent Home Directory...")
        self.set_home_dir_action.triggered.connect(self.show_set_home_dir_dialog)
        self.set_master_password_action = self.settings_menu.addAction("Set Master Password...")
        self.set_master_password_action.triggered.connect(self.show_set_master_password_dialog)
        self.set_shared_sessions_action = self.settings_menu.addAction("Set Shared Sessions File...")
        self.set_shared_sessions_action.triggered.connect(self.show_set_shared_sessions_dialog)
        self.manage_plugins_action = self.settings_menu.addAction("Manage Plugins...")
        self.manage_plugins_action.triggered.connect(self.show_plugin_manager_dialog)
        self.shell_integration_action = self.settings_menu.addAction("Shell Integration Guide...")
        self.shell_integration_action.triggered.connect(self.show_shell_integration_dialog)

        self.help_menu = QMenu(self)
        self.github_action = self.help_menu.addAction("GitHub / Support")
        self.github_action.triggered.connect(self.open_github)
        self.about_action = self.help_menu.addAction("About OmniTerm")
        self.about_action.triggered.connect(self.show_about_dialog)

        self._build_toolbar()

    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setObjectName("ribbon")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(26, 26))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        def add_button(label, icon_name, menu=None, slot=None):
            btn = QToolButton()
            btn.setText(label)
            btn.setIcon(get_icon(icon_name))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            if menu is not None:
                btn.setMenu(menu)
                btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            if slot is not None:
                btn.clicked.connect(slot)
            toolbar.addWidget(btn)
            return btn

        add_button("Sessions", "session", menu=self.session_menu)
        add_button("Home", "home", slot=self.open_home_terminal)
        toolbar.addSeparator()
        add_button("Split", "split", menu=self.split_menu)
        toolbar.addSeparator()
        add_button("Settings", "settings", menu=self.settings_menu)
        add_button("Help", "help", menu=self.help_menu)

    # layout key -> (pane count, splitter orientation, title)
    # "Horizontal" = a horizontal divider (panes stacked); "Vertical" = a
    # vertical divider (panes side by side), matching the vim/tmux convention.
    SPLIT_LAYOUTS = {
        "2h": (2, "vertical", "2 Panes (Horizontal)"),
        "2v": (2, "horizontal", "2 Panes (Vertical)"),
        "4": (4, "horizontal", "4 Panes"),
    }

    def unsplit_current_tab(self):
        index = self.tabs.currentIndex()
        if index >= 0 and getattr(self.tabs.widget(index), "terminals", None) is not None:
            self.unsplit_tab(index)
        else:
            QMessageBox.information(
                self, "Single Terminal",
                "The current tab isn't split. 'Single Terminal' unsplits a split "
                "tab back into individual tabs.")

    def show_split_view_dialog(self, layout_key="2h"):
        count, orientation, title = self.SPLIT_LAYOUTS.get(layout_key, self.SPLIT_LAYOUTS["2h"])

        # Candidate tabs = currently open single terminals (not already-split tabs)
        candidates = []
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if getattr(widget, "terminals", None) is None and hasattr(widget, "worker"):
                candidates.append((self.tabs.tabText(i), widget))

        if len(candidates) < 2:
            QMessageBox.information(
                self, "Split View",
                "Open at least two session tabs first, then split them into one view.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        form = QFormLayout(dialog)

        pickers = []
        for i in range(count):
            combo = QComboBox()
            for label, widget in candidates:
                combo.addItem(label, widget)
            if i < len(candidates):
                combo.setCurrentIndex(i)  # default each pane to a different tab
            pickers.append(combo)
            form.addRow(f"Pane {i + 1}:", combo)

        def open_it():
            chosen, seen = [], set()
            for combo in pickers:
                widget = combo.currentData()
                if widget is not None and id(widget) not in seen:
                    seen.add(id(widget))
                    chosen.append(widget)
            dialog.accept()
            if len(chosen) >= 2:
                self.combine_tabs_into_split(count, chosen, orientation)

        btn = QPushButton("Split")
        btn.clicked.connect(open_it)
        form.addRow(btn)

        dialog.exec()

    def combine_tabs_into_split(self, count, widgets, orientation="horizontal"):
        """Combine the given open terminal tabs into one split tab.

        A QWebEngineView goes permanently blank if it is reparented after being
        shown, so instead of moving the existing views we build fresh terminals
        inside the split and transplant the running workers into them.
        """
        qt_orientation = (Qt.Orientation.Vertical if orientation == "vertical"
                          else Qt.Orientation.Horizontal)
        container = SplitContainer(count, qt_orientation)
        for old_tab in widgets:
            worker = getattr(old_tab, "worker", None)

            # Detach the worker from the old tab's view
            if worker is not None:
                try:
                    worker.data_received.disconnect(old_tab.bridge.onDataReceived)
                except (TypeError, RuntimeError):
                    pass
                try:
                    worker.error_occurred.disconnect(old_tab.handle_error)
                except (TypeError, RuntimeError):
                    pass
                try:
                    worker.disconnected.disconnect(old_tab.on_disconnected)
                except (TypeError, RuntimeError, AttributeError):
                    pass

            # Fresh terminal, created directly inside the split hierarchy
            new_tab = TerminalTab(
                old_tab.session_name,
                windows_mode=self._needs_windows_mode(getattr(old_tab, "session_type", None)),
                renderer=get_renderer())
            new_tab.apply_settings(get_terminal_settings())
            self._wire_terminal(new_tab, getattr(old_tab, "session_type", None),
                                getattr(old_tab, "session_data", None))
            if worker is not None:
                new_tab.set_worker(worker)  # reconnects the live worker to the new view
            container.add_terminal(new_tab)

            # Drop the old tab without stopping the (now transplanted) worker
            idx = self.tabs.indexOf(old_tab)
            if idx != -1:
                self.tabs.removeTab(idx)
            old_tab.worker = None
            old_tab.deleteLater()

        self.tabs.addTab(container, f"Split x{len(container.terminals)}")
        self.tabs.setCurrentWidget(container)
        return container

    def on_session_selected(self, index):
        item = self.session_dock.model.itemFromIndex(index)
        if not (item and item.data(32)): # Must be a node carrying session data
            return

        session_data = item.data(32)
        # Folders are containers, not connectable sessions
        if session_data.get("type") == "folder":
            return

        session_type = session_data.get("type", "local")
        self.create_terminal_tab(session_type, session_data)

    def _make_worker(self, session_type, session_data, init=None):
        ish = get_use_inshellisense()
        if session_type == "ssh":
            data = session_data
            if init:
                existing = session_data.get("startup_script")
                combined = f"{existing}\n{init}" if existing else init
                data = {**session_data, "startup_script": combined}
            return SSHWorker(data, inshellisense=ish)
        elif session_type == "serial":
            return SerialWorker(
                session_data.get("com_port"),
                session_data.get("baud_rate", 115200),
                session_data.get("data_bits", 8),
                session_data.get("parity", "N"),
                session_data.get("stop_bits", 1)
            )
        elif session_type == "local":
            return LocalPTYWorker(inshellisense=ish, startup=init)
        elif session_type == "home":
            # MobaXterm-style local Unix shell (Git Bash/WSL/BusyBox on Windows)
            return LocalPTYWorker(prefer_unix=True, inshellisense=ish, startup=init)
        return None

    def _start_worker_for(self, tab, worker):
        tab.set_worker(worker)
        if isinstance(worker, SSHWorker):
            # Each SSH worker opens its own SFTP session (in its thread) and
            # hands it over; the panel shows the active tab's connection.
            worker.sftp_ready.connect(
                lambda payload, w=worker: self.sftp_browser.attach_sftp(w, payload[0], payload[1]))
            worker.cwd_changed.connect(
                lambda path, w=worker: self.sftp_browser.on_terminal_cwd(w, path))
        elif isinstance(worker, LocalPTYWorker):
            # Local/home terminals show the local filesystem in the Files panel.
            self.sftp_browser.attach_local(worker, os.path.expanduser("~"))
        worker.start()

    def _wire_terminal(self, tab, session_type, session_data):
        """Record how to rebuild this terminal and hook its reconnect/close actions."""
        tab.session_type = session_type
        tab.session_data = session_data
        tab.reconnect_requested.connect(lambda t=tab: self.on_terminal_reconnect_requested(t))
        tab.close_requested.connect(lambda t=tab: self.on_terminal_close_requested(t))
        tab.activity.connect(lambda t=tab: self._on_terminal_activity(t))

    # Blink for a few ticks after the last output, then stop (so an idle tab
    # doesn't keep blinking forever).
    ACTIVITY_TICKS = 5

    def _on_terminal_activity(self, term):
        top = self._top_level_tab_of(term)
        if top is None:
            return
        idx = self.tabs.indexOf(top)
        if idx == -1 or idx == self.tabs.currentIndex():
            return  # ignore activity on the tab you're already looking at
        self._activity[top] = self.ACTIVITY_TICKS

    def _ensure_tab_icons(self):
        # Reserve a fixed icon slot on every tab so widths stay constant
        for i in range(self.tabs.count()):
            if self.tabs.tabIcon(i).isNull():
                self.tabs.setTabIcon(i, self._blank_icon)

    def _blink_activity(self):
        self._blink_on = not self._blink_on
        for top in list(self._activity):
            idx = self.tabs.indexOf(top)
            if idx == -1:
                del self._activity[top]
                continue
            self._activity[top] -= 1
            if self._activity[top] <= 0:
                del self._activity[top]
                self.tabs.setTabIcon(idx, self._blank_icon)
            else:
                self.tabs.setTabIcon(idx, self._dot_icon if self._blink_on else self._blank_icon)

    def _clear_activity(self, top):
        if top in self._activity:
            del self._activity[top]
        idx = self.tabs.indexOf(top)
        if idx != -1:
            self.tabs.setTabIcon(idx, self._blank_icon)

    @staticmethod
    def _needs_windows_mode(session_type):
        # Local/home terminals on Windows are ConPTY-backed and need xterm's
        # Windows-PTY mode; SSH (remote Linux) does not.
        return os.name == "nt" and session_type in ("local", "home")

    def build_terminal(self, session_type, session_data, init=None):
        """Create a TerminalTab, start its worker, and apply appearance
        settings. Does not add it to the tab widget (the caller places it)."""
        tab = TerminalTab(session_data.get("name", "Unnamed Session"),
                          windows_mode=self._needs_windows_mode(session_type),
                          renderer=get_renderer())
        tab.apply_settings(get_terminal_settings())
        self._wire_terminal(tab, session_type, session_data)

        worker = self._make_worker(session_type, session_data, init=init)
        if worker:
            self._start_worker_for(tab, worker)

        return tab

    def on_terminal_reconnect_requested(self, tab):
        session_type = getattr(tab, "session_type", None)
        session_data = getattr(tab, "session_data", None)
        if not session_type or session_data is None:
            return
        old = getattr(tab, "worker", None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        worker = self._make_worker(session_type, session_data)
        if worker:
            tab.hide_disconnect_bar()
            self._start_worker_for(tab, worker)

    def _top_level_tab_of(self, term):
        if self.tabs.indexOf(term) != -1:
            return term
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            terminals = getattr(widget, "terminals", None)
            if terminals and term in terminals:
                return widget
        return None

    def on_terminal_close_requested(self, term):
        top = self._top_level_tab_of(term)
        if top is not None:
            idx = self.tabs.indexOf(top)
            if idx != -1:
                self.close_tab(idx)

    def create_terminal_tab(self, session_type, session_data):
        session_name = session_data.get("name", "Unnamed Session")
        tab = self.build_terminal(session_type, session_data)
        self.tabs.addTab(tab, session_name)
        self.tabs.setCurrentWidget(tab)
        return tab

    def open_home_terminal(self):
        self.create_terminal_tab("home", {"name": "Home", "type": "home"})

    # ---- Layouts (save/restore the open workspace) ----
    def _session_ref(self, term):
        """A portable reference to a terminal's session for a saved layout."""
        data = getattr(term, "session_data", {}) or {}
        stype = getattr(term, "session_type", data.get("type", "home"))
        if data.get("id"):
            return {"id": data["id"], "name": data.get("name", "Session")}
        # Unsaved (home/local/quick) session — store the minimal spec
        return {"type": stype, "name": data.get("name", stype.title())}

    def _resolve_ref(self, ref):
        """Turn a layout session reference back into (session_type, session_data)."""
        if ref.get("id"):
            session = find_session(ref["id"])
            if session:
                return session.get("type", "ssh"), session
            return None  # session was deleted
        stype = ref.get("type", "home")
        return stype, {"name": ref.get("name", stype.title()), "type": stype}

    def _last_known_dir(self, term):
        """Best-effort current directory for a terminal (for pre-filling 'cd')."""
        worker = getattr(term, "worker", None)
        if worker is None:
            return ""
        return self.sftp_browser._latest_cwd.get(id(worker), "")

    def capture_layout(self):
        """Build a layout structure from the currently open tabs."""
        tabs = []
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            title = self.tabs.tabText(i)  # preserve any custom (renamed) title
            terminals = getattr(widget, "terminals", None)
            if terminals is not None:  # split tab
                panes = [{"session": self._session_ref(t), "init": ""} for t in terminals]
                tabs.append({
                    "kind": "split",
                    "title": title,
                    "count": len(terminals),
                    "orientation": "vertical" if getattr(widget, "root", None) is not None
                                   and widget.root.orientation() == Qt.Orientation.Vertical
                                   else "horizontal",
                    "panes": panes,
                    "terms": terminals,
                })
            elif hasattr(widget, "worker"):
                tabs.append({
                    "kind": "single",
                    "title": title,
                    "session": self._session_ref(widget),
                    "init": "",
                    "terms": [widget],
                })
        return tabs

    # Query a shell for its cwd and active conda env via an (invisible) OSC marker.
    CTX_QUERY = (" printf '\\033]1337;OmniCtx=%s\\037%s\\007' "
                 "\"$PWD\" \"${CONDA_DEFAULT_ENV:-}\"\r")
    CTX_RE = re.compile(r'\x1b\]1337;OmniCtx=([^\x1f]*)\x1f([^\x07\x1b]*)(?:\x07|\x1b\\)')

    def show_save_layout_dialog(self):
        entries = self.capture_layout()
        term_rows = [t for e in entries for t in e["terms"]]
        if not term_rows:
            QMessageBox.information(self, "Save Layout", "Open some sessions first.")
            return

        # Ask each shell for its cwd + conda env; capture the reply, then show
        # the dialog after a short delay.
        self._ctx = {}
        self._ctx_buffers = {}
        self._ctx_conns = []
        for term in term_rows:
            worker = getattr(term, "worker", None)
            if worker is None or not hasattr(worker, "send_data"):
                continue
            self._ctx_buffers[id(term)] = ""

            def handler(data, tid=id(term)):
                buf = self._ctx_buffers.get(tid, "") + data
                self._ctx_buffers[tid] = buf[-8192:]
                m = self.CTX_RE.search(self._ctx_buffers[tid])
                if m:
                    self._ctx[tid] = {"cwd": m.group(1), "conda": m.group(2)}

            worker.data_received.connect(handler)
            self._ctx_conns.append((worker, handler))
            try:
                worker.send_data(self.CTX_QUERY)
            except Exception:
                pass

        QTimer.singleShot(450, lambda: self._show_save_layout_dialog(entries, term_rows))

    def _layout_prefill(self, term):
        ctx = self._ctx.get(id(term), {})
        cwd = ctx.get("cwd") or self._last_known_dir(term)
        conda = ctx.get("conda")
        parts = []
        if cwd:
            parts.append(f"cd {cwd}")
        if conda:
            parts.append(f"conda activate {conda}")
        return " && ".join(parts)

    def _show_save_layout_dialog(self, entries, term_rows):
        for worker, handler in self._ctx_conns:
            try:
                worker.data_received.disconnect(handler)
            except (TypeError, RuntimeError):
                pass
        self._ctx_conns = []

        dialog = QDialog(self)
        dialog.setWindowTitle("Save Current Layout")
        form = QFormLayout(dialog)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Layout name")
        form.addRow("Name:", name_edit)

        init_edits = {}
        for term in term_rows:
            edit = QLineEdit()
            edit.setText(self._layout_prefill(term))
            edit.setPlaceholderText("e.g. cd /path && conda activate myenv")
            init_edits[id(term)] = edit
            form.addRow(f"{getattr(term, 'session_name', 'Terminal')}:", edit)

        def do_save():
            name = name_edit.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Save Layout", "Please enter a name.")
                return
            layout = {"tabs": []}
            for entry in entries:
                if entry["kind"] == "split":
                    panes = []
                    for term, pane in zip(entry["terms"], entry["panes"]):
                        panes.append({"session": pane["session"],
                                      "init": init_edits[id(term)].text().strip()})
                    layout["tabs"].append({"kind": "split", "title": entry.get("title", ""),
                                           "count": entry["count"],
                                           "orientation": entry["orientation"], "panes": panes})
                else:
                    term = entry["terms"][0]
                    layout["tabs"].append({"kind": "single", "title": entry.get("title", ""),
                                           "session": entry["session"],
                                           "init": init_edits[id(term)].text().strip()})
            save_layout(name, layout)
            dialog.accept()
            QMessageBox.information(self, "Layout Saved",
                                    f"Layout '{name}' saved. Open it from Sessions -> Open Layout.")

        btn = QPushButton("Save")
        btn.clicked.connect(do_save)
        form.addRow(btn)
        dialog.exec()

    def show_open_layout_dialog(self):
        layouts = get_layouts()
        if not layouts:
            QMessageBox.information(self, "Open Layout", "No saved layouts yet.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Open Layout")
        form = QFormLayout(dialog)

        combo = QComboBox()
        combo.addItems(sorted(layouts.keys()))
        form.addRow("Layout:", combo)

        btn_open = QPushButton("Open")
        btn_open.clicked.connect(lambda: (self.restore_layout(combo.currentText()), dialog.accept()))
        btn_delete = QPushButton("Delete")
        def do_delete():
            name = combo.currentText()
            if delete_layout(name):
                combo.removeItem(combo.currentIndex())
                if combo.count() == 0:
                    dialog.accept()
        btn_delete.clicked.connect(do_delete)
        form.addRow(btn_open, btn_delete)
        dialog.exec()

    def restore_layout(self, name):
        layout = get_layouts().get(name)
        if not layout:
            return
        for entry in layout.get("tabs", []):
            if entry.get("kind") == "split":
                container = SplitContainer(
                    entry.get("count", 2),
                    Qt.Orientation.Vertical if entry.get("orientation") == "vertical"
                    else Qt.Orientation.Horizontal)
                for pane in entry.get("panes", []):
                    resolved = self._resolve_ref(pane.get("session", {}))
                    if resolved is None:
                        continue
                    stype, sdata = resolved
                    term = self.build_terminal(stype, sdata, init=pane.get("init") or None)
                    container.add_terminal(term)
                if container.terminals:
                    title = entry.get("title") or f"Split x{len(container.terminals)}"
                    self.tabs.addTab(container, title)
                    self.tabs.setCurrentWidget(container)
            else:
                resolved = self._resolve_ref(entry.get("session", {}))
                if resolved is None:
                    continue
                stype, sdata = resolved
                tab = self.build_terminal(stype, sdata, init=entry.get("init") or None)
                title = entry.get("title") or sdata.get("name", "Session")
                self.tabs.addTab(tab, title)
                self.tabs.setCurrentWidget(tab)

    def _toggle_inshellisense(self, enabled):
        set_use_inshellisense(enabled)
        if enabled:
            QMessageBox.information(
                self, "Inshellisense",
                "Command autocomplete (Inshellisense) will start in new terminals.\n\n"
                "Requires the 'is' tool to be installed:\n"
                "  npm install -g @microsoft/inshellisense\n\n"
                "For SSH sessions it must be installed on the remote host. "
                "Existing tabs are unaffected — open a new terminal to use it.")

    def _set_renderer(self, key):
        set_renderer(key)
        QMessageBox.information(
            self, "Terminal Renderer",
            f"Renderer set to '{key}'. It applies to newly opened terminals.\n\n"
            "If full-screen apps (htop/btop) blank out, use DOM.")

    def _toggle_debug_logging(self, enabled):
        from omniterm.core.config import set_debug_logging, DEBUG_LOG_FILE
        set_debug_logging(enabled)
        if enabled:
            QMessageBox.information(
                self, "Debug Logging",
                "Terminal I/O logging is ON.\n\n"
                "Reproduce the issue in a NEW terminal, then send me ~15-20 lines "
                "of the log around a fast-typed word. Turn this OFF when done.\n\n"
                f"Log file:\n{DEBUG_LOG_FILE}")

    def open_tools_folder(self):
        """Open <home>/bin, which is on the Home terminal's PATH. Drop rsync.exe
        (and its DLLs) here to make rsync available in Home terminals."""
        from omniterm.core.config import HOME_DIR
        tools = os.path.join(str(HOME_DIR), "bin")
        try:
            os.makedirs(tools, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(tools))
        QMessageBox.information(
            self, "Home Tools Folder",
            "This folder is on the Home terminal's PATH.\n\n"
            "Drop rsync.exe (and any DLLs it needs, e.g. msys-2.0.dll from Git's "
            "usr\\bin, or cygwin1.dll from cwRsync) here, then open a new Home "
            "terminal and run 'rsync'.\n\n"
            f"{tools}")

    def _primary_fs_worker(self, widget):
        """The worker whose files should be shown for `widget` (a tab): prefer
        an SSH pane (remote SFTP), else a local/home pane (local filesystem)."""
        if widget is None:
            return None
        terminals = getattr(widget, "terminals", None)
        candidates = terminals if terminals is not None else [widget]
        for term in candidates:
            if isinstance(getattr(term, "worker", None), SSHWorker):
                return term.worker
        for term in candidates:
            if isinstance(getattr(term, "worker", None), LocalPTYWorker):
                return term.worker
        return None

    def on_tab_changed(self, index):
        self._ensure_tab_icons()  # keep a fixed icon slot on every tab
        widget = self.tabs.widget(index) if index >= 0 else None
        if widget is not None:
            self._clear_activity(widget)  # focusing a tab clears its activity dot
        self.sftp_browser.show_worker(self._primary_fs_worker(widget))

    def _stop_terminal(self, term):
        worker = getattr(term, "worker", None)
        if not worker:
            return
        if isinstance(worker, (SSHWorker, LocalPTYWorker)):
            self.sftp_browser.forget_worker(worker)
        try:
            worker.data_received.disconnect()
            worker.error_occurred.disconnect()
            if hasattr(worker, "auth_success"):
                worker.auth_success.disconnect()
        except (TypeError, RuntimeError):
            pass
        worker.stop()
        worker.wait()

    def rename_tab(self, index):
        if index < 0:
            return
        current = self.tabs.tabText(index)
        new_name, ok = QInputDialog.getText(self, "Rename Tab", "Tab name:", text=current)
        if ok and new_name.strip():
            self.tabs.setTabText(index, new_name.strip())

    def show_tab_context_menu(self, position):
        tab_bar = self.tabs.tabBar()
        index = tab_bar.tabAt(position)
        if index < 0:
            return
        is_split = getattr(self.tabs.widget(index), "terminals", None) is not None

        menu = QMenu()
        rename_action = menu.addAction("Rename...")
        split_menu = menu.addMenu("Split View")
        split_2h = split_menu.addAction("2 Panes (Horizontal)")
        split_2v = split_menu.addAction("2 Panes (Vertical)")
        split_4 = split_menu.addAction("4 Panes")
        unsplit_action = menu.addAction("Unsplit") if is_split else None
        close_action = menu.addAction("Close")
        chosen = menu.exec(tab_bar.mapToGlobal(position))
        if chosen == rename_action:
            self.rename_tab(index)
        elif chosen == split_2h:
            self.show_split_view_dialog("2h")
        elif chosen == split_2v:
            self.show_split_view_dialog("2v")
        elif chosen == split_4:
            self.show_split_view_dialog("4")
        elif unsplit_action is not None and chosen == unsplit_action:
            self.unsplit_tab(index)
        elif chosen == close_action:
            self.close_tab(index)

    def unsplit_tab(self, index):
        """Split a combined tab back into individual tabs, one per pane."""
        container = self.tabs.widget(index)
        terminals = getattr(container, "terminals", None)
        if terminals is None:
            return

        new_tabs = []
        for old_term in list(terminals):
            worker = getattr(old_term, "worker", None)
            if worker is not None:
                try:
                    worker.data_received.disconnect(old_term.bridge.onDataReceived)
                except (TypeError, RuntimeError):
                    pass
                try:
                    worker.error_occurred.disconnect(old_term.handle_error)
                except (TypeError, RuntimeError):
                    pass
                try:
                    worker.disconnected.disconnect(old_term.on_disconnected)
                except (TypeError, RuntimeError, AttributeError):
                    pass

            new_tab = TerminalTab(
                old_term.session_name,
                windows_mode=self._needs_windows_mode(getattr(old_term, "session_type", None)),
                renderer=get_renderer())
            new_tab.apply_settings(get_terminal_settings())
            self._wire_terminal(new_tab, getattr(old_term, "session_type", None),
                                getattr(old_term, "session_data", None))
            if worker is not None:
                new_tab.set_worker(worker)
            old_term.worker = None  # keep the transplanted worker alive
            new_tabs.append(new_tab)

        # Remove the split container (its now-workerless panes are destroyed with it)
        self.tabs.removeTab(index)
        container.deleteLater()

        for new_tab in new_tabs:
            self.tabs.addTab(new_tab, new_tab.session_name)
        if new_tabs:
            self.tabs.setCurrentWidget(new_tabs[-1])

    def close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget:
            self._activity.pop(widget, None)
            terminals = getattr(widget, "terminals", None)
            if terminals is not None:
                for term in terminals:
                    self._stop_terminal(term)
            else:
                self._stop_terminal(widget)
            self.tabs.removeTab(index)
            widget.deleteLater()

    # Common tasks -> (id, label, default key sequence, handler method name)
    SHORTCUT_DEFS = [
        ("new_session",    "New Session",                 "Ctrl+N",       "show_add_session_dialog"),
        ("home_terminal",  "New Home Terminal",           "Ctrl+H",       "open_home_terminal"),
        ("local_terminal", "New Local Terminal",          "Ctrl+T",       "new_local_terminal"),
        ("next_tab",       "Switch to Next Tab",          "Ctrl+Shift+Z", "next_tab"),
        ("prev_tab",       "Switch to Previous Tab",      "Ctrl+Shift+A", "prev_tab"),
        ("close_tab",      "Close Current Tab",           "Ctrl+W",       "close_current_tab"),
        ("rename_tab",     "Rename Current Tab",          "F2",           "rename_current_tab"),
        ("split_2h",       "Split: 2 Panes (Horizontal)", "",             "split_2h"),
        ("split_2v",       "Split: 2 Panes (Vertical)",   "",             "split_2v"),
        ("split_4",        "Split: 4 Panes",              "",             "split_4"),
        ("unsplit",        "Unsplit Current Tab",         "",             "unsplit_current_tab"),
        ("save_layout",    "Save Current Layout",         "Ctrl+Shift+S", "show_save_layout_dialog"),
        ("open_layout",    "Open Layout",                 "Ctrl+Shift+O", "show_open_layout_dialog"),
        ("shortcuts",      "Keyboard Shortcuts...",       "Ctrl+K",       "show_shortcuts_dialog"),
    ]

    def setup_shortcuts(self):
        from omniterm.core.config import get_shortcuts
        # Remove any previously-built shortcuts (for rebuild after editing)
        for sc in getattr(self, "_shortcuts", []):
            sc.setParent(None)
            sc.deleteLater()
        self._shortcuts = []

        overrides = get_shortcuts()
        for sid, _label, default, handler_name in self.SHORTCUT_DEFS:
            seq = overrides.get(sid, default)
            if not seq:
                continue  # unbound
            handler = getattr(self, handler_name, None)
            if handler is None:
                continue
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(handler)
            self._shortcuts.append(sc)

    # ---- Shortcut action handlers ----
    def new_local_terminal(self):
        self.create_terminal_tab("local", {"name": "Local Terminal"})

    def next_tab(self):
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % n)

    def prev_tab(self):
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % n)

    def close_current_tab(self):
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self.close_tab(idx)

    def rename_current_tab(self):
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self.rename_tab(idx)

    def split_2h(self):
        self.show_split_view_dialog("2h")

    def split_2v(self):
        self.show_split_view_dialog("2v")

    def split_4(self):
        self.show_split_view_dialog("4")

    def show_shortcuts_dialog(self):
        from omniterm.core.config import get_shortcuts, set_shortcuts
        overrides = get_shortcuts()

        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        form = QFormLayout(dialog)

        editors = {}
        for sid, label, default, _handler in self.SHORTCUT_DEFS:
            editor = QKeySequenceEdit(QKeySequence(overrides.get(sid, default)))
            editors[sid] = editor
            form.addRow(label, editor)

        def save():
            mapping = {}
            for sid, _label, default, _h in self.SHORTCUT_DEFS:
                mapping[sid] = editors[sid].keySequence().toString()
            set_shortcuts(mapping)
            self.setup_shortcuts()  # rebuild live
            dialog.accept()

        def reset():
            for sid, _label, default, _h in self.SHORTCUT_DEFS:
                editors[sid].setKeySequence(QKeySequence(default))

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(save)
        btn_reset = QPushButton("Reset to Defaults")
        btn_reset.clicked.connect(reset)
        form.addRow(btn_reset, btn_save)

        dialog.exec()

    def show_sftp_error(self, msg):
        # Route SFTP errors to the current terminal tab if possible, or a status bar
        current_tab = self.tabs.currentWidget()
        if current_tab and hasattr(current_tab, 'bridge'):
            current_tab.bridge.onDataReceived.emit(f"\r\n\x1b[31m[SFTP ERROR]: {msg}\x1b[0m\r\n")
        else:
            print(f"SFTP Error: {msg}")

    def show_set_home_dir_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Persistent Home Directory")
        layout = QFormLayout(dialog)

        current_dir = QLineEdit(str(HOME_DIR))
        layout.addRow("Current Home Directory:", current_dir)
        
        # Use a button to pick a directory
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(lambda: self.browse_home_dir(current_dir))
        layout.addRow(btn_browse)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(lambda: self.save_home_dir(current_dir.text(), dialog))
        layout.addRow(btn_save)
        
        dialog.exec()

    def browse_home_dir(self, line_edit):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Home Directory", str(HOME_DIR))
        if dir_path:
            line_edit.setText(dir_path)

    def save_home_dir(self, path, dialog):
        set_home_dir(path)
        # We don't restart the app, but we inform the user
        QMessageBox.information(self, "Home Directory Updated", 
                               f"Home directory set to: {path}\n\nChanges will take effect after restart.")
        dialog.accept()

    def show_set_shared_sessions_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Shared Sessions File")
        layout = QFormLayout(dialog)

        file_edit = QLineEdit()
        layout.addRow("Sessions File Path:", file_edit)
        
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(lambda: self.browse_shared_file(file_edit))
        layout.addRow(btn_browse)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(lambda: self.save_shared_file(file_edit.text(), dialog))
        layout.addRow(btn_save)
        
        dialog.exec()

    def browse_shared_file(self, line_edit):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Sessions File", "", "JSON Files (*.json)")
        if file_path:
            line_edit.setText(file_path)

    def save_shared_file(self, path, dialog):
        set_shared_sessions_file(path)
        QMessageBox.information(self, "Shared Sessions Updated", 
                               f"Now using shared sessions file: {path}")
        self.session_dock.load_sessions_into_tree()
        dialog.accept()

    def show_plugin_manager_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Plugin Manager")
        layout = QVBoxLayout(dialog)
        
        from omniterm.core.config import load_plugins
        plugins = load_plugins()
        
        plugin_list = QLineEdit(f"Installed: {', '.join(plugins) if plugins else 'None'}")
        plugin_list.setReadOnly(True)
        layout.addWidget(plugin_list)
        
        btn_install = QPushButton("Install New Plugin (Simulated)")
        btn_install.clicked.connect(lambda: QMessageBox.information(dialog, "Plugins", "Plugin installation would happen here via MobApt-like system."))
        layout.addWidget(btn_install)
        
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.exec()

    def show_terminal_appearance_dialog(self):
        settings = get_terminal_settings()

        dialog = QDialog(self)
        dialog.setWindowTitle("Terminal Appearance")
        layout = QFormLayout(dialog)

        # Font size
        size_spin = QSpinBox()
        size_spin.setRange(6, 48)
        size_spin.setValue(int(settings.get("fontSize", 14)))
        layout.addRow("Font Size:", size_spin)

        # Colors held in a mutable dict so the picker callbacks can update them
        colors = {
            "foreground": settings.get("foreground", "#ffffff"),
            "background": settings.get("background", "#1e1e1e"),
        }

        def make_color_row(label, key):
            btn = QPushButton(colors[key])

            def update_btn():
                btn.setText(colors[key])
                btn.setStyleSheet(f"background-color: {colors[key]}; color: #000;")

            def pick():
                chosen = QColorDialog.getColor(QColor(colors[key]), dialog, f"Select {label}")
                if chosen.isValid():
                    colors[key] = chosen.name()
                    update_btn()

            btn.clicked.connect(pick)
            update_btn()
            layout.addRow(f"{label}:", btn)

        make_color_row("Text Color", "foreground")
        make_color_row("Background Color", "background")

        def save():
            new_settings = {
                "fontSize": size_spin.value(),
                "foreground": colors["foreground"],
                "background": colors["background"],
            }
            set_terminal_settings(new_settings)
            self.apply_terminal_settings_to_open_tabs()
            dialog.accept()

        btn_save = QPushButton("Apply")
        btn_save.clicked.connect(save)
        layout.addRow(btn_save)

        dialog.exec()

    def export_sessions_to_file(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Sessions", "omniterm_sessions.json", "JSON Files (*.json)")
        if not file_path:
            return

        include = QMessageBox.question(
            self, "Include Passwords?",
            "Include saved passwords in the export?\n\n"
            "Yes: passwords are included (still encrypted; only usable with your "
            "master password / key on this machine).\n"
            "No: passwords are stripped (recommended for sharing or backup).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

        try:
            export_sessions(file_path, include_secrets=include)
            QMessageBox.information(self, "Export Complete", f"Sessions exported to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def import_sessions_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Sessions", "", "JSON Files (*.json)")
        if not file_path:
            return
        try:
            count = import_sessions(file_path)
            self.session_dock.load_sessions_into_tree()
            QMessageBox.information(
                self, "Import Complete",
                f"Imported {count} session(s) from:\n{file_path}\n\n"
                "Any sessions whose passwords were stripped on export will need "
                "the password re-entered.")
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))

    def apply_terminal_settings_to_open_tabs(self):
        settings = get_terminal_settings()
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            terminals = getattr(widget, "terminals", None)
            if terminals is not None:
                for term in terminals:
                    if hasattr(term, "apply_settings"):
                        term.apply_settings(settings)
            elif hasattr(widget, "apply_settings"):
                widget.apply_settings(settings)

    GITHUB_URL = "https://github.com/fbobe321/omniterm"

    @staticmethod
    def app_version():
        try:
            from importlib.metadata import version
            return version("omniterm")
        except Exception:
            return "unknown"

    def open_github(self):
        QDesktopServices.openUrl(QUrl(self.GITHUB_URL))

    def show_about_dialog(self):
        version = self.app_version()
        box = QMessageBox(self)
        box.setWindowTitle("About OmniTerm")
        box.setTextFormat(Qt.TextFormat.RichText)  # makes the link clickable
        box.setText(
            f"<h3>OmniTerm</h3>"
            f"<p>Version <b>{version}</b></p>"
            f"<p>A cross-platform terminal: SSH, serial, and local sessions "
            f"with an integrated SFTP browser.</p>"
            f"<p>For support, issues, and documentation:<br>"
            f"<a href='{self.GITHUB_URL}'>{self.GITHUB_URL}</a></p>"
        )
        box.exec()

    def show_shell_integration_dialog(self):
        QMessageBox.information(self, "Shell Integration",
                               "To integrate Omniterm into Windows Explorer:\n\n1. Open Registry Editor (regedit)\n2. Navigate to HKEY_CLASSES_ROOT\\Directory\\shell\n3. Create a key 'OmniTerm'\n4. Set (Default) to 'Open in OmniTerm'\n5. Create a subkey 'command' and set (Default) to '\"C:\\Path\\To\\omniterm.exe\" \"%1\"'")
        
    def show_set_master_password_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Master Password")
        layout = QFormLayout(dialog)

        pass_edit = QLineEdit()
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Master Password:", pass_edit)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(lambda: self.save_master_password(pass_edit.text(), dialog))
        layout.addRow(btn_save)
        
        dialog.exec()

    def save_master_password(self, password, dialog):
        init_cipher(password)
        QMessageBox.information(self, "Master Password Updated", 
                               "Master password has been set for the current session.")
        dialog.accept()

    def show_edit_session_dialog(self, session_data):
        self.show_add_session_dialog(existing=session_data)

    def show_add_session_dialog(self, existing=None):
        editing = isinstance(existing, dict)
        existing = existing if editing else {}

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Session" if editing else "Add Session")
        layout = QFormLayout(dialog)

        name_edit = QLineEdit(existing.get("name", ""))
        type_combo = QComboBox()
        type_combo.addItems(["ssh", "serial", "local", "home"])
        type_combo.setCurrentText(existing.get("type", "ssh"))

        layout.addRow("Name:", name_edit)
        layout.addRow("Type:", type_combo)

        # SSH Fields
        ssh_container = QWidget()
        ssh_layout = QFormLayout(ssh_container)
        host_edit = QLineEdit(existing.get("host", ""))
        user_edit = QLineEdit(existing.get("user", ""))
        port_edit = QLineEdit(str(existing.get("port", 22)))
        auth_combo = QComboBox()
        auth_combo.addItems(["password", "key"])
        auth_combo.setCurrentText(existing.get("auth_method", "password"))
        pass_edit = QLineEdit()
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if editing and existing.get("password"):
            pass_edit.setPlaceholderText("(unchanged - leave blank to keep)")
        key_edit = QLineEdit(existing.get("key_path", ""))

        # Tunneling Fields
        tunnel_edit = QLineEdit(self.tunnels_to_str(existing.get("tunnels", [])))
        tunnel_edit.setPlaceholderText("local_port:remote_host:remote_port (comma separated)")

        # Startup Script Field
        startup_edit = QLineEdit(existing.get("startup_script", ""))
        startup_edit.setPlaceholderText("Command to run on startup")

        # X11 Forwarding
        x11_check = QCheckBox("Enable X11 forwarding (run remote GUI apps locally)")
        x11_check.setChecked(bool(existing.get("x11", False)))

        ssh_layout.addRow("Host:", host_edit)
        ssh_layout.addRow("User:", user_edit)
        ssh_layout.addRow("Port:", port_edit)
        ssh_layout.addRow("Auth Method:", auth_combo)
        ssh_layout.addRow("Password:", pass_edit)
        ssh_layout.addRow("Key Path:", key_edit)
        ssh_layout.addRow("Tunnels:", tunnel_edit)
        ssh_layout.addRow("Startup Script:", startup_edit)
        ssh_layout.addRow("X11:", x11_check)
        layout.addRow(ssh_container)

        # Serial Fields
        serial_container = QWidget()
        serial_layout = QFormLayout(serial_container)
        com_edit = QLineEdit(existing.get("com_port", ""))
        baud_edit = QLineEdit(str(existing.get("baud_rate", 115200)))
        data_bits_combo = QComboBox()
        data_bits_combo.addItems(["5", "6", "7", "8"])
        data_bits_combo.setCurrentText(str(existing.get("data_bits", 8)))
        parity_combo = QComboBox()
        parity_combo.addItems(["N", "E", "O", "M"])
        parity_combo.setCurrentText(existing.get("parity", "N"))
        stop_bits_combo = QComboBox()
        stop_bits_combo.addItems(["1", "1.5", "2"])
        stop_bits_combo.setCurrentText(str(existing.get("stop_bits", 1)).rstrip("0").rstrip(".") or "1")

        serial_layout.addRow("COM Port:", com_edit)
        serial_layout.addRow("Baud Rate:", baud_edit)
        serial_layout.addRow("Data Bits:", data_bits_combo)
        serial_layout.addRow("Parity:", parity_combo)
        serial_layout.addRow("Stop Bits:", stop_bits_combo)
        layout.addRow(serial_container)

        # Visibility Logic
        def update_visibility():
            stype = type_combo.currentText()
            ssh_container.setVisible(stype == "ssh")
            serial_container.setVisible(stype == "serial")
            if stype == "ssh":
                pass_edit.setVisible(auth_combo.currentText() == "password")
                key_edit.setVisible(auth_combo.currentText() == "key")

        type_combo.currentTextChanged.connect(update_visibility)
        auth_combo.currentTextChanged.connect(update_visibility)
        update_visibility()

        btn = QPushButton("Save")
        btn.clicked.connect(lambda: self.save_new_session(
            existing.get("id") if editing else None,
            name_edit.text(),
            type_combo.currentText(),
            host_edit.text(),
            user_edit.text(),
            port_edit.text(),
            auth_combo.currentText(),
            pass_edit.text(),
            key_edit.text(),
            tunnel_edit.text(),
            startup_edit.text(),
            x11_check.isChecked(),
            com_edit.text(),
            baud_edit.text(),
            data_bits_combo.currentText(),
            parity_combo.currentText(),
            stop_bits_combo.currentText(),
            dialog
        ))
        layout.addRow(btn)
        dialog.exec()

    @staticmethod
    def tunnels_to_str(tunnels):
        return ",".join(
            f"{t.get('local_port')}:{t.get('remote_host')}:{t.get('remote_port')}"
            for t in (tunnels or [])
        )

    def save_new_session(self, session_id, name, stype, host, user, port, auth_method, password, key_path, tunnel, startup, x11, com, baud, data_bits, parity, stop_bits, dialog):
        from omniterm.core.config import load_sessions, save_sessions, encrypt_password, update_session, find_session
        import uuid

        prior = find_session(session_id) if session_id else None

        session = {
            "id": session_id or str(uuid.uuid4()),
            "name": name,
            "type": stype,
        }

        if stype == "ssh":
            session.update({
                "host": host,
                "user": user,
                "port": int(port) if port.isdigit() else 22,
                "auth_method": auth_method
            })
            if auth_method == "password":
                if password:
                    session["password"] = encrypt_password(password)
                elif prior and prior.get("password"):
                    session["password"] = prior["password"]  # keep existing
            elif auth_method == "key":
                session["key_path"] = key_path

            tunnels = self.parse_tunnels(tunnel)
            if tunnels:
                session["tunnels"] = tunnels
            if startup.strip():
                session["startup_script"] = startup.strip()
            if x11:
                session["x11"] = True
        elif stype == "serial":
            session.update({
                "com_port": com,
                "baud_rate": int(baud) if baud.isdigit() else 115200,
                "data_bits": int(data_bits),
                "parity": parity,
                "stop_bits": float(stop_bits)
            })

        if session_id and update_session(session):
            pass  # updated in place
        else:
            data = load_sessions()
            data.setdefault("sessions", []).append(session)
            save_sessions(data)

        self.session_dock.load_sessions_into_tree()
        dialog.accept()

    def parse_tunnels(self, text):
        """Parse 'local_port:remote_host:remote_port' entries (comma separated)
        into a list of tunnel config dicts. Malformed entries are skipped."""
        tunnels = []
        for entry in text.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) != 3:
                continue
            local_port, remote_host, remote_port = (p.strip() for p in parts)
            if not (local_port.isdigit() and remote_port.isdigit()):
                continue
            tunnels.append({
                "local_port": int(local_port),
                "remote_host": remote_host,
                "remote_port": int(remote_port),
            })
        return tunnels
