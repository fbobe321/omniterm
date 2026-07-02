# Central dark theme for OmniTerm. Charcoal zones, 1px borders, curved tabs,
# colorful accents, and readable tree/list styling.

APP_STYLESHEET = """
QMainWindow, QWidget#central { background-color: #16181d; }

/* ---- Top toolbar (ribbon) ---- */
QToolBar#ribbon {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a2e36, stop:1 #23262d);
    border: none;
    border-bottom: 1px solid #0d0e11;
    padding: 4px 6px;
    spacing: 2px;
}
QToolBar#ribbon QToolButton {
    color: #c7cdd8;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 12px 3px 12px;
    margin: 0 1px;
    font-size: 11px;
}
QToolBar#ribbon QToolButton:hover {
    background: #333945;
    border: 1px solid #3f4653;
    color: #ffffff;
}
QToolBar#ribbon QToolButton:pressed { background: #2b3039; }
QToolBar#ribbon QToolButton::menu-indicator { image: none; width: 0px; }
QToolBar::separator { background: #33373f; width: 1px; margin: 6px 6px; }

/* ---- Tabs (curved folder style) ---- */
QTabWidget::pane {
    border: 1px solid #2b2f37;
    background: #1e1e1e;
    top: -1px;
}
QTabBar { background: transparent; }
QTabBar::tab {
    background: #2a2d33;
    color: #98a2b2;
    padding: 6px 18px;
    margin-right: 3px;
    border: 1px solid #33373d;
    border-bottom: none;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    min-width: 90px;
}
QTabBar::tab:hover { background: #343a44; color: #d3d9e3; }
QTabBar::tab:selected {
    background: #1e1e1e;
    color: #ffffff;
    border-color: #2b2f37;
    border-top: 2px solid #38bdf8;
    font-weight: bold;
}

/* ---- Dock widgets / sidebar zones ---- */
QDockWidget {
    color: #c7cdd8;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #262a31, stop:1 #1f2229);
    color: #aeb6c2;
    padding: 6px 10px;
    border-bottom: 1px solid #0d0e11;
    font-weight: bold;
}

/* ---- Tree views (sessions + remote files) ---- */
QTreeView {
    background-color: #1b1d22;
    alternate-background-color: #202329;
    color: #cdd3dd;
    border: 1px solid #2b2f37;
    outline: 0;
    font-family: 'Segoe UI', 'DejaVu Sans', sans-serif;
    show-decoration-selected: 1;
}
QTreeView::item { padding: 3px 2px; border: none; }
QTreeView::item:hover { background-color: #2a2f39; }
QTreeView::item:selected { background-color: #1e4a63; color: #ffffff; }
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b2f37, stop:1 #24272e);
    color: #aeb6c2;
    border: none;
    border-right: 1px solid #191b1f;
    border-bottom: 1px solid #191b1f;
    padding: 5px 6px;
}

/* ---- Dialogs / inputs ---- */
QDialog { background-color: #22252b; color: #e6e9ee; }
QLabel { color: #d3d9e3; }
QLineEdit, QSpinBox {
    background-color: #14161a;
    color: #eef1f5;
    border: 1px solid #3a3f49;
    padding: 5px;
    border-radius: 5px;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #38bdf8; }
QComboBox {
    background-color: #14161a;
    color: #eef1f5;
    border: 1px solid #3a3f49;
    padding: 5px;
    border-radius: 5px;
}
QComboBox QAbstractItemView {
    background: #1b1d22; color: #eef1f5;
    selection-background-color: #2563eb; border: 1px solid #3a3f49;
}
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b74d8, stop:1 #1f5fbf);
    color: #ffffff;
    border: none;
    padding: 6px 14px;
    border-radius: 6px;
    font-weight: bold;
}
QPushButton:hover { background: #2f80ea; }
QPushButton:pressed { background: #1b53a8; }
QCheckBox { color: #d3d9e3; }

/* ---- Menus ---- */
QMenu { background-color: #23262d; color: #e6e9ee; border: 1px solid #3a3f49; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 4px; }
QMenu::item:selected { background-color: #2563eb; color: #ffffff; }
QMenu::separator { height: 1px; background: #33373f; margin: 4px 8px; }

/* ---- Scrollbars ---- */
QScrollBar:vertical { background: #16181d; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #394050; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4a5364; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #16181d; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #394050; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #4a5364; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""
