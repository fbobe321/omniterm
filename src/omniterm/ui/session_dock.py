from PyQt6.QtWidgets import QDockWidget, QTreeView, QMenu, QMessageBox
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QAction
from PyQt6.QtCore import Qt, QSize
from omniterm.core.config import load_sessions, delete_session
from omniterm.ui.icons import get_icon, session_icon

class SessionDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("SESSIONS", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        self.tree_view = QTreeView()
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setIconSize(QSize(18, 18))
        self.tree_view.setHeaderHidden(True)  # dock title already says SESSIONS
        self.model = QStandardItemModel()
        self.tree_view.setModel(self.model)
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self.show_context_menu)

        self.setWidget(self.tree_view)
        self.load_sessions_into_tree()

    def show_context_menu(self, position):
        index = self.tree_view.indexAt(position)
        if not index.isValid():
            return
        item = self.model.itemFromIndex(index)
        session_data = item.data(32) if item else None
        if not session_data or not session_data.get("id"):
            return  # not a real session/folder node (e.g. the "All Sessions" root)

        menu = QMenu()
        if session_data.get("type") != "folder":
            edit_action = QAction("Edit Session", self)
            edit_action.triggered.connect(lambda: self._edit_session(session_data))
            menu.addAction(edit_action)
        delete_action = QAction("Delete Session", self)
        delete_action.triggered.connect(lambda: self.delete_session(session_data))
        menu.addAction(delete_action)
        menu.exec(self.tree_view.viewport().mapToGlobal(position))

    def _edit_session(self, session_data):
        window = self.window()
        if hasattr(window, "show_edit_session_dialog"):
            window.show_edit_session_dialog(session_data)

    def delete_session(self, session_data):
        name = session_data.get("name", "this session")
        is_folder = session_data.get("type") == "folder"
        prompt = (f"Delete folder '{name}' and all sessions inside it?"
                  if is_folder else f"Delete session '{name}'?")
        reply = QMessageBox.question(
            self, "Confirm Delete", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if delete_session(session_data.get("id")):
                self.load_sessions_into_tree()

    def load_sessions_into_tree(self):
        data = load_sessions()
        sessions = data.get("sessions", [])

        self.model.clear()

        def add_session_recursive(parent, session_list):
            for s in session_list:
                if s.get("type") == "folder":
                    folder_node = QStandardItem(s.get("name", "Unnamed Folder"))
                    folder_node.setData(s, 32)
                    folder_node.setIcon(get_icon("folder"))
                    parent.appendRow(folder_node)
                    add_session_recursive(folder_node, s.get("children", []))
                else:
                    session_node = QStandardItem(s.get("name", "Unnamed Session"))
                    session_node.setData(s, 32)
                    session_node.setIcon(session_icon(s.get("type", "ssh")))
                    parent.appendRow(session_node)

        # Add sessions directly at the top level (no "All Sessions" wrapper)
        add_session_recursive(self.model.invisibleRootItem(), sessions)

        # Expand everything so sessions are visible without manual expanding
        self.tree_view.expandAll()
