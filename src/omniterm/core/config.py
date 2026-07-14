import json
import os
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Global config for application settings like the home directory
GLOBAL_CONFIG_FILE = Path.home() / ".omniterm_global.json"

def get_home_dir():
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
                home_dir = config.get("home_dir")
                if home_dir:
                    return Path(home_dir).expanduser().resolve()
        except (json.JSONDecodeError, IOError):
            pass
    return Path.home()

HOME_DIR = get_home_dir()
CONFIG_FILE = HOME_DIR / ".omniterm_sessions.json"
KEY_FILE = HOME_DIR / ".omniterm_key"
SALT_FILE = HOME_DIR / ".omniterm_salt"

def set_home_dir(path):
    path_obj = Path(path).expanduser().resolve()
    path_obj.mkdir(parents=True, exist_ok=True)
    
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
            
    config["home_dir"] = str(path_obj)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def set_shared_sessions_file(path):
    path_obj = Path(path).expanduser().resolve()
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["shared_sessions_file"] = str(path_obj)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_shared_sessions_file():
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config.get("shared_sessions_file")
        except (json.JSONDecodeError, IOError):
            pass
    return None

DEFAULT_TERMINAL_SETTINGS = {
    "fontSize": 14,
    "fontFamily": "Consolas, 'DejaVu Sans Mono', monospace",
    "foreground": "#e6e9ee",
    "background": "#181a1f",
}

def get_terminal_settings():
    settings = dict(DEFAULT_TERMINAL_SETTINGS)
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
                stored = config.get("terminal")
                if isinstance(stored, dict):
                    settings.update({k: v for k, v in stored.items() if v is not None})
        except (json.JSONDecodeError, IOError):
            pass
    return settings

def set_terminal_settings(settings):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    merged = dict(config["terminal"]) if isinstance(config.get("terminal"), dict) else {}
    merged.update(settings)
    config["terminal"] = merged
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_salt():
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = os.urandom(16)
    SALT_FILE.write_bytes(salt)
    return salt

def derive_key_from_password(password: str):
    salt = get_salt()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key

# Global cipher instance. Initially None until master password is provided or fallback key is used.
_cipher = None

def init_cipher(master_password=None):
    global _cipher
    if master_password:
        key = derive_key_from_password(master_password)
    else:
        # Fallback to the static key file if no master password is used
        if KEY_FILE.exists():
            key = KEY_FILE.read_bytes()
        else:
            key = Fernet.generate_key()
            KEY_FILE.write_bytes(key)
    _cipher = Fernet(key)

# Initialize with fallback by default
init_cipher()

def encrypt_password(password):
    if _cipher is None:
        init_cipher()
    return _cipher.encrypt(password.encode()).decode()

def decrypt_password(token):
    if _cipher is None:
        init_cipher()
    try:
        return _cipher.decrypt(token.encode()).decode()
    except Exception:
        # If decryption fails, it's likely the wrong master password or an unencrypted token.
        # We return the token as-is, but in a production app, we might want to log a warning.
        return token 

def load_sessions():
    shared_file = get_shared_sessions_file()
    target_file = Path(shared_file) if shared_file else CONFIG_FILE

    if not target_file.exists():
        return {"version": "1.0", "sessions": []}
    try:
        with open(target_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"version": "1.0", "sessions": []}


def save_sessions(data):
    shared_file = get_shared_sessions_file()
    target_file = Path(shared_file) if shared_file else CONFIG_FILE
    with open(target_file, "w") as f:
        json.dump(data, f, indent=2)

