"""Widget terminala: emulator VT (pyte) + rysowanie w Qt + backend PTY.

Zastępuje Vte.Terminal, którego nie ma na Windowsie. Działa tak samo na Linuksie
(PTY ze stdlib) i Windowsie (ConPTY przez pywinpty).
"""
from __future__ import annotations

import logging
import sys
from collections import deque
from math import ceil

import pyte
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QMenu

from ..theme import ANSI_PALETTE, BLUE, FIELD, TEXT
from .backend import PtyBackend, create_backend

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# pyte trzyma prywatne tryby DEC jako (numer << 5)
_MODE_BRACKETED_PASTE = 2004 << 5
_MODE_APP_CURSOR = 1 << 5

_FONT_FAMILIES = [
    "JetBrains Mono", "Fira Code", "Cascadia Mono", "Cascadia Code",
    "DejaVu Sans Mono", "Consolas", "Menlo", "Monospace",
]

_ANSI_NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]


def _build_color_map() -> dict[str, QColor]:
    cmap: dict[str, QColor] = {}
    for i, name in enumerate(_ANSI_NAMES):
        cmap[name] = QColor(ANSI_PALETTE[i])
        cmap["bright" + name] = QColor(ANSI_PALETTE[i + 8])
    return cmap


_CSI_FINAL = {
    Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C", Qt.Key.Key_Left: "D",
    Qt.Key.Key_Home: "H", Qt.Key.Key_End: "F",
}
_TILDE = {
    Qt.Key.Key_Insert: 2, Qt.Key.Key_Delete: 3, Qt.Key.Key_PageUp: 5, Qt.Key.Key_PageDown: 6,
    Qt.Key.Key_F5: 15, Qt.Key.Key_F6: 17, Qt.Key.Key_F7: 18, Qt.Key.Key_F8: 19,
    Qt.Key.Key_F9: 20, Qt.Key.Key_F10: 21, Qt.Key.Key_F11: 23, Qt.Key.Key_F12: 24,
}
_SS3 = {Qt.Key.Key_F1: "P", Qt.Key.Key_F2: "Q", Qt.Key.Key_F3: "R", Qt.Key.Key_F4: "S"}
_PLAIN = {
    Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r", Qt.Key.Key_Backspace: "\x7f",
    Qt.Key.Key_Tab: "\t", Qt.Key.Key_Backtab: "\x1b[Z", Qt.Key.Key_Escape: "\x1b",
}
_CTRL_SYMBOLS = {
    Qt.Key.Key_Space: "\x00", Qt.Key.Key_BracketLeft: "\x1b", Qt.Key.Key_Backslash: "\x1c",
    Qt.Key.Key_BracketRight: "\x1d", Qt.Key.Key_AsciiCircum: "\x1e", Qt.Key.Key_Underscore: "\x1f",
    Qt.Key.Key_Minus: "\x1f",
}


