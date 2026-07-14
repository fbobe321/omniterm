"""Native Qt terminal widget: pyte (screen model) + QPainter (rendering).

Renders the terminal grid directly with QPainter instead of xterm.js in a web
view, for native-terminal smoothness (no browser, no GPU context loss, minimal
input latency).
"""
import re
import pyte
from PyQt6.QtWidgets import QWidget, QApplication
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
        self._sel_anchor = None               # (row, col) in visible coords
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

        # cursor blink
        self._cursor_on = True
        self._blink = QTimer(self)
        self._blink.setInterval(600)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start()

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
        # Split around alternate-screen enter/leave so vi/htop draw on a
        # separate buffer and the shell is restored when they exit.
        pos = 0
        for m in _ALT_RE.finditer(text):
            self._feed_active(text[pos:m.start()])
            self._switch_alt(m.group(1) == "h")
            pos = m.end()
        self._feed_active(text[pos:])

        if self._scroll != 0:
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

        for row in range(row_start, row_end):
            line = lines[row]
            y = row * self._ch
            col = 0
            while col < self._cols:
                start_col = col
                first = line[col] if col in line else _BLANK
                style = (first.fg, first.bg, first.bold, first.italics,
                         first.underscore, first.reverse)
                run = []
                while col < self._cols:
                    c = line[col] if col in line else _BLANK
                    if (c.fg, c.bg, c.bold, c.italics, c.underscore, c.reverse) != style:
                        break
                    run.append(c.data if c.data else " ")
                    col += 1
                fg, bg, bold, italics, underscore, reverse = style
                fgc = self._color(fg, self._fg, bold)
                bgc = self._color(bg, self._bg)
                if reverse:
                    fgc, bgc = bgc, fgc
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

        self._paint_selection(p)
        self._paint_cursor(p)

    def _paint_cursor(self, p):
        if self._scroll != 0 or self._screen.cursor.hidden or not self._cursor_on:
            return
        cx = self._screen.cursor.x
        cy = self._screen.cursor.y
        if cx >= self._cols or cy >= self._rows:
            return
        rect = QRectF(cx * self._cw, cy * self._ch, self._cw, self._ch)
        p.fillRect(rect, self._cursor_color)
        # redraw glyph under the block cursor in the background colour
        ch = self._screen.buffer[cy][cx] if cx in self._screen.buffer[cy] else _BLANK
        if ch.data and ch.data.strip():
            p.setPen(self._bg)
            p.setFont(self._font)
            p.drawText(QPointF(cx * self._cw, cy * self._ch + self._ascent), ch.data)

    def _paint_selection(self, p):
        span = self._selection_span()
        if not span:
            return
        (r0, c0), (r1, c1) = span
        for row in range(r0, r1 + 1):
            cs = c0 if row == r0 else 0
            ce = c1 if row == r1 else self._cols
            p.fillRect(QRectF(cs * self._cw, row * self._ch,
                              (ce - cs) * self._cw, self._ch), self._sel_color)

    def _selection_span(self):
        if self._sel_anchor is None or self._sel_head is None:
            return None
        a, b = self._sel_anchor, self._sel_head
        if a == b:
            return None
        return (a, b) if a <= b else (b, a)

    # ---- mouse (selection + copy-on-select) ----
    def _cell_at(self, pos):
        col = max(0, min(self._cols, int(pos.x() / self._cw)))
        row = max(0, min(self._rows - 1, int(pos.y() / self._ch)))
        return (row, col)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._sel_anchor = self._cell_at(event.position())
            self._sel_head = self._sel_anchor
            self.update()
        self.setFocus()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._sel_anchor is not None:
            self._sel_head = self._cell_at(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._copy_selection()

    def mouseDoubleClickEvent(self, event):
        row, col = self._cell_at(event.position())
        line = self._visible_lines()[row]

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
        self._sel_anchor = (row, start)
        self._sel_head = (row, end + 1)
        self.update()
        self._copy_selection()

    def _selected_text(self):
        span = self._selection_span()
        if not span:
            return ""
        (r0, c0), (r1, c1) = span
        lines = self._visible_lines()
        out = []
        for row in range(r0, r1 + 1):
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

    # ---- scrollback ----
    def wheelEvent(self, event):
        if not hasattr(self._screen, "history"):
            return  # no scrollback on the alternate screen
        steps = event.angleDelta().y() / 120.0
        maxscroll = len(self._screen.history.top)
        self._scroll = int(max(0, min(maxscroll, self._scroll + steps * 3)))
        self.update()

    # ---- keyboard ----
    _SPECIAL = {
        Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r",
        Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
        Qt.Key.Key_Escape: "\x1b",
        Qt.Key.Key_Up: "\x1b[A", Qt.Key.Key_Down: "\x1b[B",
        Qt.Key.Key_Right: "\x1b[C", Qt.Key.Key_Left: "\x1b[D",
        Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F",
        Qt.Key.Key_PageUp: "\x1b[5~", Qt.Key.Key_PageDown: "\x1b[6~",
        Qt.Key.Key_Insert: "\x1b[2~", Qt.Key.Key_Delete: "\x1b[3~",
        Qt.Key.Key_F1: "\x1bOP", Qt.Key.Key_F2: "\x1bOQ",
        Qt.Key.Key_F3: "\x1bOR", Qt.Key.Key_F4: "\x1bOS",
        Qt.Key.Key_F5: "\x1b[15~", Qt.Key.Key_F6: "\x1b[17~",
        Qt.Key.Key_F7: "\x1b[18~", Qt.Key.Key_F8: "\x1b[19~",
        Qt.Key.Key_F9: "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~",
        Qt.Key.Key_F11: "\x1b[23~", Qt.Key.Key_F12: "\x1b[24~",
    }

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        # Ctrl+C copies when there is a selection; otherwise sends SIGINT (^C)
        if ctrl and key == Qt.Key.Key_C and self._selection_span():
            self._copy_selection()
            return
        if ctrl and key == Qt.Key.Key_V:
            cb = QGuiApplication.clipboard()
            if cb is not None:
                self.send_input.emit(cb.text())
            self._scroll = 0
            return

        seq = self._SPECIAL.get(key)
        if seq is None:
            if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                seq = chr(key - Qt.Key.Key_A + 1)  # ^A..^Z
            elif event.text():
                seq = event.text()
        if seq:
            if self._scroll != 0:
                self._scroll = 0
                self.update()
            self.send_input.emit(seq)
        else:
            super().keyPressEvent(event)
