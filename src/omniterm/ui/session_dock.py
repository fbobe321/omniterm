from PyQt6.QtWidgets import QDockWidget, QTreeView
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6.QtCore import Qt
from omniterm.core.config import load_sessions

class SessionDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Sessions", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        self.tree_view = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Sessions"])
        self.tree_view.setModel(self.model)

        self.setWidget(self.tree_view)
        self.load_sessions_into_tree()

    def load_sessions_into_tree(self):
        data = load_sessions()
        sessions = data.get("sessions", [])

        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Sessions"])

        def add_session_recursive(parent_item, session_list):
            for s in session_list:
                if s.get("type") == "folder":
                    folder_node = QStandardItem(s.get("name", "Unnamed Folder"))
                    folder_node.setData(s, 32)
                    parent_item.appendRow(folder_node)
                    add_session_recursive(folder_node, s.get("children", []))
                else:
                    session_node = QStandardItem(s.get("name", "Unnamed Session"))
                    session_node.setData(s, 32)
                    parent_item.appendRow(session_node)

        root = QStandardItem("All Sessions")
        add_session_recursive(root, sessions)
        self.model.appendRow(root)