class _Screen(pyte.Screen):
    """pyte.Screen + scrollback + odpowiedzi na zapytania terminala (np. ESC[6n)."""

    def __init__(self, columns: int, lines: int, history: int = 20000) -> None:
        self.scrollback: deque[dict] = deque(maxlen=history)
        self.responder = None
        super().__init__(columns, lines)

    def index(self) -> None:
        top, bottom = self.margins or (0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:
            self.scrollback.append(dict(self.buffer[0]))
        super().index()

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        if how == 3:  # ESC[3J — wyczyść też scrollback
            self.scrollback.clear()
        super().erase_in_display(how, *args, **kwargs)

    def write_process_input(self, data: str) -> None:
        # ConPTY i PSReadLine pytają o pozycję kursora — bez odpowiedzi potrafią "wisieć".
        if self.responder is not None:
            self.responder(data)


class TerminalWidget(QAbstractScrollArea):
    shell_exited = Signal(int)
    enter_pressed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

        self._bg = QColor(FIELD)
        self._fg = QColor(TEXT)
        self._cursor_color = QColor(BLUE)
        self._sel_bg = QColor(BLUE)
        self._colors = _build_color_map()
        self._color_cache: dict[str, QColor] = {}

        self._init_fonts(11)

        self._screen = _Screen(80, 24)
        self._screen.responder = self._send
        self._stream = pyte.Stream(self._screen)
        self._default_char = self._screen.default_char

        self._backend: PtyBackend | None = None
        self._pending_spec: tuple[list[str], str, dict[str, str]] | None = None
        self._size_known = False

        self._view_top = 0
        self._follow = True
        self.verticalScrollBar().setSingleStep(1)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._sel_anchor: tuple[int, int] | None = None
        self._sel_active: tuple[int, int] | None = None
        self._selecting = False

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(12)
        self._repaint_timer.timeout.connect(self._refresh)

    # ------------------------------------------------------------------ fonty
    def _init_fonts(self, size: int) -> None:
        available = set(QFontDatabase.families())
        families = [f for f in _FONT_FAMILIES if f in available] or ["Monospace"]
        font = QFont()
        font.setFamilies(families)
        font.setPointSize(size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        self._fonts = {}
        for bold in (False, True):
            for italic in (False, True):
                f = QFont(font)
                f.setBold(bold)
                f.setItalic(italic)
                self._fonts[(bold, italic)] = f
        metrics = QFontMetricsF(font)
        self._cw = max(1.0, metrics.horizontalAdvance("M"))
        self._ch = max(1, ceil(metrics.height()))
        self._ascent = metrics.ascent()

    # ------------------------------------------------------------ cykl życia
    def start_shell(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        """Uruchamia powłokę (po ustaleniu rozmiaru widgetu)."""
        self._pending_spec = (argv, cwd, env)
        self._maybe_start()

    def _maybe_start(self) -> None:
        if self._pending_spec is None or not self._size_known:
            return
        argv, cwd, env = self._pending_spec
        self._pending_spec = None
        self._detach_backend(terminate=True)
        self._reset_screen()

        backend = create_backend(self)
        backend.data_received.connect(self._on_data)
        backend.exited.connect(self._on_backend_exited)
        self._backend = backend
        try:
            backend.start(argv, cwd, env, self._screen.lines, self._screen.columns)
        except Exception as exc:  # brak powłoki, brak pywinpty itp.
            log.exception("Nie udało się uruchomić powłoki")
            self._detach_backend(terminate=False)
            self._on_data(f"\r\n[Nie udało się uruchomić powłoki: {exc}]\r\n")
            self.shell_exited.emit(127)

    def _detach_backend(self, terminate: bool) -> None:
        backend, self._backend = self._backend, None
        if backend is None:
            return
        for sig in (backend.data_received, backend.exited):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        if terminate:
            backend.terminate()
        backend.deleteLater()

    def shutdown(self) -> None:
        self._pending_spec = None
        self._detach_backend(terminate=True)

    def _on_backend_exited(self, code: int) -> None:
        self._detach_backend(terminate=False)
        self.shell_exited.emit(code)

    def _reset_screen(self) -> None:
        self._screen.reset()
        self._screen.scrollback.clear()
        self._clear_selection()
        self._follow = True
        self._sync_scrollbar()
        self.viewport().update()

    # ----------------------------------------------------------------- dane
    def _on_data(self, text: str) -> None:
        try:
            self._stream.feed(text)
        except Exception:  # pyte nie może wywrócić aplikacji przez dziwną sekwencję
            log.exception("Błąd parsowania sekwencji terminala")
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _refresh(self) -> None:
        self._sync_scrollbar()
        self.viewport().update()

    def _send(self, text: str) -> None:
        if self._backend is not None:
            self._backend.write(text)

    # --------------------------------------------------------- API publiczne
    def send_text(self, text: str) -> None:
        """Surowe wysłanie do powłoki (jak wpisanie z klawiatury)."""
        self._scroll_to_bottom()
        self._send(text)

    def send_lines(self, text: str) -> None:
        """Wysyła tekst; każdy koniec linii działa jak Enter."""
        self.send_text(_normalize_newlines(text))

    def paste_text(self, text: str) -> None:
        """Wklejenie jak Ctrl+Shift+V (bracketed paste, jeśli powłoka je wspiera)."""
        if not text:
            return
        data = _normalize_newlines(text)
        if _MODE_BRACKETED_PASTE in self._screen.mode:
            data = f"\x1b[200~{data}\x1b[201~"
        self.send_text(data)

    def stage_commands(self, text: str) -> None:
        """Wkleja komendy BEZ uruchamiania ich.

        Gdy powłoka nie obsługuje bracketed paste, linie łączone są przez „; ”
        w jedną linię — inaczej pierwszy znak końca linii od razu by je wykonał.
        """
        payload = text.strip("\r\n")
        if not payload:
            return
        if _MODE_BRACKETED_PASTE in self._screen.mode:
            self.paste_text(payload)
        else:
            lines = [ln for ln in payload.splitlines() if ln.strip()]
            self.send_text("; ".join(lines))

    def copy_selection(self) -> bool:
        text = self.selected_text()
        if not text:
            return False
        QGuiApplication.clipboard().setText(text)
        return True

    def paste_clipboard(self) -> None:
        self.paste_text(QGuiApplication.clipboard().text())

    def clear_scrollback(self) -> None:
        self._screen.scrollback.clear()
        self._sync_scrollbar()
        self.viewport().update()

    # ----------------------------------------------------- rozmiar i scroll
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_size()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_size()

    def _apply_size(self) -> None:
        vp = self.viewport().size()
        if vp.width() < 40 or vp.height() < 40:
            return
        cols = max(2, int(vp.width() / self._cw))
        rows = max(2, int(vp.height() / self._ch))
        if (rows, cols) != (self._screen.lines, self._screen.columns):
            self._screen.resize(rows, cols)
            if self._backend is not None:
                self._backend.resize(rows, cols)
        first = not self._size_known
        self._size_known = True
        self._sync_scrollbar()
        if first:
            self._maybe_start()

    def _sync_scrollbar(self) -> None:
        bar = self.verticalScrollBar()
        n = len(self._screen.scrollback)
        bar.blockSignals(True)
        bar.setRange(0, n)
        bar.setPageStep(self._screen.lines)
        if self._follow:
            bar.setValue(n)
        self._view_top = min(bar.value(), n)
        bar.blockSignals(False)

    def _on_scroll(self, value: int) -> None:
        self._view_top = value
        self._follow = value >= self.verticalScrollBar().maximum()
        self.viewport().update()

    def _scroll_to_bottom(self) -> None:
        if not self._follow or self._view_top < len(self._screen.scrollback):
            self._follow = True
            self._sync_scrollbar()
            self.viewport().update()

    # -------------------------------------------------------------- rysowanie
    def _color(self, name: str, default: QColor) -> QColor:
        if name == "default":
            return default
        c = self._colors.get(name)
        if c is not None:
            return c
        c = self._color_cache.get(name)
        if c is None:
            c = QColor(f"#{name}") if len(name) == 6 else default
            if not c.isValid():
                c = default
            self._color_cache[name] = c
        return c

    def _line_for(self, abs_row: int):
        sb = self._screen.scrollback
        if abs_row < len(sb):
            return sb[abs_row]
        y = abs_row - len(sb)
        if 0 <= y < self._screen.lines:
            return self._screen.buffer[y]
        return None

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), self._bg)
        scr = self._screen
        cw, ch = self._cw, self._ch
        sel = self._normalized_selection()
        default = self._default_char
        current_font = None
        clip = event.rect()

        for r in range(scr.lines):
            y = r * ch
            if y > clip.bottom() or y + ch < clip.top():
                continue
            abs_row = self._view_top + r
            line = self._line_for(abs_row)
            if line is None:
                break
            for c in range(scr.columns):
                cell = line[c] if c in line else default
                selected = sel is not None and _in_selection(sel, abs_row, c)
                fg = self._color(cell.fg, self._fg)
                bg = self._color(cell.bg, self._bg)
                if cell.reverse:
                    fg, bg = bg if cell.bg != "default" else self._bg, fg
                if selected:
                    bg, fg = self._sel_bg, self._bg
                x = c * cw
                if selected or cell.bg != "default" or cell.reverse:
                    painter.fillRect(QRectF(x, y, cw + 0.5, ch), bg)
                data = cell.data
                if data and data != " ":
                    key = (bool(cell.bold), bool(cell.italics))
                    if key != current_font:
                        painter.setFont(self._fonts[key])
                        current_font = key
                    painter.setPen(fg)
                    painter.drawText(QPointF(x, y + self._ascent), data)
                if cell.underscore or cell.strikethrough:
                    painter.setPen(QPen(fg, 1))
                    if cell.underscore:
                        painter.drawLine(QPointF(x, y + ch - 1.5), QPointF(x + cw, y + ch - 1.5))
                    if cell.strikethrough:
                        painter.drawLine(QPointF(x, y + ch / 2), QPointF(x + cw, y + ch / 2))

        self._paint_cursor(painter)
        painter.end()

    def _paint_cursor(self, painter: QPainter) -> None:
        scr = self._screen
        cur = scr.cursor
        if cur.hidden or self._view_top < len(scr.scrollback):
            return
        if not (0 <= cur.y < scr.lines and 0 <= cur.x < scr.columns):
            return
        rect = QRectF(cur.x * self._cw, cur.y * self._ch, self._cw, self._ch)
        if self.hasFocus():
            painter.fillRect(rect, self._cursor_color)
            cell = scr.buffer[cur.y][cur.x]
            if cell.data and cell.data != " ":
                painter.setFont(self._fonts[(bool(cell.bold), bool(cell.italics))])
                painter.setPen(self._bg)
                painter.drawText(QPointF(rect.x(), rect.y() + self._ascent), cell.data)
        else:
            painter.setPen(QPen(self._cursor_color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.viewport().update()

    # --------------------------------------------------------------- zaznaczanie
    def _cell_at(self, pos) -> tuple[int, int]:
        col = min(max(int(pos.x() / self._cw), 0), self._screen.columns - 1)
        row = min(max(int(pos.y() / self._ch), 0), self._screen.lines - 1)
        return (self._view_top + row, col)

    def _normalized_selection(self):
        if self._sel_anchor is None or self._sel_active is None:
            return None
        if self._sel_anchor == self._sel_active:
            return None
        a, b = sorted((self._sel_anchor, self._sel_active))
        return (a, b)

    def _clear_selection(self) -> None:
        self._sel_anchor = self._sel_active = None
        self._selecting = False

    def selected_text(self) -> str:
        sel = self._normalized_selection()
        if sel is None:
            return ""
        (r1, c1), (r2, c2) = sel
        cols = self._screen.columns
        lines = []
        for a in range(r1, r2 + 1):
            line = self._line_for(a)
            if line is None:
                continue
            start = c1 if a == r1 else 0
            end = c2 if a == r2 else cols - 1
            text = "".join(
                (line[c].data if c in line else " ") for c in range(start, end + 1)
            )
            lines.append(text.rstrip())
        return "\n".join(lines).rstrip("\n")

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position())
            self._sel_anchor = self._sel_active = cell
            self._selecting = True
            self.viewport().update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            cb = QGuiApplication.clipboard()
            if cb.supportsSelection():
                self.paste_text(cb.text(cb.Mode.Selection))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._sel_active = self._cell_at(event.position())
            self.viewport().update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            text = self.selected_text()
            cb = QGuiApplication.clipboard()
            if text and cb.supportsSelection():  # X11/Wayland: zaznaczone = schowek PRIMARY
                cb.setText(text, cb.Mode.Selection)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        abs_row, col = self._cell_at(event.position())
        line = self._line_for(abs_row)
        if line is None:
            return
        cols = self._screen.columns

        def ch_at(c: int) -> str:
            return line[c].data if c in line else " "

        if not ch_at(col).strip():
            return
        lo = hi = col
        while lo > 0 and ch_at(lo - 1).strip():
            lo -= 1
        while hi < cols - 1 and ch_at(hi + 1).strip():
            hi += 1
        self._sel_anchor, self._sel_active = (abs_row, lo), (abs_row, hi + 1 if hi + 1 < cols else hi)
        self._selecting = False
        self.viewport().update()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        act_copy = menu.addAction("Kopiuj")
        act_copy.setEnabled(bool(self.selected_text()))
        act_paste = menu.addAction("Wklej")
        act_paste.setEnabled(bool(QGuiApplication.clipboard().text()))
        menu.addSeparator()
        act_clear = menu.addAction("Wyczyść historię")
        chosen = menu.exec(event.globalPos())
        if chosen is act_copy:
            self.copy_selection()
        elif chosen is act_paste:
            self.paste_clipboard()
        elif chosen is act_clear:
            self.clear_scrollback()

    # ------------------------------------------------------------- klawiatura
    def event(self, e) -> bool:
        t = e.type()
        if t == QEvent.Type.ShortcutOverride:
            e.accept()  # terminal dostaje wszystkie klawisze (Ctrl+C, Tab, Esc …)
            return True
        if t == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def inputMethodEvent(self, event) -> None:
        commit = event.commitString()
        if commit:
            self.send_text(commit)
        event.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)

        if self._handle_app_shortcut(key, ctrl, shift, alt):
            event.accept()
            return

        data = self._key_to_bytes(event, key, ctrl, shift, alt)
        if data:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.enter_pressed.emit()
            self.send_text(data)
            event.accept()
        else:
            event.ignore()

    def _handle_app_shortcut(self, key, ctrl: bool, shift: bool, alt: bool) -> bool:
        K = Qt.Key
        if ctrl and shift and key == K.Key_C:
            self.copy_selection()
            return True
        if ctrl and shift and key == K.Key_V:
            self.paste_clipboard()
            return True
        if ctrl and key == K.Key_Insert:
            self.copy_selection()
            return True
        if shift and not ctrl and key == K.Key_Insert:
            self.paste_clipboard()
            return True
        if IS_WINDOWS and ctrl and not shift and not alt:
            if key == K.Key_V:
                self.paste_clipboard()
                return True
            if key == K.Key_C and self._normalized_selection() is not None:
                self.copy_selection()
                self._clear_selection()
                self.viewport().update()
                return True
        if shift and not ctrl and key in (K.Key_PageUp, K.Key_PageDown):
            bar = self.verticalScrollBar()
            step = self._screen.lines - 1
            bar.setValue(bar.value() + (-step if key == K.Key_PageUp else step))
            return True
        if shift and not ctrl and key in (K.Key_Home, K.Key_End):
            bar = self.verticalScrollBar()
            bar.setValue(bar.minimum() if key == K.Key_Home else bar.maximum())
            return True
        return False

    def _key_to_bytes(self, event, key, ctrl: bool, shift: bool, alt: bool) -> str:
        K = Qt.Key
        mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)

        if key in _PLAIN:
            seq = _PLAIN[key]
            return "\x1b" + seq if alt and key != K.Key_Escape else seq
        if key in _CSI_FINAL:
            final = _CSI_FINAL[key]
            if mod > 1:
                return f"\x1b[1;{mod}{final}"
            app = _MODE_APP_CURSOR in self._screen.mode
            return f"\x1b{'O' if app else '['}{final}"
        if key in _TILDE:
            return f"\x1b[{_TILDE[key]};{mod}~" if mod > 1 else f"\x1b[{_TILDE[key]}~"
        if key in _SS3:
            return f"\x1b[1;{mod}{_SS3[key]}" if mod > 1 else f"\x1bO{_SS3[key]}"

        text = event.text()
        printable = bool(text) and text >= " " and text != "\x7f"

        # AltGr w Windows to Ctrl+Alt — wtedy `text` zawiera znak (np. „ą”) i wysyłamy go wprost.
        if printable and not (ctrl and not alt):
            return "\x1b" + text if alt and not ctrl else text

        if ctrl and not alt:
            if K.Key_A <= key <= K.Key_Z:
                return chr(key - 0x40)
            if key in _CTRL_SYMBOLS:
                return _CTRL_SYMBOLS[key]
        return ""


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\r").replace("\n", "\r")


def _in_selection(sel, row: int, col: int) -> bool:
    (r1, c1), (r2, c2) = sel
    if row < r1 or row > r2:
        return False
    if row == r1 and col < c1:
        return False
    if row == r2 and col >= c2:
        return False
    return True