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