def get_layouts():
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("layouts")
                if isinstance(value, dict):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def save_layout(name, layout):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    layouts = config.get("layouts") if isinstance(config.get("layouts"), dict) else {}
    layouts[name] = layout
    config["layouts"] = layouts
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def delete_layout(name):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    layouts = config.get("layouts") if isinstance(config.get("layouts"), dict) else {}
    if name in layouts:
        del layouts[name]
        config["layouts"] = layouts
        with open(GLOBAL_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return True
    return False

def get_native_terminal():
    """True = native Qt terminal (pyte + QPainter); False = web terminal
    (xterm.js in QtWebEngine). Native is the default (smoother, no GPU blanking)."""
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("native_terminal")
                if isinstance(value, bool):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return True

def set_native_terminal(value):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["native_terminal"] = bool(value)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_disable_gpu():
    """When True, run QtWebEngine with software rendering (no GPU). Slower but
    immune to GPU context-loss blanking (htop/btop) on flaky/virtual GPUs."""
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("disable_gpu")
                if isinstance(value, bool):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return False

def set_disable_gpu(value):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["disable_gpu"] = bool(value)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

VALID_RENDERERS = ("dom", "canvas", "webgl")

def get_renderer():
    """Terminal renderer: 'dom' (most stable), 'canvas', or 'webgl' (fastest,
    but can blank under heavy full-screen redraws like htop/btop)."""
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("renderer")
                if value in VALID_RENDERERS:
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return "dom"

def set_renderer(value):
    if value not in VALID_RENDERERS:
        value = "dom"
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["renderer"] = value
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

import time as _time
DEBUG_LOG_FILE = HOME_DIR / "omniterm_debug.log"
_debug_enabled_cache = None

def get_debug_logging():
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("debug_logging")
                if isinstance(value, bool):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return False

def set_debug_logging(value):
    global _debug_enabled_cache
    _debug_enabled_cache = bool(value)
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["debug_logging"] = bool(value)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def log_terminal_io(direction, data):
    """Append raw terminal I/O (repr) with a timestamp when debug logging is on."""
    global _debug_enabled_cache
    if _debug_enabled_cache is None:
        _debug_enabled_cache = get_debug_logging()
    if not _debug_enabled_cache:
        return
    try:
        with open(DEBUG_LOG_FILE, "a") as f:
            f.write(f"{_time.monotonic():.4f} {direction} {data!r}\n")
    except Exception:
        pass

def get_shortcuts():
    """Return the saved {action_id: key_sequence} overrides (may be empty)."""
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("shortcuts")
                if isinstance(value, dict):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return {}

def set_shortcuts(mapping):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["shortcuts"] = dict(mapping)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_use_inshellisense():
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                value = json.load(f).get("inshellisense")
                if isinstance(value, bool):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return False

def set_use_inshellisense(value):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    config["inshellisense"] = bool(value)
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

DEFAULT_SHADOW_PREDICTOR = {
    "enabled": False,      # off until validated
    "min_prefix": 1,       # min typed chars before suggesting
    "history_depth": 2000,  # commands kept/scanned for ranking
    "accept_key": "right",  # right-arrow at end of line accepts
}

def get_shadow_predictor():
    settings = dict(DEFAULT_SHADOW_PREDICTOR)
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                stored = json.load(f).get("shadow_predictor")
                if isinstance(stored, dict):
                    settings.update({k: v for k, v in stored.items() if v is not None})
        except (json.JSONDecodeError, IOError):
            pass
    return settings

def set_shadow_predictor(settings):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    merged = dict(config["shadow_predictor"]) if isinstance(config.get("shadow_predictor"), dict) else {}
    merged.update(settings)
    config["shadow_predictor"] = merged
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_group_folders_first():
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
                value = config.get("sftp", {}).get("group_folders_first")
                if isinstance(value, bool):
                    return value
        except (json.JSONDecodeError, IOError):
            pass
    return True  # default: folders grouped before files

def set_group_folders_first(value):
    config = {}
    if GLOBAL_CONFIG_FILE.exists():
        try:
            with open(GLOBAL_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    sftp_cfg = config.get("sftp", {}) if isinstance(config.get("sftp"), dict) else {}
    sftp_cfg["group_folders_first"] = bool(value)
    config["sftp"] = sftp_cfg
    with open(GLOBAL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def delete_session(session_id):
    """Remove the session with the given id, searching nested folders too.
    Returns True if a session was removed."""
    data = load_sessions()
    removed = [False]

    def prune(session_list):
        kept = []
        for s in session_list:
            if s.get("id") == session_id:
                removed[0] = True
                continue
            if s.get("type") == "folder":
                s["children"] = prune(s.get("children", []))
            kept.append(s)
        return kept

    data["sessions"] = prune(data.get("sessions", []))
    if removed[0]:
        save_sessions(data)
    return removed[0]

def import_sessions(path):
    """Merge sessions from a JSON file into the current config. Accepts either
    a full export ({"sessions": [...]}) or a bare list. Imported ids that are
    missing or collide with existing ones are reassigned. Returns the number
    of sessions (non-folder entries) imported."""
    import uuid
    with open(path, "r") as f:
        imported = json.load(f)

    if isinstance(imported, list):
        imported_sessions = imported
    elif isinstance(imported, dict):
        imported_sessions = imported.get("sessions", [])
    else:
        raise ValueError("Unrecognized sessions file format")

    data = load_sessions()

    existing_ids = set()
    def collect(session_list):
        for s in session_list:
            if isinstance(s, dict):
                if s.get("id"):
                    existing_ids.add(s["id"])
                if s.get("type") == "folder":
                    collect(s.get("children", []))
    collect(data.get("sessions", []))

    count = [0]
    def normalize(session_list):
        for s in session_list:
            if not isinstance(s, dict):
                continue
            sid = s.get("id")
            if not sid or sid in existing_ids:
                sid = str(uuid.uuid4())
                s["id"] = sid
            existing_ids.add(sid)
            if s.get("type") == "folder":
                normalize(s.get("children", []))
            else:
                count[0] += 1
    normalize(imported_sessions)

    data.setdefault("sessions", []).extend(imported_sessions)
    save_sessions(data)
    return count[0]

def update_session(updated):
    """Replace the session (matched by id) in-place, searching nested folders.
    Returns True if a matching session was found and saved."""
    sid = updated.get("id")
    if not sid:
        return False
    data = load_sessions()
    found = [False]

    def walk(session_list):
        for i, s in enumerate(session_list):
            if s.get("id") == sid:
                # keep folder children if the caller didn't provide new ones
                if s.get("type") == "folder" and "children" not in updated:
                    updated["children"] = s.get("children", [])
                session_list[i] = updated
                found[0] = True
                return
            if s.get("type") == "folder":
                walk(s.get("children", []))

    walk(data.get("sessions", []))
    if found[0]:
        save_sessions(data)
    return found[0]

def find_session(session_id):
    """Return the session dict with the given id, or None."""
    def walk(session_list):
        for s in session_list:
            if s.get("id") == session_id:
                return s
            if s.get("type") == "folder":
                r = walk(s.get("children", []))
                if r:
                    return r
        return None
    return walk(load_sessions().get("sessions", []))

def export_sessions(path, include_secrets=False):
    """Write the session config to `path`. By default encrypted password
    tokens are stripped so the exported file is safe to share/back up."""
    import copy
    data = copy.deepcopy(load_sessions())

    def scrub(session_list):
        for s in session_list:
            if not include_secrets:
                s.pop("password", None)
            if s.get("type") == "folder":
                scrub(s.get("children", []))

    if not include_secrets:
        scrub(data.get("sessions", []))

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_plugins():
    plugin_dir = HOME_DIR / "plugins"
    if not plugin_dir.exists():
        return []
    
    plugins = []
    for item in plugin_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            plugins.append(item.name)
    return plugins
