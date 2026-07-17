"""Native Qt terminal widget: pyte (screen model) + QPainter (rendering).

Renders the terminal grid directly with QPainter instead of xterm.js in a web
view, for native-terminal smoothness (no browser, no GPU context loss, minimal
input latency).
"""
import os
import re
import pyte
from PyQt6.QtWidgets import QWidget, QApplication, QMenu
from PyQt6.QtGui import QPainter, QFont, QFontMetricsF, QColor, QGuiApplication
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QTimer

# Alternate-screen enter/leave sequences (vi, htop, btop, less, ...)
_ALT_RE = re.compile(r'\x1b\[\?(?:1049|1047|47)([hl])')

# 16-colour ANSI palette (matches the web terminal theme)
_BASE = {
    "black": "#2a2e37", "red": "#f26d78", "green": "#7ee081", "brown": "#f4d160",
    "blue": "#61a7f0", "magenta": "#c792ea", "cyan": "#56d4c2", "white": "#cdd3dd",
}
_BRIGHT = {
    "black": "#5a626f", "red": "#ff8790", "green": "#9af09c", "brown": "#ffe58a",
    "blue": "#8cc4ff", "magenta": "#dcb1ff", "cyan": "#7fecdd", "white": "#ffffff",
}

_BLANK = pyte.screens.Char(data=" ", fg="default", bg="default", bold=False,
                           italics=False, underscore=False, strikethrough=False,
                           reverse=False, blink=False)


