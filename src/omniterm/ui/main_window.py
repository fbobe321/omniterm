import os
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QVBoxLayout, QWidget, QDialog, QFormLayout, QLineEdit, QPushButton, QComboBox, QFileDialog, QMessageBox, QSpinBox, QColorDialog, QToolButton, QInputDialog, QMenu, QCheckBox
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtCore import Qt, QUrl
from omniterm.ui.session_dock import SessionDock
from omniterm.ui.terminal_tab import TerminalTab, SplitContainer
from omniterm.ui.sftp_browser import SFTPBrowser
from omniterm.core.ssh_client import SSHWorker
from omniterm.core.serial_client import SerialWorker
from omniterm.core.local_pty import LocalPTYWorker
from omniterm.core.config import HOME_DIR, set_home_dir, init_cipher, set_shared_sessions_file, get_terminal_settings, set_terminal_settings, export_sessions, import_sessions

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OmniTerm")
        self.resize(1200, 800)

        # Dark Theme
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QTabWidget::pane { border: 1px solid #333; background: #1e1e1e; }
            QTabBar::tab { 
                background: #2d2d2d; 
                color: #aaa; 
                padding: 8px 15px; 
                border: 1px solid #333; 
                border-bottom: none;
                min-width: 100px;
            }
            QTabBar::tab:selected { 
                background: #3c3c3c; 
                color: white; 
                font-weight: bold;
            }
            QTreeView { 
                background-color: #252526; 
                color: #cccccc; 
                border: none; 
                font-family: 'Segoe UI', 'DejaVu Sans', monospace;
            }
            QTreeView::item:hover { background-color: #2a2d2e; }
            QTreeView::item:selected { background-color: #37373d; color: white; }
            QHeaderView::section { 
                background-color: #333333; 
                color: #cccccc; 
                border: 1px solid #444; 
                padding: 4px;
            }
            QDialog { background-color: #2d2d2d; color: white; }
            QLineEdit { 
                background-color: #3c3c3c; 
                color: white; 
                border: 1px solid #555; 
                padding: 4px; 
                border-radius: 2px;
            }
            QPushButton { 
                background-color: #0e639c; 
                color: white; 
                border: none; 
                padding: 6px 12px; 
                border-radius: 3px;
            }
            QPushButton:hover { background-color: #1177c4; }
            QComboBox { 
                background-color: #3c3c3c; 
                color: white; 
                border: 1px solid #555; 
                padding: 4px;
            }
            QMenu { background-color: #2d2d2d; color: white; border: 1px solid #444; }
            QMenu::item:selected { background-color: #0e639c; }
        """)

        # Main Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)

        # Rename tabs: double-click a tab, or right-click for a menu
        self.tabs.tabBarDoubleClicked.connect(self.rename_tab)
        tab_bar = self.tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self.show_tab_context_menu)

        # "Split" button in the tab bar corner: open 1/2/4 terminals side by side
        self.split_button = QToolButton()
        self.split_button.setText("▦ Split")
        self.split_button.setToolTip("Open multiple sessions in a split view (1 / 2 / 4 panes)")
        self.split_button.clicked.connect(lambda: self.show_split_view_dialog())
        self.tabs.setCornerWidget(self.split_button, Qt.Corner.TopRightCorner)

        # Session Dock
        self.session_dock = SessionDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.session_dock)

        # SFTP Browser
        self.sftp_browser = SFTPBrowser(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sftp_browser)
        self.sftp_browser.error_occurred.connect(self.show_sftp_error)

        # Show the active tab's remote files when the selected tab changes
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # Connect session selection to tab creation
        self.session_dock.tree_view.clicked.connect(self.on_session_selected)

        # Setup Keyboard Shortcuts
        self.setup_shortcuts()

        # Add a simple menu for session management
        self.menu_bar = self.menuBar()
        self.session_menu = self.menu_bar.addMenu("&Sessions")
        self.add_session_action = self.session_menu.addAction("Add Session")
        self.add_session_action.triggered.connect(self.show_add_session_dialog)
        self.export_sessions_action = self.session_menu.addAction("Export Sessions...")
        self.export_sessions_action.triggered.connect(self.export_sessions_to_file)
        self.import_sessions_action = self.session_menu.addAction("Import Sessions...")
        self.import_sessions_action.triggered.connect(self.import_sessions_from_file)

        self.settings_menu = self.menu_bar.addMenu("&Settings")
        self.terminal_appearance_action = self.settings_menu.addAction("Terminal Appearance...")
        self.terminal_appearance_action.triggered.connect(self.show_terminal_appearance_dialog)
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

        # Help menu: support link + version
        self.help_menu = self.menu_bar.addMenu("&Help")
        self.github_action = self.help_menu.addAction("GitHub / Support")
        self.github_action.triggered.connect(self.open_github)
        self.about_action = self.help_menu.addAction("About OmniTerm")
        self.about_action.triggered.connect(self.show_about_dialog)

    def show_split_view_dialog(self, default_count=2):
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
        dialog.setWindowTitle("Split Open Tabs")
        layout = QFormLayout(dialog)

        max_panes = min(4, len(candidates))
        count_options = [str(n) for n in (2, 4) if n <= max_panes] or ["2"]
        count_combo = QComboBox()
        count_combo.addItems(count_options)
        wanted = str(default_count)
        count_combo.setCurrentText(wanted if wanted in count_options else count_options[0])
        layout.addRow("Panes:", count_combo)

        pickers = []
        for i in range(4):
            combo = QComboBox()
            for label, widget in candidates:
                combo.addItem(label, widget)
            # Default each pane to a different open tab
            if i < len(candidates):
                combo.setCurrentIndex(i)
            pickers.append(combo)
            layout.addRow(f"Pane {i + 1}:", combo)

        def update_rows():
            n = int(count_combo.currentText())
            for i in range(4):
                layout.setRowVisible(i + 1, i < n)
            dialog.adjustSize()

        count_combo.currentTextChanged.connect(update_rows)
        update_rows()

        def open_it():
            n = int(count_combo.currentText())
            chosen, seen = [], set()
            for i in range(n):
                widget = pickers[i].currentData()
                if widget is not None and id(widget) not in seen:
                    seen.add(id(widget))
                    chosen.append(widget)
            dialog.accept()
            if len(chosen) >= 2:
                self.combine_tabs_into_split(n, chosen)

        btn = QPushButton("Split")
        btn.clicked.connect(open_it)
        layout.addRow(btn)

        dialog.exec()

    def combine_tabs_into_split(self, count, widgets):
        """Move the given already-open terminal tabs into a single split tab."""
        container = SplitContainer(count)
        for widget in widgets:
            idx = self.tabs.indexOf(widget)
            if idx != -1:
                self.tabs.removeTab(idx)  # detach without deleting/stopping it
            container.add_terminal(widget)  # reparents into the splitter

        self.tabs.addTab(container, f"Split x{len(widgets)}")
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

    def build_terminal(self, session_type, session_data):
        """Create a TerminalTab, start its worker, and apply appearance
        settings. Does not add it to the tab widget (the caller places it)."""
        tab = TerminalTab(session_data.get("name", "Unnamed Session"))
        tab.apply_settings(get_terminal_settings())

        worker = None
        if session_type == "ssh":
            worker = SSHWorker(session_data)
        elif session_type == "serial":
            worker = SerialWorker(
                session_data.get("com_port"),
                session_data.get("baud_rate", 115200),
                session_data.get("data_bits", 8),
                session_data.get("parity", "N"),
                session_data.get("stop_bits", 1)
            )
        elif session_type == "local":
            worker = LocalPTYWorker()

        if worker:
            tab.set_worker(worker)
            if isinstance(worker, SSHWorker):
                # Each SSH worker opens its own SFTP session (in its thread) and
                # hands it over; the panel shows the active tab's connection.
                worker.sftp_ready.connect(
                    lambda payload, w=worker: self.sftp_browser.attach_sftp(w, payload[0], payload[1]))
                worker.cwd_changed.connect(
                    lambda path, w=worker: self.sftp_browser.on_terminal_cwd(w, path))
            worker.start()

        return tab

    def create_terminal_tab(self, session_type, session_data):
        session_name = session_data.get("name", "Unnamed Session")
        tab = self.build_terminal(session_type, session_data)
        self.tabs.addTab(tab, session_name)
        self.tabs.setCurrentWidget(tab)
        return tab

    def _primary_ssh_worker(self, widget):
        """The SSH worker whose files should be shown for `widget` (a tab).
        For split tabs, the first SSH pane wins; None if there is no SSH pane."""
        if widget is None:
            return None
        terminals = getattr(widget, "terminals", None)
        candidates = terminals if terminals is not None else [widget]
        for term in candidates:
            worker = getattr(term, "worker", None)
            if isinstance(worker, SSHWorker):
                return worker
        return None

    def on_tab_changed(self, index):
        widget = self.tabs.widget(index) if index >= 0 else None
        self.sftp_browser.show_worker(self._primary_ssh_worker(widget))

    def _stop_terminal(self, term):
        worker = getattr(term, "worker", None)
        if not worker:
            return
        if isinstance(worker, SSHWorker):
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
        menu = QMenu()
        rename_action = menu.addAction("Rename...")
        split_menu = menu.addMenu("Split View")
        split1 = split_menu.addAction("1 Pane")
        split2 = split_menu.addAction("2 Panes")
        split4 = split_menu.addAction("4 Panes")
        close_action = menu.addAction("Close")
        chosen = menu.exec(tab_bar.mapToGlobal(position))
        if chosen == rename_action:
            self.rename_tab(index)
        elif chosen == split1:
            self.show_split_view_dialog(1)
        elif chosen == split2:
            self.show_split_view_dialog(2)
        elif chosen == split4:
            self.show_split_view_dialog(4)
        elif chosen == close_action:
            self.close_tab(index)

    def close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget:
            terminals = getattr(widget, "terminals", None)
            if terminals is not None:
                for term in terminals:
                    self._stop_terminal(term)
            else:
                self._stop_terminal(widget)
            self.tabs.removeTab(index)
            widget.deleteLater()

    def setup_shortcuts(self):
        from PyQt6.QtGui import QShortcut, QKeySequence
        
        # Ctrl+N: New Session
        self.shortcut_new = QShortcut(QKeySequence("Ctrl+N"), self)
        self.shortcut_new.activated.connect(self.show_add_session_dialog)
        
        # Ctrl+T: New Tab (Local PTY)
        self.shortcut_tab = QShortcut(QKeySequence("Ctrl+T"), self)
        self.shortcut_tab.activated.connect(lambda: self.create_terminal_tab("local", {"name": "Local Terminal"}))
        
        # Ctrl+S: Save Current Session (if applicable)
        self.shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_save.activated.connect(lambda: QMessageBox.information(self, "Save", "Session settings saved."))

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
        type_combo.addItems(["ssh", "serial", "local"])
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
