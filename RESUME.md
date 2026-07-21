# OmniTerm — RESUME / Session Handoff

**This is a RUNNING log — keep it current.** Update the "Session log" and "Current state"
sections as work happens (after every ship, and whenever a task's status changes), so if we
lose connection mid-task the next session can pick up without getting lost. Last updated: **2026-07-20**.

## ⚠️ Testing policy — READ FIRST
The user can **only** test via a **published PyPI release** (`pip install -U omniterm`)
on a separate Windows machine. Local install (`pip install -e .`), local wheels, and
running from source are **not** options for them — do not suggest them. A code change is
only "ready to test" once it's version-bumped, rebuilt, and **published to PyPI**.
**Publishing is automatic after changes — do NOT ask for go-ahead** (standing instruction).

## Current state
- **Version:** `0.1.88` — live on PyPI and GitHub, all work committed and pushed.
- **PyPI:** https://pypi.org/project/omniterm/ (`pip install -U omniterm`, run `omniterm`)
- **GitHub:** https://github.com/fbobe321/omniterm  (branch `main`, tag `v0.1.88`)
- **PRD:** `/data3/omniterm/PRD.md` (v4, as-built — native terminal).
- **Working tree:** clean (last release committed + pushed).
- **In flight / awaiting user test:** v0.1.87 exit-crash fix + v0.1.88 Files delete — user to verify on Windows.

## Session log (running — newest first)
### 2026-07-21 — v0.1.88 shipped (Files panel: delete files/folders)
Right-click context menu in the Files (SFTP) panel gained a **Delete** entry for
files and folders, with an "are you sure?" confirmation (`QMessageBox.question`,
default No). Also bound to the **Del** key (`SFTPTreeView.keyPressEvent`, next to
F2 rename). Works local + remote via one recursive `_delete_entry(path, is_dir)`
that uses the adapter's `listdir_attr`/`remove`/`rmdir` (added `remove`/`rmdir` to
`LocalFSAdapter` to match paramiko's API); folders delete recursively with all
contents. Menu acts on the current selection, or the right-clicked entry if it
isn't in the selection (file-manager behaviour); the ".." row is never deletable.
Single vs multi wording ("Delete" / "Delete N Items"). All in `ui/sftp_browser.py`
(`selected_entry_paths`, `_delete_entry`, `delete_entries`, `delete_selected`,
menu additions). Verified offscreen: recursive delete empties a nested tree, No
cancels, ".." excluded, "Delete 2 Items" on multi-select. **Status: awaiting Windows test.**
- Note: delete runs synchronously on the GUI thread (like mkdir/rename). Fine for
  files/small trees; a huge remote recursive delete would briefly block the UI —
  move to a worker thread if that ever bites (backlog).

### 2026-07-21 — v0.1.87 shipped (exit-crash, take 2)
User hit `QThread: Destroyed while thread '' is still running` again (Windows, on
close — the transcript's leading `omniterm` + trailing prompt is launch→use→close,
not a pure-startup crash; confirmed plain startup opens 0 tabs and no QThread, so
it can't be a startup abort). v0.1.86 joined workers with `wait(2000)` but a worker
that refuses to stop in that window (the Windows `LocalPTYWorker` blocked in
pywinpty `pty.read()` that `close()` didn't interrupt from the GUI thread) was left
running and destroyed during teardown → `qFatal()` aborts the process. Fix: after
the graceful wait, `MainWindow.closeEvent` and `SFTPBrowser.shutdown()` now
force-`terminate()`+`wait()` any straggler still `isRunning()`. Safe because these
workers block in native reads that release the GIL (GUI stays live while a terminal
idles), so `terminate()` can't strand the GIL — and it's strictly better than the
abort it replaces. `pty.close()` in `stop()` still handles the normal case, so
terminate is a true last resort. Verified on Linux offscreen: a worker that ignores
`stop()` and blocks in a GIL-releasing `os.read` is joined by the terminate path,
`isRunning()`→False, process exits RC=0 (no abort). **Status: awaiting Windows test.**
- Note: the harmless `QFont::setPointSize: Point size <= 0 (-1)` warning in the same
  transcript is Qt noise (our code uses `setPointSizeF` with a `>0` guard), not the crash.

### 2026-07-20 — Relocated project to standalone `/data3/omniterm`
Moved the repo out of the `/data3/mobax` workspace so OmniTerm stands on its own:
`/data3/mobax/omniterm` → `/data3/omniterm` (repo + .git + local credential config),
and folded the project's workspace docs in beside the code (DESIGN.md, DOCUMENTATION.md,
MOBAXTERM_GUIDE.md, DRYDOCK_GUIDE.md, cmd_helper_PLAN.md, cmd_helper_PRD.md, Docs/,
plus local-only `.drydock/`, `icon.png`, gitignored `.omniterm_sessions.json`).
`/data3/mobax` removed. Repo remote/branch/creds verified intact. All path references
updated (RESUME, memory). **Open decision:** which of the moved docs to commit to the
public repo vs keep local — see below / ask the user.

### 2026-07-20 — v0.1.86 shipped (follow-folder + exit-crash fixes)
Three fixes, all shipped in `0.1.86` (PyPI + tag `v0.1.86`). **Status: awaiting user test on Windows.**
1. **Follow-folder command echoed in every terminal.** `SSHWorker.send_invisible` hid echo
   by literal-matching the command text — impossible against readline redraw / zsh highlight.
   Rewrote to hide all output until the shell's first OSC 7 (`_hiding_echo` in
   `core/ssh_client.py`); `_scan_cwd` still runs on raw data so cwd detection is unaffected.
2. **"Doesn't follow."** Old `FOLLOW_CMD` clobbered `PROMPT_COMMAND` + used a bare `precmd`
   (frameworks override it). New `SFTPBrowser.FOLLOW_CMD`: one `__omniterm_cwd` fn; bash
   PREPENDS to PROMPT_COMMAND, zsh uses `precmd_functions` behind `eval`+`$ZSH_VERSION`
   guard (POSIX shells don't parse-error). Verified emission in bash/dash here.
3. **Exit crash `QThread: Destroyed while thread is still running`.** SFTP listers/transfer
   QThreads (children of the Files dock) were never joined on close. Added
   `SFTPBrowser.shutdown()` (stop+wait all listers/transfer), `SFTPLister.stop()` now closes
   its own SFTP channel to unblock a `listdir_attr` on a dead socket, and
   `MainWindow.closeEvent` calls `shutdown()` before teardown.
- Also this session: stored PyPI + GitHub creds (`~/.pypirc`, `~/.git-credentials`, mode 600;
  omniterm repo `credential.helper=store`) so releases run without prompting. Publishing is
  now automatic after changes.
- **Next:** wait for user's Windows test results; if follow still misbehaves on a host, have
  them enable Settings → debug logging to capture raw remote bytes.

## Shadow command predictor (v0.1.67–0.1.70) — shipped, opt-in
- Inline dim suggestion of the next command from the user's own history
  (zero-ML). Off by default; enable via **Settings → Command Prediction
  (Shadow Text)** or `~/.omniterm_global.json` → `"shadow_predictor":{"enabled":true}`.
- Accept whole = **Ctrl+F / End / Right**; one word = **Ctrl+Right**. **Tab is
  deliberately left for shell completion** (do NOT bind Tab to accept). These
  keys are listed (read-only) in Settings → Keyboard Shortcuts under
  "Terminal (built-in)".
- **v0.1.70 polish:** ranking is recency-dominant + cwd boost + mild frequency
  tie-break (`predictor.py`); the block cursor no longer hides the first
  suggestion char (`_paint_cursor`).
- **v0.1.69 (general terminal fix, not predictor-specific):** Tab/Shift+Tab were
  stolen by Qt focus traversal (jumped to tab bar/menu) so shell completion never
  worked. Fixed by `NativeTerminal.focusNextPrevChild()` returning False; Tab now
  reaches keyPressEvent → sends `\t` / back-tab `\x1b[Z`.
- Works on **SSH + local + Home**. The v0.1.68 fix: masked-input (password)
  detection was latching on SSH echo latency and killing suggestions on remote;
  it's now a live check that clears the moment any char echoes.
- **Secrets:** no-echo password prompts pause suggestion+recording; inline-secret
  commands are filtered (`is_sensitive_command`). Never stored, never suggested.
- Code: `core/command_history.py`, `core/predictor.py`, `ui/native_terminal.py`
  (prompt-end capture, `_current_buffer`, `_update_prediction`, `_paint_shadow`,
  `_accept_shadow*`). Config: `get/set_shadow_predictor`. Tests:
  `tests/test_shadow_predictor.py` (21 tests; run with
  `QT_QPA_PLATFORM=offscreen python -m pytest tests/`).
- Plan/design doc: `/data3/omniterm/cmd_helper_PLAN.md` (Phases 0–2 DONE; Phase 3 =
  optional neural ONNX model, NOT started).

## Recent work (v0.1.64 → v0.1.66)
- **0.1.64:** mode-aware arrow keys — apps in DECCKM (inshellisense/readline/vi)
  get SS3 (`ESC O x`), otherwise CSI (`ESC [ x`). Fixed up-arrow history.
- **0.1.65 (folded into 0.1.66, never published separately):** right-click
  Copy/Paste menu, Ctrl+Shift+C / Ctrl+Shift+V, Shift+Insert paste, middle-click
  paste. (Ctrl+C without a selection still sends SIGINT.)
- **0.1.66:** keyboard text selection — Shift+Arrow/Home/End extend a selection
  seeded at the cursor (with line-wrap at row boundaries); a plain arrow
  collapses it; the highlight is copied on-select so Ctrl+V / Shift+Insert /
  middle-click paste it immediately. All in `native_terminal.py`
  (`_extend_keyboard_selection`, `_SEL_KEYS`, `_paste`, `contextMenuEvent`).

## SFTP browser: edit-on-double-click + transfer progress (v0.1.72)
- Double-click a **remote** file → downloads to `%TEMP%/omniterm_edit/` and opens
  in the OS default app; a `QFileSystemWatcher` detects local saves and prompts
  to upload+overwrite the remote (`open_for_edit` / `_start_editing` /
  `_handle_edit_change`). Double-click a **local** file just opens it.
- All transfers run in a background `core/transfer.py::TransferWorker` behind a
  modal `QProgressDialog` (Cancel supported). Remote transfers open a DEDICATED
  SFTP channel from the transport (`_transport_or_none`) so the browser's own
  client isn't used concurrently. `LocalFSAdapter` gained chunked get/put with a
  progress callback + `stat`. Entry: `SFTPBrowser._run_transfer(jobs, title, on_success)`.
- Tests: `tests/test_transfer.py` (6).

## Headless CLI — omniterm-cli (v0.1.75) — agent-native control, Phase 1
- New GUI-free CLI (`src/omniterm/cli.py`, entry point `omniterm-cli`, also
  `python -m omniterm.cli`). Reuses `core/config` + paramiko; imports **no PyQt**
  (test enforces this in a subprocess). Inspired by HKUDS/CLI-Anything.
- Commands: `session list|show|add|remove`, `exec <session> "<cmd>"` (captures
  stdout/stderr/exit, propagates exit code), `sftp ls|get|put`, `repl`. All take
  `--json`. Sessions referenced by name or id; only `ssh` type supports exec/sftp.
- Ships `SKILL.md` (repo root) for agent discovery. Tests: `tests/test_cli.py` (12).
- **Phase 2 (v0.1.76) — DONE: drive the running GUI via a control socket.**
  `omniterm-cli ctl ping/list-tabs/open/run/send-keys/capture/focus-tab/close-tab`.
  Transport `core/control.py` (Qt-free: newline-JSON over 127.0.0.1, random token
  in 0600 `~/.omniterm_ctl.json`). Server `core/control_server.py` (QObject)
  accepts on bg threads, marshals each request onto the Qt thread via a queued
  signal (blocks worker on a threading.Event for the result).
  `MainWindow.handle_control_command` maps verbs; `NativeTerminal.screen_text()`
  backs capture. Disable via `OMNITERM_NO_CONTROL=1`. Verified end-to-end vs a
  real headless GUI. Tests: `tests/test_control.py`. Stale ctl file after a hard
  crash is handled client-side (connection-refused → clear error), not auto-cleaned.
- **v0.1.77 — split-pane control:** `ctl split --tabs 0,1 [--orientation ...]`
  (reuses `combine_tabs_into_split`) and `ctl unsplit --tab N` (`unsplit_tab`).
  `run`/`send-keys`/`capture`/`focus-tab` take `--pane K` (default 0); `list-tabs`
  reports `panes` per tab. Verified end-to-end (ran in pane 1, captured pane 1).
- Ctrl+wheel font zoom also landed pre-CLI (v0.1.74) in `native_terminal.py`
  (`_adjust_font_size`, persisted debounced).

## Other terminal fixes
- **v0.1.72 scrollback pinning:** scrolling up no longer snaps to the bottom on
  new output — `feed()` advances `_scroll` by the number of lines pushed into
  `history.top` so the view stays on the same lines.
- **v0.1.71 bracketed paste:** large/multi-line pastes into vim used to
  "staircase" (autoindent per line). `_paste()` now wraps pasted text in
  `ESC[200~`/`ESC[201~` when the app has private mode 2004 on (pyte tracks it
  per-screen) and normalizes newlines to CR. Covers all paste paths.

## BIG architecture change (v0.1.61–0.1.63): native terminal
- The terminal is now **native** — `ui/native_terminal.py` = `NativeTerminal(QWidget)`
  built on `pyte` (screen model) + `QPainter` (drawing). It replaced xterm.js in
  QtWebEngine. `terminal_tab.py` wraps it.
- **`PyQt6-WebEngine` dependency was DROPPED** (v0.1.63) and the bundled xterm.js
  (`static/xterm/`) removed — installs no longer pull ~100 MB of Chromium.
- This fixed the long-running pain: typing latency, htop/btop blanking, general
  roughness. **Do NOT reintroduce a web/GPU renderer or output coalescing** — the
  native path renders directly and is smooth.
- Native terminal already covers: 16/256/truecolor, bold/italic/underline/reverse,
  cursor, run-batched dirty-rect painting, full keyboard, resize→PTY, mouse
  selection + copy-on-select (highlight is a cell bg so text stays readable),
  scrollback (wheel), a real alternate-screen buffer (vi/htop/btop), and
  `ESC[s`/`ESC[u` cursor save/restore (translated to DEC `ESC 7`/`ESC 8`).
- Watch-for rough edges (small targeted fixes): wide/CJK/emoji cell widths, rare
  escape sequences, alt-screen sequence split across two `feed()` calls.

## Where the code lives
- Repo root: `/data3/omniterm/`  (standalone git repo; project docs + code live together here)
- Package: `src/omniterm/` — see PRD §1 for the module map.
  - `core/`: `config.py`, `ssh_client.py`, `serial_client.py`, `local_pty.py`
  - `ui/`: `main_window.py`, `session_dock.py`, `sftp_browser.py`,
    `native_terminal.py`, `terminal_tab.py`, `theme.py`, `icons.py`
  - `static/icons/` (SVG set + app icon png/ico). NOTE: `static/xterm/` is gone.

## How to release (automatic — no go-ahead needed)
Credentials are now stored locally, so no token typing/URLs. From `/data3/omniterm/`:
1. Edit, then `python3 -m py_compile` the changed files. For UI, run an offscreen
   smoke test: `QT_QPA_PLATFORM=offscreen python3 ...` (instantiate widgets, feed
   data, `grab()` to force a paint — catches runtime errors headlessly).
2. Bump version in `pyproject.toml`.
3. `rm -rf dist build src/*.egg-info && python3 -m build`
4. `python3 -m twine upload dist/omniterm-<ver>*`   (reads `~/.pypirc`, `__token__`)
5. `git add -A && git commit && git tag v<ver> && git push origin main && git push origin v<ver>`
   (repo `credential.helper=store` supplies the GitHub token; author: fbobe3 <fbobe3@gmail.com>).
- Verify on the simple index (JSON endpoint lags): `curl -s https://pypi.org/simple/omniterm/ | grep <ver>`
- If twine/git auth fails, the stored token was likely rotated → ask the user for a fresh one
  and rewrite `~/.pypirc` / `~/.git-credentials`.

## Credentials
Stored locally (mode 600): PyPI token in `~/.pypirc` (`[pypi]`, `username=__token__`);
GitHub PAT in `~/.git-credentials` + omniterm repo `credential.helper=store`. Both tokens
were pasted in chat once, so they may get rotated — treat an auth failure as "token rotated,
ask for a new one." Never write raw token values into the repo / PRD / RESUME / artifacts.

## Backlog ideas (not committed)
- Drop the now-dead config funcs (`get_renderer`/`get_disable_gpu`/`get_native_terminal`) — harmless but unused.
- Folder create/rename in the session tree.
- Duplicate-shortcut detection warning in the Keyboard Shortcuts editor.
- Auto-cleanup of drag-out temp files (`%TEMP%/omniterm_sftp_*`) and
  edit temp files (`%TEMP%/omniterm_edit/*`).
- Terminal Appearance: cursor style/blink toggle now that we own rendering.

## Quick sanity check on resume
```bash
cd /data3/omniterm
grep '^version' pyproject.toml            # should be 0.1.86 (or newer)
python3 -m py_compile src/omniterm/main.py src/omniterm/core/*.py src/omniterm/ui/*.py
git status --short                        # should be clean
```
