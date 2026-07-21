# OmniTerm Shadow Command Predictor — Implementation Plan

Companion to `cmd_helper_PRD.md`. Reflects the decisions taken 2026-07-14.
Target repo: `/data3/mobax/omniterm/` (native terminal, `pyte` + `QPainter`).

## Locked decisions
1. **Engine: history-first, neural later.** Ship a zero-ML history/frequency
   predictor (fish / zsh-autosuggestions style) now. The 30–40M ONNX model from
   the PRD becomes an *optional* Phase 3, pursued only if the UX proves out.
2. **Buffer source: reconstruct from the screen.** Parse the current command
   line locally from the `pyte` screen — no remote install, uniform across
   SSH / serial / local / Home. Accept the fragility of prompt detection as the
   main technical risk (see Phase 0).
3. **Accept key: Right-arrow at end of line** (fish-style). Tab stays shell
   completion. No key conflict.

## Why this order (vs. the PRD)
The PRD front-loads the model, but the hard, uncertain part is reconstructing a
command line from an emulator that only sees echoed output. We de-risk that
first with a trivial predictor, prove the UX, and only then (maybe) invest weeks
in ML. Every phase ships something usable.

Note: this overlaps with the existing **Inshellisense** (`is`) integration
(shell-side ghost text). Make the two mutually exclusive per tab.

---

## Phase 0 — De-risk the buffer (spike)  — DONE 2026-07-14
**Goal:** prove we can reconstruct `(prompt, buffer, cursor_col)` from the
`pyte` screen reliably enough to build on. No user-facing change.

**Implemented** in `ui/native_terminal.py` behind `OMNITERM_PREDICT_DEBUG`:
`_reconstruct_line()` + `_line_text()` + `_debug_predict()`, plus a headless
battery harness (`scratchpad/spike_recon.py`). Prompt-end detection prefers a
"strong" terminator (`$ # % ❯ ➜ » ›`) and falls back to a "weak" `>`
(Windows/PowerShell) only when no strong one is present.

**Result: 11/13 scenarios correct**, including redirection (`ls > out` keeps the
whole command — strong `$` beats weak `>`), `$VAR` (not mistaken for a prompt),
starship `❯`, Windows/PowerShell `>`, and mid-line-edit cursor tracking inside
the buffer. No-prompt output correctly yields `<none>` (suppress).

**Known failures (both anticipated):**
1. **Leading-symbol prompts** (oh-my-zsh `➜  proj git:(main) `): the terminator
   leads the prompt, so decoration is swallowed into the buffer. Guessing from
   symbols is fundamentally ambiguous here.
2. **Line-wrapped commands**: cursor lands on a continuation row with no prompt.

**Decision for Phase 1 — stop guessing from symbols.** Capture the cursor column
at the moment the *first keystroke of a new line* is sent (right after the
prompt has been echoed): that column *is* the prompt end, prompt-agnostic, and
fixes both failure cases. Keep symbol-based `_reconstruct_line()` as a fallback
for the pre-first-keystroke state and for resynchronizing after scroll/clear.
Handle wrapping by walking pyte's wrapped-line continuation from the captured
prompt-end row.

**Exit criterion: met.** Core mechanism proven; failure modes enumerated and
degrade gracefully (suppress, never corrupt input); Phase 1 mechanism identified.

## Phase 1 — History-based shadow suggestions, end to end  — DONE 2026-07-14
**Shipped (all verified headlessly, predictor OFF by default):**
- `core/command_history.py` (new): `CommandHistory` records submitted commands
  to `~/.local/share/omniterm/history.jsonl` (dedups immediate repeats, caps +
  compacts). **`is_sensitive_command()` + a hard guard in `record()` mean a
  secret is never persisted, so it can never be suggested.**
- `core/predictor.py` (new): `Predictor` interface + `HistoryPredictor`
  (recency-dominant prefix match with a cwd-match boost).
- `ui/native_terminal.py`: **prompt-end column captured at the first keystroke**
  of each line (`_note_forwarded`) — prompt-agnostic, no symbol guessing;
  `_current_buffer` / `_update_prediction` / `_set_shadow`; dim shadow rendered
  in `_paint_shadow`; **Right-arrow at line end accepts** (`_accept_shadow`);
  Enter records + resets, Ctrl+C resets.
- `core/config.py`: `get/set_shadow_predictor()` (`enabled` default False,
  `min_prefix`, `history_depth`, `accept_key`).
- `ui/terminal_tab.py`: `apply_settings` reloads predictor config; worker
  `cwd_changed` (OSC 7) wired to `terminal.set_cwd`.

**Secrets / privacy (hardened per user request):**
1. **No-echo password prompts** — echo-detection: printable keys forwarded but
   nothing echoes ⇒ `_masked`; suggestions AND history recording suspended for
   that line.
2. **Inline secrets in visible commands** (`mysql -pX`, `export TOKEN=…`,
   `Authorization: Bearer …`, `--password=…`, pasted `-----BEGIN` keys) — the
   line is neither suggested nor recorded, via `is_sensitive_command()`.

**Enable for now (Settings UI is Phase 2):** set `"shadow_predictor":
{"enabled": true}` in `~/.omniterm_global.json`.

**Deferred to Phase 2:** wrapped/multiline command lines (currently suppressed),
accept-one-word, per-directory weighting UI.

