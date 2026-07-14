"""Command history store for the shadow predictor.

Records commands the user actually submits (captured client-side, so it works
uniformly across SSH / serial / local / Home sessions), persists them to a
JSONL file, and exposes them most-recent-last for ranking. Ranking itself lives
in ``predictor.py`` — this module only stores and serves.
"""
import json
import os
import re
import time

# Commands that likely carry an inline secret are never recorded, so they can
# never be suggested back. Covers inline passwords/tokens/keys and auth headers.
_SENSITIVE_RE = re.compile(
    r"""(?ix)
      (^|\s)-p\S                      # mysql/curl style -p<password>
    | pass(word|wd)?\s*[=:]           # password= / passwd:
    | (api[_-]?key|secret|token|auth[_-]?token|access[_-]?key)\s*[=:]
    | bearer\s+\S                     # Authorization: Bearer <token>
    | -----BEGIN                      # pasted private key material
    """,
)


def is_sensitive_command(cmd):
    """Heuristic: does this command line appear to contain a secret?"""
    return bool(cmd and _SENSITIVE_RE.search(cmd))


# ~/.local/share/omniterm/history.jsonl (XDG data dir), with a home fallback.
def _default_path():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "omniterm", "history.jsonl")


class CommandHistory:
    def __init__(self, path=None, cap=2000):
        self.path = path or _default_path()
        self.cap = cap
        self._entries = []   # list of {"cmd", "cwd", "ts"}, most-recent-last
        self._load()

    # ---- persistence ----
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict) and e.get("cmd"):
                        self._entries.append(e)
        except (IOError, OSError):
            pass
        if len(self._entries) > self.cap:
            self._entries = self._entries[-self.cap:]

    def _append_disk(self, entry):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except (IOError, OSError):
            pass

    def _rewrite_disk(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                for e in self._entries:
                    f.write(json.dumps(e) + "\n")
        except (IOError, OSError):
            pass

    # ---- API ----
    def record(self, cmd, cwd=None):
        cmd = (cmd or "").strip()
        if not cmd:
            return
        # Hard guarantee: never persist (and thus never suggest) a secret.
        if is_sensitive_command(cmd):
            return
        # collapse an immediate duplicate (same command run twice in a row)
        if self._entries and self._entries[-1].get("cmd") == cmd:
            self._entries[-1]["ts"] = time.time()
            self._entries[-1]["cwd"] = cwd
            self._append_disk(self._entries[-1])
            return
        entry = {"cmd": cmd, "cwd": cwd, "ts": time.time()}
        self._entries.append(entry)
        self._append_disk(entry)
        if len(self._entries) > self.cap * 2:
            # compact when the on-disk log has grown well past the cap
            self._entries = self._entries[-self.cap:]
            self._rewrite_disk()

    def entries(self):
        """All stored entries, most-recent-last."""
        return self._entries
