import os
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QVBoxLayout, QWidget, QDialog, QFormLayout, QLineEdit, QPushButton, QComboBox, QFileDialog, QMessageBox, QSpinBox, QColorDialog
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from omniterm.ui.session_dock import SessionDock
from omniterm.ui.terminal_tab import TerminalTab
from omniterm.ui.sftp_browser import SFTPBrowser
from omniterm.core.ssh_client import SSHWorker
from omniterm.core.serial_client import SerialWorker
from omniterm.core.local_pty import LocalPTYWorker
from omniterm.core.config import HOME_DIR, set_home_dir, init_cipher, set_shared_sessions_file, get_terminal_settings, set_terminal_settings

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

        # Session Dock
        self.session_dock = SessionDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.session_dock)

        # SFTP Browser
        self.sftp_browser = SFTPBrowser(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sftp_browser)

        # Connect session selection to tab creation
        self.session_dock.tree_view.clicked.connect(self.on_session_selected)

        # Setup Keyboard Shortcuts
        self.setup_shortcuts()

        # Add a simple menu for session management
        self.menu_bar = self.menuBar()
        self.session_menu = self.menu_bar.addMenu("&Sessions")
        self.add_session_action = self.session_menu.addAction("Add Session")
        self.add_session_action.triggered.connect(self.show_add_session_dialog)

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

        # Warn if running elevated on Windows (blocks drag/drop from Explorer)
        self._warn_if_elevated()

    def _warn_if_elevated(self):
        if os.name != "nt":
            return
        try:
            import ctypes
            if ctypes.windll.shell32.IsUserAnAdmin():
                QMessageBox.warning(
                    self,
                    "Running as Administrator",
                    "OmniTerm is running elevated (as Administrator).\n\n"
                    "Windows blocks drag-and-drop from a normal Explorer window into "
                    "an elevated application. To drag files in, restart OmniTerm from a "
                    "normal (non-Administrator) terminal.\n\n"
                    "The right-click Upload/Download menu works either way."
                )
        except Exception:
            pass

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

    def create_terminal_tab(self, session_type, session_data):
        session_name = session_data.get("name", "Unnamed Session")

        tab = TerminalTab(session_name)
        tab.apply_settings(get_terminal_settings())
        self.tabs.addTab(tab, session_name)
        self.tabs.setCurrentWidget(tab)

        # Worker Factory
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
                worker.auth_success.connect(lambda: self.sftp_browser.connect_sftp(worker))
                self.sftp_browser.error_occurred.connect(lambda msg: self.show_sftp_error(msg))
            worker.start()

        return tab

    def close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget:
            if hasattr(widget, 'worker') and widget.worker:
                worker = widget.worker
                # Disconnect signals to prevent leaks
                try:
                    worker.data_received.disconnect()
                    worker.error_occurred.disconnect()
                    if hasattr(worker, 'auth_success'):
                        worker.auth_success.disconnect()
                except (TypeError, RuntimeError):
                    # Signal might already be disconnected or worker is already dead
                    pass
                
                worker.stop()
                worker.wait()
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

    def apply_terminal_settings_to_open_tabs(self):
        settings = get_terminal_settings()
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if hasattr(widget, "apply_settings"):
                widget.apply_settings(settings)

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

    def show_add_session_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Session")
        layout = QFormLayout(dialog)

        name_edit = QLineEdit()
        type_combo = QComboBox()
        type_combo.addItems(["ssh", "serial", "local"])

        layout.addRow("Name:", name_edit)
        layout.addRow("Type:", type_combo)

        # SSH Fields
        ssh_container = QWidget()
        ssh_layout = QFormLayout(ssh_container)
        host_edit = QLineEdit()
        user_edit = QLineEdit()
        port_edit = QLineEdit("22")
        auth_combo = QComboBox()
        auth_combo.addItems(["password", "key"])
        pass_edit = QLineEdit()
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit = QLineEdit()
        
        # Tunneling Fields
        tunnel_edit = QLineEdit()
        tunnel_edit.setPlaceholderText("local_port:remote_host:remote_port (comma separated)")
        
        # Startup Script Field
        startup_edit = QLineEdit()
        startup_edit.setPlaceholderText("Command to run on startup")
        
        ssh_layout.addRow("Host:", host_edit)
        ssh_layout.addRow("User:", user_edit)
        ssh_layout.addRow("Port:", port_edit)
        ssh_layout.addRow("Auth Method:", auth_combo)
        ssh_layout.addRow("Password:", pass_edit)
        ssh_layout.addRow("Key Path:", key_edit)
        ssh_layout.addRow("Tunnels:", tunnel_edit)
        ssh_layout.addRow("Startup Script:", startup_edit)
        layout.addRow(ssh_container)

        # Serial Fields
        serial_container = QWidget()
        serial_layout = QFormLayout(serial_container)
        com_edit = QLineEdit()
        baud_edit = QLineEdit("115200")
        data_bits_combo = QComboBox()
        data_bits_combo.addItems(["5", "6", "7", "8"])
        data_bits_combo.setCurrentText("8")
        parity_combo = QComboBox()
        parity_combo.addItems(["N", "E", "O", "M"])
        stop_bits_combo = QComboBox()
        stop_bits_combo.addItems(["1", "1.5", "2"])
        stop_bits_combo.setCurrentText("1")

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
            com_edit.text(),
            baud_edit.text(),
            data_bits_combo.currentText(),
            parity_combo.currentText(),
            stop_bits_combo.currentText(),
            dialog
        ))
        layout.addRow(btn)
        dialog.exec()

    def save_new_session(self, name, stype, host, user, port, auth_method, password, key_path, tunnel, startup, com, baud, data_bits, parity, stop_bits, dialog):
        from omniterm.core.config import load_sessions, save_sessions, encrypt_password
        import uuid

        data = load_sessions()
        new_session = {
            "id": str(uuid.uuid4()),
            "name": name,
            "type": stype,
        }

        if stype == "ssh":
            new_session.update({
                "host": host,
                "user": user,
                "port": int(port) if port.isdigit() else 22,
                "auth_method": auth_method
            })
            if auth_method == "password" and password:
                new_session["password"] = encrypt_password(password)
            elif auth_method == "key":
                new_session["key_path"] = key_path

            tunnels = self.parse_tunnels(tunnel)
            if tunnels:
                new_session["tunnels"] = tunnels
            if startup.strip():
                new_session["startup_script"] = startup.strip()
        elif stype == "serial":
            new_session.update({
                "com_port": com, 
                "baud_rate": int(baud) if baud.isdigit() else 115200,
                "data_bits": int(data_bits),
                "parity": parity,
                "stop_bits": float(stop_bits)
            })

        data["sessions"].append(new_session)
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