class NativeTerminal(QWidget):
    send_input = pyqtSignal(str)   # user input to forward to the shell
    resized = pyqtSignal(int, int)  # cols, rows (for PTY resize)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)

        self._fg = QColor("#e6e9ee")
        self._bg = QColor("#181a1f")
        self._cursor_color = QColor("#38bdf8")
        self._sel_color = QColor("#2b4b63")

        self._cols, self._rows = 80, 24
        self._scroll = 0                      # lines scrolled up (0 = bottom)
        # Selection endpoints are stored in *document* coords (an index into
        # history + the live buffer), so they stay put while the view scrolls -
        # which lets a drag-selection span more than one screen of scrollback.
        self._sel_anchor = None               # (doc_row, col)
        self._sel_head = None
        self._color_cache = {}

        # Main screen (with scrollback) + a separate alternate screen (vi/htop).
        self._main_screen = pyte.HistoryScreen(self._cols, self._rows, history=5000, ratio=0.5)
        self._alt_screen = pyte.Screen(self._cols, self._rows)
        self._main_stream = pyte.Stream(self._main_screen)
        self._alt_stream = pyte.Stream(self._alt_screen)
        self._screen = self._main_screen
        self._stream = self._main_stream
        self._in_alt = False

        self._set_font("Consolas, monospace", 12)

        # Shadow-predictor Phase 0 spike: when OMNITERM_PREDICT_DEBUG is set,
        # reconstruct the current command line from the screen and log it.
        self._predict_debug = bool(os.environ.get("OMNITERM_PREDICT_DEBUG"))

        # Shadow predictor (Phase 1). Command-line tracking uses a prompt-end
        # column captured at the first keystroke of each line (prompt-agnostic),
        # not symbol guessing. Prediction is history-based; the suggestion is
        # drawn as dim shadow text and accepted with Right-arrow at line end.
        self._cwd = None
        self._shadow = ""            # predicted suffix rendered after the cursor
        self._shadow_color = QColor("#6b7280")
        self._line_active = False    # are we tracking an editable prompt line?
        self._prompt_row = 0
        self._prompt_col = 0         # where the command starts on _prompt_row
        self._typed_count = 0        # printable keys forwarded this line
        self._masked = False         # no-echo (password) input on this line
        self._history = None
        self._predictor = None
        self._predict_cfg = {"enabled": False, "min_prefix": 1}
        self._load_predict_settings()

        # cursor blink
        self._cursor_on = True
        self._cursor_row = 0         # row we last drew the cursor on (to erase it)
        self._blink = QTimer(self)
        self._blink.setInterval(600)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start()

        # Auto-scroll while drag-selecting past the top/bottom edge, so a
        # selection can be dragged across earlier/later pages of scrollback.
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(40)
        self._autoscroll.timeout.connect(self._autoscroll_tick)
        self._autoscroll_dir = 0              # +1 = older (up), -1 = newer (down)
        self._last_drag_pos = None            # latest drag position, for the timer

    # ---- appearance ----
    def _set_font(self, family, size):
        self._font = QFont()
        # family may be a CSS-style list; take the first name
        self._font.setFamily(family.split(",")[0].strip().strip("'\""))
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self._font.setPointSizeF(float(size))
        self._fm = QFontMetricsF(self._font)
        self._cw = max(1.0, self._fm.horizontalAdvance("W"))
        self._ch = max(1.0, self._fm.height())
        self._ascent = self._fm.ascent()

    def apply_appearance(self, family=None, size=None, fg=None, bg=None):
        if fg:
            self._fg = QColor(fg)
        if bg:
            self._bg = QColor(bg)
        if family or size:
            self._set_font(family or self._font.family(),
                           size if size else self._font.pointSizeF())
        self._color_cache.clear()
        self._recompute_size()
        self.update()

    # ---- feeding output ----
    def feed(self, text):
        # Remember scrollback depth so we can keep a scrolled-up view pinned.
        before_hist = len(self._screen.history.top) \
            if hasattr(self._screen, "history") else 0

        # Split around alternate-screen enter/leave so vi/htop draw on a
        # separate buffer and the shell is restored when they exit.
        pos = 0
        for m in _ALT_RE.finditer(text):
            self._feed_active(text[pos:m.start()])
            self._switch_alt(m.group(1) == "h")
            pos = m.end()
        self._feed_active(text[pos:])

        self._update_prediction()
        if self._predict_debug:
            self._debug_predict()

        # Output usually means the cursor just moved (e.g. every keystroke in
        # vi). Make it solid and restart the blink phase so it's visible on the
        # new spot immediately, instead of possibly sitting in the "off" half of
        # an independent blink cycle for up to a full interval.
        self._wake_cursor()

        if self._scroll != 0 and hasattr(self._screen, "history"):
            # User is scrolled up reading history: keep the view pinned to the
            # same lines as new output pushes content into scrollback, instead
            # of yanking the view back down to the bottom.
            added = len(self._screen.history.top) - before_hist
            if added > 0:
                self._scroll = min(self._scroll + added,
                                   len(self._screen.history.top))
            self._screen.dirty.clear()
            self.update()
            return
        if self._scroll != 0:
            # Alternate screen (no scrollback) — can't pin; show the live view.
            self._scroll = 0
            self._screen.dirty.clear()
            self.update()
            return
        dirty = self._screen.dirty
        if len(dirty) >= self._rows:
            self.update()
        else:
            for y in dirty:
                self.update(0, int(y * self._ch), self.width(), int(self._ch) + 2)
        dirty.clear()

    def _feed_active(self, text):
        if not text:
            return
        # pyte ignores ANSI.SYS save/restore cursor (ESC[s / ESC[u); translate
        # to the DEC form (ESC 7 / ESC 8) which it handles. Inshellisense relies
        # on this to position its suggestion box correctly.
        if "\x1b[s" in text or "\x1b[u" in text:
            text = text.replace("\x1b[s", "\x1b7").replace("\x1b[u", "\x1b8")
        try:
            self._stream.feed(text)
        except Exception:
            pass

    def _switch_alt(self, enter):
        if enter == self._in_alt:
            return
        self._in_alt = enter
        self._scroll = 0
        if enter:
            self._alt_screen.reset()
            self._alt_screen.resize(self._rows, self._cols)
            self._screen = self._alt_screen
            self._stream = self._alt_stream
        else:
            self._screen = self._main_screen
            self._stream = self._main_stream
            self._main_screen.dirty.update(range(self._rows))
        self.update()

    def _toggle_cursor(self):
        self._cursor_on = not self._cursor_on
        cy = self._screen.cursor.y
        self.update(0, int(cy * self._ch), self.width(), int(self._ch) + 2)

    def _wake_cursor(self):
        """Keep the cursor visible as it moves. A pure cursor move (arrow keys in
        vi, say) changes no line content, so pyte marks nothing dirty and feed()'s
        dirty-based repaint would skip the cursor entirely - it would only reappear
        on the next 600ms blink tick. So force it solid, restart the blink phase,
        and explicitly repaint both the row it left and the row it moved to (full
        width, which also covers horizontal moves within a row)."""
        self._cursor_on = True
        self._blink.start()  # restart so it stays solid while actively moving
        cy = self._screen.cursor.y
        for y in {self._cursor_row, cy}:
            if 0 <= y < self._rows:
                self.update(0, int(y * self._ch), self.width(), int(self._ch) + 2)
        self._cursor_row = cy

    # ---- sizing ----
    def resizeEvent(self, event):
        self._recompute_size()
        super().resizeEvent(event)

    def _recompute_size(self):
        cols = max(2, int(self.width() / self._cw))
        rows = max(1, int(self.height() / self._ch))
        if cols != self._cols or rows != self._rows:
            self._cols, self._rows = cols, rows
            for scr in (self._main_screen, self._alt_screen):
                try:
                    scr.resize(rows, cols)
                except Exception:
                    pass
            self.resized.emit(cols, rows)

    # ---- colours ----
    def _color(self, name, default, bold=False):
        if name == "default":
            return default
        key = (name, bold)
        c = self._color_cache.get(key)
        if c is None:
            table = _BRIGHT if bold else _BASE
            if name in table:
                c = QColor(table[name])
            elif len(name) == 6:
                c = QColor("#" + name)
            else:
                c = default
            self._color_cache[key] = c
        return c

    def _visible_lines(self):
        buf = self._screen.buffer
        if self._scroll == 0 or not hasattr(self._screen, "history"):
            return [buf[y] for y in range(self._rows)]
        hist = list(self._screen.history.top)
        alllines = hist + [buf[y] for y in range(self._rows)]
        total = len(alllines)
        start = max(0, total - self._rows - self._scroll)
        window = alllines[start:start + self._rows]
        while len(window) < self._rows:
            window.append({})
        return window

    # ---- painting ----
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), self._bg)
        lines = self._visible_lines()
        r = event.rect()
        row_start = max(0, int(r.top() // self._ch))
        row_end = min(self._rows, int(r.bottom() // self._ch) + 1)
        base_font = self._font

        span = self._selection_span()
        doc_top = self._doc_top()
        for row in range(row_start, row_end):
            line = lines[row]
            y = row * self._ch
            # selected column range for this row (folded into the cell bg so
            # text renders on top and stays readable). The span is in document
            # coords, so map this visible row to its document row to compare.
            sel_cs = sel_ce = -1
            if span:
                doc = doc_top + row
                (sr0, sc0), (sr1, sc1) = span
                if sr0 <= doc <= sr1:
                    sel_cs = sc0 if doc == sr0 else 0
                    sel_ce = sc1 if doc == sr1 else self._cols
            col = 0
            while col < self._cols:
                start_col = col
                first = line[col] if col in line else _BLANK
                sel0 = sel_cs <= col < sel_ce
                style = (first.fg, first.bg, first.bold, first.italics,
                         first.underscore, first.reverse, sel0)
                run = []
                while col < self._cols:
                    c = line[col] if col in line else _BLANK
                    sel = sel_cs <= col < sel_ce
                    if (c.fg, c.bg, c.bold, c.italics, c.underscore, c.reverse, sel) != style:
                        break
                    run.append(c.data if c.data else " ")
                    col += 1
                fg, bg, bold, italics, underscore, reverse, selected = style
                fgc = self._color(fg, self._fg, bold)
                bgc = self._color(bg, self._bg)
                if reverse:
                    fgc, bgc = bgc, fgc
                if selected:
                    bgc = self._sel_color
                x = start_col * self._cw
                width = (col - start_col) * self._cw
                if bgc.rgb() != self._bg.rgb():
                    p.fillRect(QRectF(x, y, width, self._ch), bgc)
                if bold or italics or underscore:
                    f = QFont(base_font)
                    f.setBold(bold)
                    f.setItalic(italics)
                    f.setUnderline(underscore)
                    p.setFont(f)
                else:
                    p.setFont(base_font)
                text = "".join(run)
                if text.strip():
                    p.setPen(fgc)
                    p.drawText(QPointF(x, y + self._ascent), text)

        self._paint_shadow(p)
        self._paint_cursor(p)

    def _paint_shadow(self, p):
        """Draw the predicted suffix dim, starting at the cursor cell and
        wrapping onto following rows like real input would."""
        if not self._shadow or self._scroll != 0:
            return
        cx = self._screen.cursor.x
        cy = self._screen.cursor.y
        if cy >= self._rows or cx >= self._cols:
            return
        p.setFont(self._font)
        p.setPen(self._shadow_color)
        text = self._shadow
        col, row, i = cx, cy, 0
        while i < len(text) and row < self._rows:
            avail = self._cols - col
            chunk = text[i:i + avail]
            p.drawText(QPointF(col * self._cw, row * self._ch + self._ascent), chunk)
            i += len(chunk)
            row += 1
            col = 0

    def _paint_cursor(self, p):
        if self._scroll != 0 or self._screen.cursor.hidden or not self._cursor_on:
            return
        cx = self._screen.cursor.x
        cy = self._screen.cursor.y
        if cx >= self._cols or cy >= self._rows:
            return
        rect = QRectF(cx * self._cw, cy * self._ch, self._cw, self._ch)
        p.fillRect(rect, self._cursor_color)
        # Redraw the glyph under the block cursor in the background colour. When
        # a suggestion is showing, the cell is blank but the cursor sits on the
        # first shadow character — draw it so the whole suggestion stays
        # readable instead of the cursor block hiding its first letter.
        ch = self._screen.buffer[cy][cx] if cx in self._screen.buffer[cy] else _BLANK
        glyph = ch.data if (ch.data and ch.data.strip()) else \
            (self._shadow[0] if self._shadow else "")
        if glyph:
            p.setPen(self._bg)
            p.setFont(self._font)
            p.drawText(QPointF(cx * self._cw, cy * self._ch + self._ascent), glyph)

    def _selection_span(self):
        if self._sel_anchor is None or self._sel_head is None:
            return None
        a, b = self._sel_anchor, self._sel_head
        if a == b:
            return None
        return (a, b) if a <= b else (b, a)

    # ---- document coordinates (stable across scrolling) ----
    def _doc_lines(self):
        """The whole document as a list of line dicts: scrollback history
        followed by the current on-screen buffer."""
        buf = self._screen.buffer
        rows = [buf[y] for y in range(self._rows)]
        if hasattr(self._screen, "history"):
            return list(self._screen.history.top) + rows
        return rows

    def _doc_top(self):
        """Document index of the top visible row for the current scroll offset."""
        hist = len(self._screen.history.top) if hasattr(self._screen, "history") else 0
        return max(0, hist - self._scroll)

    # ---- mouse (selection + copy-on-select) ----
    def _cell_at(self, pos):
        col = max(0, min(self._cols, int(pos.x() / self._cw)))
        row = max(0, min(self._rows - 1, int(pos.y() / self._ch)))
        return (row, col)

    def _cell_at_doc(self, pos):
        """Mouse position -> (doc_row, col). The visible row is clamped to the
        widget, so dragging above/below the edge sticks to the first/last row."""
        row, col = self._cell_at(pos)
        return (self._doc_top() + row, col)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._stop_autoscroll()
            self._last_drag_pos = event.position()
            self._sel_anchor = self._cell_at_doc(event.position())
            self._sel_head = self._sel_anchor
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._paste()  # X11/PuTTY-style middle-click paste
        self.setFocus()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._sel_anchor is not None:
            pos = event.position()
            self._last_drag_pos = pos
            self._sel_head = self._cell_at_doc(pos)
            self._update_autoscroll(pos)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._stop_autoscroll()
            self._copy_selection()

    # ---- auto-scroll while drag-selecting past an edge ----
    def _update_autoscroll(self, pos):
        """Start/stop edge auto-scroll based on where the drag is. Dragging into
        (or past) the top or bottom of the widget scrolls history under the
        selection; releasing or dragging back inside stops it."""
        if not hasattr(self._screen, "history"):
            self._stop_autoscroll()
            return
        margin = self._ch  # within one row of an edge (or beyond) triggers it
        y = pos.y()
        if y < margin:
            self._autoscroll_dir = 1          # reveal older lines
        elif y > self.height() - margin:
            self._autoscroll_dir = -1         # reveal newer lines
        else:
            self._stop_autoscroll()
            return
        if not self._autoscroll.isActive():
            self._autoscroll.start()

    def _stop_autoscroll(self):
        self._autoscroll_dir = 0
        self._autoscroll.stop()

    def _autoscroll_tick(self):
        if self._autoscroll_dir == 0 or self._sel_anchor is None \
                or not hasattr(self._screen, "history"):
            self._stop_autoscroll()
            return
        maxscroll = len(self._screen.history.top)
        step = 2
        new = self._scroll + (step if self._autoscroll_dir > 0 else -step)
        new = max(0, min(maxscroll, new))
        if new == self._scroll:
            self._stop_autoscroll()           # reached the top/bottom of history
            return
        self._scroll = new
        # Extend the selection head to the edge cell the mouse is held at; as the
        # view scrolled, that cell now maps to a new document row.
        if self._last_drag_pos is not None:
            self._sel_head = self._cell_at_doc(self._last_drag_pos)
        self.update()

    def mouseDoubleClickEvent(self, event):
        doc_row, col = self._cell_at_doc(event.position())
        lines = self._doc_lines()
        if doc_row < 0 or doc_row >= len(lines):
            return
        line = lines[doc_row]

        def is_word(c):
            return c.isalnum() or c in "_.-/~"
        text = [(line[i].data if i in line else " ") for i in range(self._cols)]
        if col >= len(text) or not is_word(text[col]):
            return
        start = col
        while start > 0 and is_word(text[start - 1]):
            start -= 1
        end = col
        while end < self._cols - 1 and is_word(text[end + 1]):
            end += 1
        self._sel_anchor = (doc_row, start)
        self._sel_head = (doc_row, end + 1)
        self.update()
        self._copy_selection()

    def _selected_text(self):
        span = self._selection_span()
        if not span:
            return ""
        (r0, c0), (r1, c1) = span
        lines = self._doc_lines()
        n = len(lines)
        out = []
        for row in range(r0, r1 + 1):
            if row < 0 or row >= n:
                continue
            line = lines[row]
            cs = c0 if row == r0 else 0
            ce = c1 if row == r1 else self._cols
            s = "".join((line[i].data if i in line and line[i].data else " ")
                        for i in range(cs, ce))
            out.append(s.rstrip())
        return "\n".join(out)

    def _copy_selection(self):
        text = self._selected_text()
        if text:
            cb = QGuiApplication.clipboard()
            if cb is not None:
                cb.setText(text)

    def _extend_keyboard_selection(self, key):
        # Keyboard text selection (Shift+Arrow/Home/End), seeded at the cursor.
        # Works in document coords, kept within the visible page.
        if self._sel_anchor is None or self._sel_head is None:
            if self._scroll != 0:
                self._scroll = 0
            hist = len(self._screen.history.top) \
                if hasattr(self._screen, "history") else 0
            origin = (hist + self._screen.cursor.y, self._screen.cursor.x)
            self._sel_anchor = origin
            self._sel_head = origin
        top = self._doc_top()
        bot = top + self._rows - 1
        row, col = self._sel_head
        if key == Qt.Key.Key_Left:
            col -= 1
        elif key == Qt.Key.Key_Right:
            col += 1
        elif key == Qt.Key.Key_Up:
            row -= 1
        elif key == Qt.Key.Key_Down:
            row += 1
        elif key == Qt.Key.Key_Home:
            col = 0
        elif key == Qt.Key.Key_End:
            col = self._cols
        # wrap horizontal movement across line boundaries
        if col < 0:
            if row > top:
                row, col = row - 1, self._cols
            else:
                col = 0
        elif col > self._cols:
            if row < bot:
                row, col = row + 1, 0
            else:
                col = self._cols
        row = max(top, min(bot, row))
        self._sel_head = (row, col)
        self._copy_selection()  # copy-on-select, matching mouse behaviour
        self.update()

    _BRACKETED_PASTE = 2004 << 5  # pyte encodes private mode 2004 as 2004<<5

    def _paste(self):
        cb = QGuiApplication.clipboard()
        if cb is None or not cb.text():
            return
        if self._scroll != 0:
            self._scroll = 0
            self.update()
        # Newlines in a paste are carriage returns (what Enter sends), not \n.
        text = cb.text().replace("\r\n", "\r").replace("\n", "\r")
        # If the app enabled bracketed paste (vim, bash/readline, ...), wrap the
        # text so it's treated as a paste — vim then skips autoindent, fixing the
        # "staircase" indentation on large pastes.
        if self._BRACKETED_PASTE in self._screen.mode:
            text = "\x1b[200~" + text + "\x1b[201~"
        self.send_input.emit(text)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        copy_act = menu.addAction("Copy")
        copy_act.setEnabled(self._selection_span() is not None)
        copy_act.triggered.connect(self._copy_selection)
        paste_act = menu.addAction("Paste")
        paste_act.setEnabled(bool(QGuiApplication.clipboard().text()))
        paste_act.triggered.connect(self._paste)
        menu.exec(event.globalPos())

    # ---- scrollback ----
    _MIN_FONT = 5.0
    _MAX_FONT = 48.0

    def wheelEvent(self, event):
        # Ctrl + wheel zooms the font (like MobaXterm / browsers).
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self._adjust_font_size(1.0 if delta > 0 else -1.0)
            event.accept()
            return
        if not hasattr(self._screen, "history"):
            return  # no scrollback on the alternate screen
        steps = event.angleDelta().y() / 120.0
        maxscroll = len(self._screen.history.top)
        self._scroll = int(max(0, min(maxscroll, self._scroll + steps * 3)))
        self.update()

    def _adjust_font_size(self, delta):
        cur = self._font.pointSizeF()
        new = max(self._MIN_FONT, min(self._MAX_FONT, cur + delta))
        if abs(new - cur) < 0.1:
            return
        self._set_font(self._font.family(), new)
        self._color_cache.clear()
        self._recompute_size()   # new cell size -> new cols/rows -> PTY resize
        self.update()
        self._schedule_font_persist(new)

    def _schedule_font_persist(self, size):
        # Remember the chosen size (debounced) so new terminals match.
        self._pending_font_size = size
        if not hasattr(self, "_font_persist_timer"):
            self._font_persist_timer = QTimer(self)
            self._font_persist_timer.setSingleShot(True)
            self._font_persist_timer.timeout.connect(self._persist_font_size)
        self._font_persist_timer.start(500)

    def _persist_font_size(self):
        try:
            from ..core import config
            config.set_terminal_settings({"fontSize": round(self._pending_font_size, 1)})
        except Exception:
            pass

    # ---- shadow predictor: command-line reconstruction (Phase 0 spike) ----
    # The remote shell owns line editing; we only see echoed output. To predict
    # (and later Tab-populate) the next command we must recover the current
    # command line from the pyte screen: locate where the prompt ends and read
    # from there to the end of the typed text.
    #
    # Prompt detection is heuristic and the known-fragile part of this feature.
    # We prefer a "strong" terminator ($ # % ❯ ➜ » ›) and fall back to the weak
    # ">" (Windows / PowerShell) only when no strong one is present, so that a
    # redirection like `ls > out` after a "$ " prompt isn't mistaken for a prompt.
    _PROMPT_STRONG = re.compile(r'[\$#%❯➜»›](?=\s)')
    _PROMPT_WEAK = re.compile(r'>(?=\s)')

    def _line_text(self, row):
        """Full text of a screen row on the active buffer."""
        line = self._screen.buffer[row]
        return "".join((line[i].data if i in line and line[i].data else " ")
                       for i in range(self._cols))

    def screen_text(self, scrollback=0):
        """The visible screen as plain text (for control/automation capture).
        `scrollback` prepends up to that many lines of history."""
        def render(line):
            return "".join((line[i].data if i in line and line[i].data else " ")
                           for i in range(self._cols)).rstrip()
        lines = []
        if scrollback and hasattr(self._screen, "history"):
            for line in list(self._screen.history.top)[-int(scrollback):]:
                lines.append(render(line))
        for y in range(self._rows):
            lines.append(render(self._screen.buffer[y]))
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _reconstruct_line(self):
        """Best-effort recovery of the current command line.

        Returns (prompt, buffer, cursor_col) where ``buffer`` is the typed
        command (may extend past the cursor if the user moved left) and
        ``cursor_col`` is the cursor's offset within it, or None if no prompt
        is found / we're on the alternate screen.

        Phase 0 limitation: single (unwrapped) screen line only.
        """
        if self._in_alt:
            return None
        cy = self._screen.cursor.y
        cx = self._screen.cursor.x
        if cy >= self._rows:
            return None
        text = self._line_text(cy)
        head = text[:cx]  # only look before the cursor for the prompt

        last = None
        for m in self._PROMPT_STRONG.finditer(head):
            last = m
        if last is None:
            for m in self._PROMPT_WEAK.finditer(head):
                last = m
        if last is None:
            return None

        end = last.end()  # just past the terminator
        while end < len(head) and head[end] == " ":
            end += 1  # skip the space(s) between prompt and command
        prompt = text[:end]
        buffer = text[end:].rstrip()
        cursor_col = max(0, cx - end)
        return prompt, buffer, cursor_col

    def _debug_predict(self):
        if not self._predict_debug:
            return
        try:
            r = self._reconstruct_line()
            path = os.path.expanduser("~/.omniterm_predict_debug.log")
            with open(path, "a", encoding="utf-8") as f:
                if r is None:
                    f.write("recon: <none>\n")
                else:
                    prompt, buffer, col = r
                    f.write("recon: prompt=%r buffer=%r cur=%d\n"
                            % (prompt, buffer, col))
        except Exception:
            pass

    # ---- shadow predictor: Phase 1 (history-based suggestions) ----
    def _load_predict_settings(self):
        """(Re)load predictor config and instantiate the engine. Safe to call
        repeatedly (e.g. after Settings changes); tolerant of missing config."""
        try:
            from ..core import config
            self._predict_cfg = config.get_shadow_predictor()
        except Exception:
            self._predict_cfg = {"enabled": False, "min_prefix": 1}
        if self._history is None:
            try:
                from ..core.command_history import CommandHistory
                self._history = CommandHistory()
            except Exception:
                self._history = None
        if self._history is not None and self._predictor is None:
            try:
                from ..core.predictor import HistoryPredictor
                self._predictor = HistoryPredictor(self._history)
            except Exception:
                self._predictor = None

    def _predict_on(self):
        return bool(self._predictor and self._predict_cfg.get("enabled"))

    def set_cwd(self, path):
        """Report the shell's working directory (from OSC 7) for cwd-aware
        ranking. Also invalidates any stale line tracking on a new prompt."""
        self._cwd = path

    def _current_buffer(self):
        """(buffer, at_end) for the active prompt line, or None.

        ``buffer`` is exactly the text typed left of the cursor (so a trailing
        space is preserved — it's a real prefix); ``at_end`` is True when nothing
        but padding sits to the right of the cursor. Uses the prompt-end column
        captured at the first keystroke — no symbol guessing.

        Handles a command that wraps onto continuation rows below the prompt row
        (Phase 2): each wrapped row is full width, so we splice the prompt row
        from the prompt column, whole intermediate rows, and the cursor row up
        to the cursor. A cursor above the prompt row (scroll/clear) yields None.
        """
        if not self._line_active or self._in_alt:
            return None
        cy = self._screen.cursor.y
        cx = self._screen.cursor.x
        if cy < self._prompt_row:
            return None
        if cy == self._prompt_row:
            if cx < self._prompt_col:
                return None
            text = self._line_text(cy)
            return text[self._prompt_col:cx], text[cx:].rstrip() == ""
        # Wrapped over multiple rows.
        parts = [self._line_text(self._prompt_row)[self._prompt_col:]]
        for r in range(self._prompt_row + 1, cy):
            parts.append(self._line_text(r))
        last = self._line_text(cy)
        parts.append(last[:cx])
        return "".join(parts), last[cx:].rstrip() == ""

    def _set_shadow(self, s):
        if s != self._shadow:
            self._shadow = s
            self.update()

    def _update_prediction(self):
        if not self._predict_on():
            self._set_shadow("")
            return
        cur = self._current_buffer()
        if cur is None:
            self._set_shadow("")
            return
        buffer, at_end = cur
        # Privacy: printable keys forwarded but nothing echoed => masked input
        # (a password prompt). This must be robust to network echo latency on
        # SSH, where keystrokes outrun their echo: a real password prompt NEVER
        # echoes, so as soon as any character appears we know it isn't masked.
        # Latch masked only while the buffer is still empty after several keys;
        # clear it the moment an echo arrives.
        if len(buffer) > 0:
            self._masked = False
        elif self._typed_count >= 3:
            self._masked = True
        if self._masked:
            self._set_shadow("")
            return
        # Only suggest with the cursor at end of line, past the min prefix, and
        # never off a line that carries an inline secret.
        min_prefix = self._predict_cfg.get("min_prefix", 1)
        if not at_end or len(buffer) < max(1, min_prefix):
            self._set_shadow("")
            return
        try:
            from ..core.command_history import is_sensitive_command
            if is_sensitive_command(buffer):
                self._set_shadow("")
                return
        except Exception:
            pass
        try:
            full = self._predictor.predict(buffer, self._cwd)
        except Exception:
            full = None
        if full and full.startswith(buffer) and len(full) > len(buffer):
            self._set_shadow(full[len(buffer):])
        else:
            self._set_shadow("")

    def _accept_shadow(self):
        text = self._shadow
        if not text:
            return
        self._shadow = ""
        self._typed_count += len(text)
        self.send_input.emit(text)   # echoes back; next feed re-predicts
        self.update()

    def _accept_shadow_word(self):
        """Accept one word of the suggestion (Ctrl+Right): leading spaces plus
        the next run of non-spaces. The remainder re-predicts on the echo."""
        s = self._shadow
        if not s:
            return
        i = 0
        while i < len(s) and s[i] == " ":
            i += 1
        while i < len(s) and s[i] != " ":
            i += 1
        chunk = s[:i]
        self._shadow = s[i:]
        self._typed_count += len(chunk)
        self.send_input.emit(chunk)
        self.update()

    def _record_command(self):
        """Record the just-submitted command (called on Enter). Skips masked
        input; the history store additionally drops anything secret-looking."""
        if self._masked or not self._predict_on():
            return
        cur = self._current_buffer()
        if not cur:
            return
        cmd = cur[0].strip()
        if cmd and self._history is not None:
            try:
                self._history.record(cmd, self._cwd)
            except Exception:
                pass

    def _reset_line(self):
        self._line_active = False
        self._typed_count = 0
        self._masked = False
        self._set_shadow("")

    def _note_forwarded(self, key, seq):
        """Maintain command-line tracking as input is forwarded to the shell."""
        if not self._line_active:
            # First key of a new line: the cursor sits at the prompt end.
            self._prompt_row = self._screen.cursor.y
            self._prompt_col = self._screen.cursor.x
            self._line_active = True
            self._typed_count = 0
            self._masked = False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._record_command()
            self._reset_line()
        elif seq == "\x03":          # Ctrl+C aborts the line
            self._reset_line()
        elif key == Qt.Key.Key_Backspace:
            self._typed_count = max(0, self._typed_count - 1)
        elif seq and len(seq) == 1 and seq.isprintable():
            self._typed_count += 1

    # ---- keyboard ----
    # Cursor keys depend on DECCKM (application cursor keys): SS3 (ESC O x) when
    # set, CSI (ESC [ x) when reset. inshellisense/readline/vi enable DECCKM and
    # rely on this for history/navigation.
    _DECCKM = 1 << 5  # pyte encodes private mode 1 as 1<<5
    _CURSOR = {
        Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C",
        Qt.Key.Key_Left: "D", Qt.Key.Key_Home: "H", Qt.Key.Key_End: "F",
    }
    _SEL_KEYS = {
        Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
        Qt.Key.Key_Home, Qt.Key.Key_End,
    }
    _SPECIAL = {
        Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r",
        Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
        Qt.Key.Key_Backtab: "\x1b[Z",  # Shift+Tab (reverse completion)
        Qt.Key.Key_Escape: "\x1b",
        Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
        Qt.Key.Key_Insert: "\x1b[2~", Qt.Key.Key_Delete: "\x1b[3~",
        Qt.Key.Key_F1: "\x1bOP", Qt.Key.Key_F2: "\x1bOQ",
        Qt.Key.Key_F3: "\x1bOR", Qt.Key.Key_F4: "\x1bOS",
        Qt.Key.Key_F5: "\x1b[15~", Qt.Key.Key_F6: "\x1b[17~",
        Qt.Key.Key_F7: "\x1b[18~", Qt.Key.Key_F8: "\x1b[19~",
        Qt.Key.Key_F9: "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~",
        Qt.Key.Key_F11: "\x1b[23~", Qt.Key.Key_F12: "\x1b[24~",
    }

    def focusNextPrevChild(self, next):
        # A terminal owns Tab and Shift+Tab (shell completion / reverse
        # completion). Returning False stops Qt from consuming them for focus
        # traversal between widgets — otherwise Tab would jump to the tab bar or
        # menu and never reach the shell. Qt then delivers the key to
        # keyPressEvent, where _SPECIAL sends "\t" / "\x1b[Z".
        return False

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        # Terminal-standard copy/paste (Ctrl+C stays SIGINT).
        if ctrl and shift and key == Qt.Key.Key_C:
            self._copy_selection()
            return
        if (ctrl and shift and key == Qt.Key.Key_V) or \
           (shift and key == Qt.Key.Key_Insert):
            self._paste()
            return
        # Ctrl+C copies when there is a selection; otherwise sends SIGINT (^C)
        if ctrl and not shift and key == Qt.Key.Key_C and self._selection_span():
            self._copy_selection()
            return
        if ctrl and not shift and key == Qt.Key.Key_V:
            self._paste()
            return

        # Shift+Arrow/Home/End extend a keyboard text selection instead of
        # sending a movement escape. A plain (non-shift) arrow collapses it.
        if shift and not ctrl and key in self._SEL_KEYS:
            self._extend_keyboard_selection(key)
            return
        if not shift and key in self._SEL_KEYS and self._sel_anchor is not None:
            self._sel_anchor = None
            self._sel_head = None
            self.update()

        # Accept the suggestion without leaving the home row. Tab is left ALONE
        # for shell filename/path completion (essential CLI function). Accept the
        # whole suggestion with Ctrl-F (fish convention; F is a home-row key),
        # End, or Right-arrow; accept one word with Ctrl-Right. The shadow is
        # only ever set with the cursor at line end, so these keys are no-ops
        # there otherwise and safe to repurpose.
        if self._shadow and not shift:
            if ctrl and key == Qt.Key.Key_Right:
                self._accept_shadow_word()
                return
            if (not ctrl and key in (Qt.Key.Key_Right, Qt.Key.Key_End)) or \
               (ctrl and key == Qt.Key.Key_F):
                self._accept_shadow()
                return

        seq = self._SPECIAL.get(key)
        if seq is None and key in self._CURSOR:
            prefix = "\x1bO" if self._DECCKM in self._screen.mode else "\x1b["
            seq = prefix + self._CURSOR[key]
        if seq is None:
            if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                seq = chr(key - Qt.Key.Key_A + 1)  # ^A..^Z
            elif event.text():
                seq = event.text()
        if seq:
            if self._scroll != 0:
                self._scroll = 0
                self.update()
            self._note_forwarded(key, seq)
            self.send_input.emit(seq)
        else:
            super().keyPressEvent(event)
