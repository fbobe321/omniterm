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
        best = None
        best_score = -1.0
        for i, e in enumerate(entries):
            cmd = e.get("cmd", "")
            if cmd == buffer or not cmd.startswith(buffer):
                continue
            recency = i / n          # 0..1, higher = more recent
            score = recency
            if cwd and e.get("cwd") == cwd:
                score += 0.5
            if score >= best_score:  # ties resolve to the more recent entry
                best_score = score
                best = cmd
        return best
