# Product Requirements Document (PRD) — v4 (As-Built, native terminal)
**Project Name:** OmniTerm (Local MobaXterm Alternative)
**Status:** Shipped — `v0.1.77` on PyPI and GitHub
**Distribution:**
- PyPI: https://pypi.org/project/omniterm/ (`pip install omniterm`, run `omniterm`)
- GitHub: https://github.com/fbobe321/omniterm

> **TESTING POLICY (important):** The user tests **only** via a published PyPI release
> (`pip install -U omniterm`) on a separate Windows machine. They do **not** test via
> `pip install -e .`, local wheels, or running from source. Any code change the user
> needs to verify is **not done** until it is: version-bumped in `pyproject.toml`,
> rebuilt, and **published to PyPI**. Do not offer local-install alternatives.
**Target Architecture:** Local-first, zero-telemetry, cross-platform (Windows / Linux / macOS)
**Primary Objective:** A secure, locally-run terminal multiplexer supporting SSH, SFTP, local shell, a Unix-like "Home" shell, and serial communication — with a modern, MobaXterm-inspired UI — using only local dependencies.

---

## 1. As-Built Architecture & File Structure

Packaged as an installable Python distribution (`src/` layout) with a console entry point (`omniterm = omniterm.main:main`).

```text
omniterm/                        # repo root
├── pyproject.toml               # setuptools packaging, deps, entry points
├── README.md, LICENSE (MIT), .gitignore
└── src/omniterm/
    ├── main.py                  # entry point; app icon + Windows AppUserModelID; QApplication
    ├── core/
    │   ├── config.py            # sessions, layouts, settings, encryption (Fernet/PBKDF2)
    │   ├── ssh_client.py        # paramiko SSHWorker (QThread): shell, SFTP, tunnels, X11, OSC7
    │   ├── serial_client.py     # pyserial SerialWorker (QThread)
    │   └── local_pty.py         # LocalPTYWorker (QThread): local + "Home" Unix shell
    ├── ui/
    │   ├── main_window.py       # QMainWindow, toolbar ribbon, tabs, splits, layouts, dialogs
    │   ├── session_dock.py      # session tree (icons, edit/delete, context menu)
    │   ├── sftp_browser.py      # remote + local file browser (LocalFSAdapter), path bar
    │   ├── native_terminal.py   # NativeTerminal(QWidget): pyte screen model + QPainter renderer
    │   ├── terminal_tab.py      # TerminalTab (wraps NativeTerminal) + SplitContainer
    │   ├── theme.py             # central dark QSS (charcoal zones, curved tabs, accents)
    │   └── icons.py             # SVG icon loader + app icon
    └── static/
        └── icons/               # bundled SVG icon set + app icon (png/ico)
```

**Terminal engine (v0.1.61+):** the terminal is rendered **natively** — `pyte`
maintains the screen model and `QPainter` draws the grid directly onto a QWidget
(`native_terminal.py`). This replaced the earlier xterm.js-in-QtWebEngine
approach, eliminating the embedded browser: native-terminal smoothness, no GPU
context-loss blanking (htop/btop), minimal input latency, and no `PyQt6-WebEngine`
dependency (v0.1.63 dropped it, shrinking installs by ~100 MB). NativeTerminal
covers 16/256/truecolor, bold/italic/underline/reverse, block cursor, run-batched
dirty-rect painting, full keyboard, live resize→PTY, mouse selection with
copy-on-select, scrollback, a real alternate-screen buffer (vi/htop/btop), and
`ESC[s`/`ESC[u` cursor save/restore (translated to the DEC form pyte handles).

**Concurrency model (unchanged from spec):** all network/serial/PTY I/O runs in `QThread` workers that communicate with the GUI via signals/slots. No blocking I/O on the main thread.

---

## 2. State & Data

All state persists to local JSON; the app is stateless between launches.

- **Sessions:** `~/.omniterm_sessions.json` (or a configurable/shared file) — `{"version","sessions":[...]}`; session types `ssh | serial | local | home | folder` (folders nest). Passwords are encrypted (Fernet key file, or PBKDF2-HMAC-SHA256 from an optional master password).
- **Global config:** `~/.omniterm_global.json` — home dir, shared sessions file, terminal appearance, folder-grouping toggle, Inshellisense toggle, keyboard-shortcut overrides, and **saved layouts**.
- **Keys/salt:** `~/.omniterm_key`, `~/.omniterm_salt`.

---

## 3. Delivered Features

### Sessions & connectivity
- SSH (password/key auth), serial (configurable), local shell, and **Home terminal** (MobaXterm-style local Unix shell: auto-detects Git Bash → WSL → BusyBox on Windows, `$SHELL` on Unix).
- Add / **edit** / delete sessions; **import / export** sessions (passwords optionally stripped).
- SSH extras: port-forwarding config, startup script, **X11 forwarding**, tab-level reconnect.

