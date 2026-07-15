---
name: omniterm-cli
description: >-
  Headless control of OmniTerm from the command line. Manage saved SSH/serial
  sessions, run remote commands and capture their output, and transfer files
  over SFTP — all without the GUI. Every command supports --json for machine
  consumption. Use this to let an agent or script drive OmniTerm's connectivity.
version: 1
entrypoint: omniterm-cli
---

# OmniTerm CLI

Deterministic, scriptable control of OmniTerm's capabilities. Install with
`pip install omniterm`; the `omniterm-cli` command is then on PATH (or run
`python -m omniterm.cli`). Sessions are read from the same config the GUI uses
(`~/.omniterm_sessions.json`), so anything you set up in the app is usable here.

Add `--json` to any command for structured output.

## Sessions

```bash
omniterm-cli session list [--json]
omniterm-cli session show <name> [--json]      # password redacted
omniterm-cli session add <name> --host H --user U [--port 22] \
    (--auth password --password PW | --auth password --ask-password | \
     --auth key --key-path ~/.ssh/id_ed25519)
omniterm-cli session remove <name>
```

Sessions are referenced by their **name** (or id). Passwords are stored
encrypted, exactly as the GUI stores them.

## Remote command execution

```bash
omniterm-cli exec <session> "<command>" [--timeout SECONDS] [--json]
```

Runs a single command over SSH (non-interactive) and captures output. Exit code
is propagated as the process exit code. With `--json` you get
`{"stdout": ..., "stderr": ..., "exit_code": ...}`.

```bash
omniterm-cli exec prod "systemctl is-active nginx" --json
```

## SFTP file transfer

```bash
omniterm-cli sftp ls  <session> <remote_dir> [--json]
omniterm-cli sftp get <session> <remote_file> [local_dest] [--json]
omniterm-cli sftp put <session> <local_file>  <remote_dest> [--json]
```

`get`/`put` accept a directory destination (the basename is kept). Progress is
printed to stderr unless `--json` is given.

## Drive a running GUI (control socket)

If OmniTerm's GUI is running, `omniterm-cli ctl ...` controls it live over a
localhost socket (token-authenticated; the running app writes
`~/.omniterm_ctl.json`, mode 0600). Use this to open tabs, type into them, and
read their output.

```bash
omniterm-cli ctl ping                                   # is a GUI running?
omniterm-cli ctl list-tabs --json
omniterm-cli ctl open --type local                      # or --type home
omniterm-cli ctl open --type ssh --session prod         # opens a saved session
omniterm-cli ctl run    --tab 0 --text "uname -a"       # types the line + Enter
omniterm-cli ctl send-keys --tab 0 --text "y" [--enter] # raw keystrokes
omniterm-cli ctl capture --tab 0 [--scrollback 200]     # read the tab's text
omniterm-cli ctl focus-tab --tab 0
omniterm-cli ctl close-tab --tab 0

# Split view: combine open tabs into panes, then target a pane with --pane
omniterm-cli ctl split   --tabs 0,1 [--orientation horizontal|vertical]
omniterm-cli ctl run     --tab 0 --pane 1 --text "htop"
omniterm-cli ctl capture --tab 0 --pane 1
omniterm-cli ctl unsplit --tab 0
```

`list-tabs` reports a `panes` count per tab; `run`/`send-keys`/`capture`/
`focus-tab` accept `--pane K` to target pane K of a split (default 0).

Typical agent loop: `ctl run --tab N --text "<cmd>"`, wait briefly, then
`ctl capture --tab N` to read the result. (`exec` above is better when you just
need a command's output and don't need the interactive session.)

## Interactive REPL

```bash
omniterm-cli repl
```

A stateful prompt that accepts the same subcommands (`help`, `quit` to exit).

## Notes for agents

- Prefer `--json`; parse `exit_code` from `exec` to branch on success/failure.
- `session list --json` first to discover available targets by name.
- Only `ssh` sessions support `exec`/`sftp`; `session show` reveals the type.
- This CLI never launches the GUI and makes no interactive prompts unless you
  pass `--ask-password`.