### Phase 1 original scope (for reference)
**New:** `core/command_history.py`
- Record each command at the moment Enter is sent: `{cmd, cwd, ts}`.
  `cwd` comes from the existing OSC 7 `cwd_changed` signal (`ssh_client.py`).
- Persist to `~/.local/share/omniterm/history.jsonl` (append-only, capped/rotated).
- Query by prefix, ranked by recency + frequency + cwd affinity.
- Sourcing from our own sent-input keeps it uniform across all session types.

**New:** `core/predictor.py`
- `class Predictor` interface: `predict(buffer, cwd) -> str | None` (returns the
  *full* predicted command; the shadow is the suffix after `buffer`).
- `class HistoryPredictor(Predictor)` — prefix match over `command_history`.

**Edit:** `ui/native_terminal.py`
- Hold `self._shadow` (predicted suffix) + the reconstructed buffer.
- Recompute on keystroke / output, debounced. Phase-1 predict is <1 ms so it can
  run on the UI thread; keep the call site swappable for the Phase-3 QThread.
- `paintEvent`: draw `self._shadow` dim, immediately after the cursor. Pure
  overlay — never written into the pyte grid, cleared on next keystroke/output.
- `keyPressEvent`: **Right-arrow at end of line with a shadow present** →
  emit the remaining chars via `send_input` instead of the cursor escape.
- **Privacy pause (echo-detection):** if we send a printable key and the cursor
  cell doesn't change on the next paint, we're in a masked / no-echo state
  (password prompt) → suspend prediction *and* stop recording history.

**Edit:** `core/config.py` (follow existing `get_/set_` pattern)
- `shadow_predictor.enabled` (default **off** until validated)
- `shadow_predictor.aggressiveness` (min prefix length / confidence to render)
- `shadow_predictor.history_depth` (context window for ranking)
- `shadow_predictor.accept_key` (default Right-arrow)

## Phase 2 — Polish  — DONE 2026-07-14
**Shipped (verified headlessly):**
- **Wrapped-line support**: `_current_buffer` now splices the prompt row +
  full intermediate rows + the cursor row, so a command wrapping across rows
  reconstructs exactly (tested: a 95-char command over 2 rows == typed text).
  `_paint_shadow` wraps the suggestion onto following rows too.
- **Accept-one-word (Ctrl+Right)**: `_accept_shadow_word` emits leading spaces +
  the next word; plain Right still accepts the whole suggestion.
- **Settings menu toggle**: "Command Prediction (Shadow Text)" checkable action
  in `main_window.py` (`_toggle_shadow_predictor`) — persists to config and
  live-reloads open tabs via `apply_terminal_settings_to_open_tabs()`.

**Post-ship field fixes (v0.1.68):**
- **SSH:** suggestions only worked on Home/local — masked-input detection latched
  on SSH echo latency. Now a live check that clears the instant any char echoes.
- **Tab:** must stay shell completion. Accept keys are **Ctrl+F / End / Right**
  (whole) and **Ctrl+Right** (word); Tab always passes through.
- Added `tests/test_shadow_predictor.py` (16 tests, incl. SSH-latency regression).

**Still deferred (Phase 2.x / later, low priority):**
- Aggressiveness→confidence knob in the UI (min_prefix is config-only for now).
- Mutually-exclusive-with-Inshellisense enforcement (they can both run; they
  don't conflict functionally, just visually overlap).
- Mid-line-edit suggestions (still suppressed unless cursor at end).

## Phase 3 — Neural predictor (OPTIONAL, gated on Phase 1/2 success)
Only if the history UX proves worth more. Same `Predictor` interface:
- `class OnnxPredictor(Predictor)` using `onnxruntime` (CPU EP).
- Ship as an extra: `pip install omniterm[ai]` →
  `[project.optional-dependencies] ai = ["onnxruntime"]` in `pyproject.toml`.
- **Lazy download** the int8 `.onnx` from a GitHub Release asset on first enable,
  cache in `~/.local/share/omniterm/models/`. Do not bloat the wheel.
- Run inference in a `QThread` with debounce + stale-request cancellation to hold
  the PRD's `<50ms` budget; **fall back to `HistoryPredictor`** whenever the
  model is absent or too slow.
- All training / custom CLI BPE tokenizer / ONNX export / quantization work lives
  in this phase (out of the app repo).

---

## Primary risks
- **Prompt/buffer reconstruction fragility** (Phase 0 is the gate). Mitigation:
  conservative — suppress the suggestion rather than risk wrong Tab-populate.
- **Masked-input leakage.** Echo-detection must be proven before any history
  recording ships; err toward not recording.
- **Latency on the neural path.** Off-thread + debounce + fallback; treat `<50ms`
  as best-effort for Phase 3, guaranteed only for the history engine.

## Touchpoints summary
| File | Change |
|------|--------|
| `core/command_history.py` | NEW — record/persist/query executed commands |
| `core/predictor.py` | NEW — `Predictor` iface + `HistoryPredictor` (+ `OnnxPredictor` P3) |
| `ui/native_terminal.py` | buffer reconstruction, shadow render, accept key, privacy pause |
| `core/config.py` | `shadow_predictor` settings block |
| `ui/main_window.py` | Settings → Shadow Predictor panel (P2) |
| `pyproject.toml` | `[ai]` optional extra (P3) |