### Terminal
- **Native Qt renderer** (`pyte` screen model + `QPainter`); 16/256/truecolor, bold/italic/underline/reverse; configurable font size & colors; scrollback; real alternate-screen buffer (vi/htop/btop); `ESC[s`/`ESC[u` cursor save/restore.
- PTY size sync (full-screen apps like `nvtop`/`vim` fill the window); Inshellisense (`is`) autocomplete toggle; per-tab reconnect banner.
- **Selection & clipboard (v0.1.66):** mouse selection + copy-on-select (drag or double-click word); **keyboard selection** — Shift+Arrow/Home/End extend a selection from the cursor (line-wrapping), a plain arrow collapses it, and the highlight is copied on-select; **paste** via Ctrl+V, Ctrl+Shift+V, Shift+Insert, or middle-click; **copy** via Ctrl+Shift+C (Ctrl+C still sends SIGINT); **right-click Copy/Paste context menu** (items enabled by selection / clipboard state).
- **Shadow command prediction (v0.1.67–0.1.68, opt-in, off by default):** as you type, the likely next command is shown as dim inline text, predicted from your own submitted-command history (zero-ML, fish/zsh-autosuggestions style; recorded client-side so it works across SSH / serial / local / Home). Accept the whole suggestion with **Ctrl+F / End / Right-arrow**, one word with **Ctrl+Right**; **Tab is left for shell completion**. Command-line tracking captures the prompt-end column at the first keystroke (prompt-agnostic) and handles wrapped lines. **Secrets are never suggested or stored:** no-echo password prompts pause suggestion + recording (robust to SSH echo latency), and commands with inline secrets (`mysql -pX`, `export TOKEN=`, `Authorization: Bearer`, `--password=`, pasted keys) are filtered. Toggle via **Settings → Command Prediction (Shadow Text)**. History at `~/.local/share/omniterm/history.jsonl`. Modules: `core/command_history.py`, `core/predictor.py`; rendered in `ui/native_terminal.py`. Tested in `tests/test_shadow_predictor.py`.

### Tabs & layout
- Tabs: closeable, **rename** (double-click / menu), **drag-to-reorder**, **background-activity dot**.
- **Split view:** combine open tabs into 1 / 2 (horizontal) / 2 (vertical) / 4 panes; **unsplit**.
- **Layouts (workspaces):** save the open tabs + split arrangement + per-terminal init commands (auto-captures cwd and active **conda env**), and restore them (recreates sessions, splits, custom tab names, and runs init).

### Files
- **SFTP browser** auto-attaches per SSH tab (each tab keeps its own session + location); **local filesystem** browser for local/Home tabs.
- Navigable (enter folders, `..`, size/date columns, click-to-sort, folders-first toggle), **drag-and-drop** to/from the OS file manager, multi-file download/upload, editable **path bar with autocomplete**, and **"Follow terminal folder"** (OSC 7).
- **Double-click-to-edit (v0.1.72):** double-clicking a remote file downloads a temp copy and opens it in the OS default app; saving locally prompts to upload+overwrite the remote (`QFileSystemWatcher`). **Transfer progress:** downloads/uploads/edit round-trips run in a background worker (`core/transfer.py`) behind a modal progress dialog with Cancel; remote transfers use a dedicated SFTP channel. Tested in `tests/test_transfer.py`.

### CLI / automation (v0.1.75)
- **Headless CLI** `omniterm-cli` (agent-native, inspired by CLI-Anything): drive OmniTerm's capabilities without the GUI. `session list/show/add/remove`, `exec <session> "<cmd>"` (captures stdout/stderr/exit code), `sftp ls/get/put`, and a `repl`. Every command supports `--json`. Reuses the saved session config + SSH/SFTP logic; imports no PyQt. Ships a `SKILL.md` for agent discovery. Module `omniterm/cli.py`; tests `tests/test_cli.py`. **Phase 2 (v0.1.76): control the running GUI** — `omniterm-cli ctl ping/list-tabs/open/run/send-keys/capture/focus-tab/close-tab` over a token-authed localhost socket (`core/control.py` transport, `core/control_server.py` server marshaling onto the Qt thread). Disable with `OMNITERM_NO_CONTROL=1`. Tests `tests/test_control.py`.

### UI / UX
- Modern dark theme: icon+label toolbar ribbon, curved folder-style tabs with active accent, colorful sidebar icons + alternating rows, charcoal depth zones.
- Help menu (GitHub/support + version), connection-drop banner (Reconnect / Close Tab), rsync-tools folder on the Home PATH.
- **Configurable keyboard shortcuts** for common tasks (tab switching Ctrl+Shift+Z/Ctrl+Shift+A, new session/home/local, close/rename tab, split/unsplit, save/open layout, etc.); **Settings → Keyboard Shortcuts** editor (`QKeySequenceEdit` per action) with live rebind and Reset to Defaults; stored in the global config.

---

## 4. Constraints (unchanged)

- **No telemetry / phoning home.** No update checks, analytics, or crash reporters.
- **Local dependencies only:** `PyQt6`, `pyte`, `paramiko`, `pyserial`, `keyring`, `cryptography`, `pywinpty` (Windows). No embedded browser — `PyQt6-WebEngine` was dropped in v0.1.63 (installs no longer pull ~100 MB of Chromium).
- **Concurrency safety:** all blocking I/O in `QThread` workers.
- **Graceful teardown:** closing a tab stops its worker, waits for the thread, and disconnects signals; split panes transplant their live workers into fresh native terminals (`detach_worker` / `set_worker`).

---

## 5. Resolved & Known Items

- **Rendering roughness / typing lag / htop-btop blanking (RESOLVED in v0.1.61–0.1.63):** these all stemmed from the old xterm.js-in-QtWebEngine architecture (browser IPC round-trip latency + GPU context loss). The **native `pyte`+`QPainter` terminal** replaced it and fixed all three. Do not reintroduce a web/GPU renderer or output coalescing to "smooth" typing — the native path renders directly and is already smooth.
- **rsync / Inshellisense** are not bundled (licensing/size); OmniTerm uses them if installed and guides the user otherwise. Inshellisense is launched via a shell-side self-check (`command -v is && is`), not the launcher PATH.
- **X11 forwarding** requires a local X server (VcXsrv/X410 on Windows, XQuartz on macOS).
- Local file browser has minor Windows drive-root (`C:`) edge cases.
- Native terminal possible rough edges to watch (small, targeted fixes): wide/CJK/emoji cell widths, unusual/rare escape sequences, and cross-`feed()` splitting of an alternate-screen sequence.
