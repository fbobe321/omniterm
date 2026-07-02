import os
from PyQt6.QtGui import QIcon

_ICON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "icons"))
_cache = {}

# Map file extensions to a themed icon
_EXT_ICON = {
    ".py": "file-code", ".js": "file-code", ".ts": "file-code", ".c": "file-code",
    ".cpp": "file-code", ".h": "file-code", ".java": "file-code", ".go": "file-code",
    ".rs": "file-code", ".rb": "file-code", ".sh": "file-code", ".json": "file-code",
    ".xml": "file-code", ".html": "file-code", ".css": "file-code", ".yml": "file-code",
    ".yaml": "file-code", ".toml": "file-code", ".ini": "file-code",
    ".zip": "file-archive", ".gz": "file-archive", ".tar": "file-archive",
    ".tgz": "file-archive", ".bz2": "file-archive", ".xz": "file-archive",
    ".7z": "file-archive", ".rar": "file-archive",
    ".png": "file-image", ".jpg": "file-image", ".jpeg": "file-image",
    ".gif": "file-image", ".bmp": "file-image", ".svg": "file-image",
    ".webp": "file-image", ".ico": "file-image",
}


def get_icon(name):
    if name not in _cache:
        _cache[name] = QIcon(os.path.join(_ICON_DIR, f"{name}.svg"))
    return _cache[name]


def session_icon(session_type):
    return get_icon({
        "ssh": "server",
        "serial": "serial",
        "local": "terminal",
        "home": "terminal",
        "folder": "folder",
    }.get(session_type, "session"))


def file_icon(name, is_dir):
    if is_dir:
        return get_icon("folder")
    ext = os.path.splitext(name)[1].lower()
    return get_icon(_EXT_ICON.get(ext, "file"))
