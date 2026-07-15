# OmniTerm

A cross-platform terminal built with PyQt6.
OmniTerm gives you SSH, serial, local, and Unix-like "Home" shell sessions in a
tabbed interface, an integrated SFTP file browser, encrypted credential storage,
a native terminal renderer, and a headless CLI for scripts and agents.

## Features

- **Multiple session types** — SSH (password or key auth), serial (configurable
  baud / data bits / parity / stop bits), local PTY shells, and a MobaXterm-style
  "Home" Unix shell.
- **Native terminal renderer** — `pyte` screen model drawn directly with
  `QPainter` (no embedded browser): smooth output, minimal input latency, true
  256/true-color, and a real alternate-screen buffer for vi/htop/btop.
- **Tabbed sessions** with a sidebar session tree (folders), drag-to-reorder,
  split view (2/4 panes), and saveable layouts.
- **Integrated SFTP browser** that attaches automatically to SSH sessions:
  navigate, drag-and-drop, **double-click a remote file to edit** it locally and
  sync changes back, and transfer with a **progress bar**.
- **Shadow command prediction** (opt-in) — inline suggestions of your next
  command from your own history; accept with Ctrl+F / End / →. Secrets are never
  suggested or stored.
- **Ergonomics** — copy-on-select, full clipboard support, keyboard text
  selection, bracketed paste (clean pastes into vim), and **Ctrl+wheel font zoom**.
- **Encrypted credentials** — passwords stored with Fernet encryption, optionally
  protected by a master password (PBKDF2-HMAC-SHA256).
- **Headless CLI** (`omniterm-cli`) — drive sessions, remote command execution,
  and SFTP from scripts and agents without the GUI (see below).

## Installation

```bash
pip install omniterm
```

## Usage

Launch the GUI:

```bash
omniterm
```

## Command-line interface (headless)

`omniterm-cli` controls OmniTerm's capabilities without the GUI — ideal for
scripts, automation, and AI agents. It reuses the sessions you've saved in the
app. Every command supports `--json`.

```bash
# Sessions
omniterm-cli session list --json
omniterm-cli session add prod --host example.com --user deploy \
    --auth key --key-path ~/.ssh/id_ed25519
omniterm-cli session show prod

# Run a remote command (captures stdout/stderr, propagates the exit code)
omniterm-cli exec prod "systemctl is-active nginx" --json

# SFTP
omniterm-cli sftp ls  prod /var/log --json
omniterm-cli sftp get prod /var/log/app.log ./
omniterm-cli sftp put prod ./build.tar.gz /tmp/

# Interactive REPL
omniterm-cli repl
```

### Driving a running GUI

If the OmniTerm window is open, `omniterm-cli ctl …` controls it live over a
token-authenticated localhost socket:

```bash
omniterm-cli ctl ping
omniterm-cli ctl open --type ssh --session prod
omniterm-cli ctl run     --tab 0 --text "uname -a"
omniterm-cli ctl capture --tab 0            # read the tab's output back
omniterm-cli ctl list-tabs --json
```

Set `OMNITERM_NO_CONTROL=1` to disable the control socket.

For agent use, a [`SKILL.md`](SKILL.md) describes the whole interface for
discovery. The CLI imports no PyQt, so it runs anywhere — no display required.

## Development

```bash
git clone https://github.com/fbobe321/omniterm
cd omniterm
pip install -e .
python -m omniterm.main          # GUI
python -m omniterm.cli --help    # CLI

# Tests (headless)
QT_QPA_PLATFORM=offscreen python -m pytest tests/
```

## License

MIT — see [LICENSE](LICENSE).
