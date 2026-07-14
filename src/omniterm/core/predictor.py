"""Shadow command predictors.

``Predictor`` is the swappable interface: given the currently-typed buffer and
(optionally) the working directory, return the *full* predicted command, or
None. The shadow text rendered in the terminal is the suffix after the buffer.

Phase 1 ships ``HistoryPredictor`` (zero-ML, fish / zsh-autosuggestions style).
A future ``OnnxPredictor`` can implement the same interface behind an extra.
"""


class Predictor:
    def predict(self, buffer, cwd=None):
        raise NotImplementedError


class HistoryPredictor(Predictor):
    """Predict the next command from recorded history by prefix match.

    Ranking is recency-dominant (what fish does) with a boost for entries that
    share the current working directory, so directory-specific commands surface
    when relevant without overriding a very recent match.
    """

    def __init__(self, history):
        self._history = history

    def predict(self, buffer, cwd=None):
        if not buffer:
            return None
        entries = self._history.entries()
        n = len(entries)
        if not n:
            return None
        # Aggregate matches by command: most-recent position, how often it was
        # run, and whether it was ever run in the current directory.
        agg = {}   # cmd -> [recency (0..1), count, cwd_match]
        for i, e in enumerate(entries):
            cmd = e.get("cmd", "")
            if cmd == buffer or not cmd.startswith(buffer):
                continue
            recency = i / n
            cwd_match = bool(cwd and e.get("cwd") == cwd)
            info = agg.get(cmd)
            if info is None:
                agg[cmd] = [recency, 1, cwd_match]
            else:
                info[0] = max(info[0], recency)
                info[1] += 1
                info[2] = info[2] or cwd_match
        if not agg:
            return None
        # Recency dominates (fish-like: what you did last is what you usually
        # want). A directory match is a strong boost; frequency is only a mild
        # nudge that breaks near-ties toward habitual commands.
        best = None
        best_key = None
        for cmd, (recency, count, cwd_match) in agg.items():
            score = recency
            if cwd_match:
                score += 0.5
            score += 0.1 * min(count, 10) / 10.0
            key = (score, recency)   # tie-break toward the more recent command
            if best_key is None or key > best_key:
                best_key = key
                best = cmd
        return best
