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
    "foreground": "#ffffff",
    "background": "#1e1e1e",
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

def load_plugins():
    plugin_dir = HOME_DIR / "plugins"
    if not plugin_dir.exists():
        return []
    
    plugins = []
    for item in plugin_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            plugins.append(item.name)
    return plugins
