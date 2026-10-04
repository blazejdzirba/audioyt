"""Ciemny, nowoczesny motyw (paleta zbliżona do Catppuccin Mocha) — Qt/QSS.

Port oryginalnego motywu GTK3 na Qt6. Zachowuje paletę kolorów i układ
wizualny, ale używa QSS zamiast CSS oraz QPalette zamiast Gtk.Settings.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Paleta bazowa (identyczna jak w oryginalnym motywie GTK)
# ---------------------------------------------------------------------------
BG = "#181825"
SURFACE = "#1e1e2e"
FIELD = "#11111b"
BORDER = "#313244"
TEXT = "#cdd6f4"
DIM = "#6c7086"
SUBTLE = "#a6adc8"
BLUE = "#89b4fa"
GREEN = "#a6e3a1"
TEAL = "#94e2d5"
RED = "#f38ba8"
YELLOW = "#f9e2af"

# 16 kolorów ANSI (Catppuccin Mocha) — używane przez widget terminala
ANSI_PALETTE = [
    "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
    "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
    "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
    "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
]


def _rgba(hex_color: str, alpha: float) -> str:
    """`#rrggbb` + alpha → string `rgba(r, g, b, a)` dla QSS."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


# ---------------------------------------------------------------------------
# Arkusz stylów Qt (QSS) — tłumaczenie oryginalnego CSS z GTK
# ---------------------------------------------------------------------------
QSS = f"""
QMainWindow, QDialog, QWidget {{
    background-color: {BG};
    color: {TEXT};
}}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel[role="section"] {{ color: {SUBTLE}; font-weight: 700; font-size: 9pt; }}
QLabel[role="dim"] {{ color: {DIM}; font-size: 9pt; }}
QLabel[role="counter"] {{ color: {GREEN}; font-weight: 600; font-size: 9pt; }}
QLabel[role="status"] {{ color: {SUBTLE}; font-size: 9pt; }}
QLabel[role="path"] {{
    color: {TEXT}; font-family: monospace;
    padding: 8px 12px;
    background-color: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QPushButton {{
    color: {TEXT};
    background-color: {BORDER};
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{ background-color: #45475a; }}
QPushButton:pressed {{ background-color: #585b70; }}
QPushButton:disabled {{ color: {DIM}; }}

QPushButton[role="accent"] {{
    color: {FIELD}; font-weight: 600;
    background-color: {BLUE}; border-color: {BLUE};
}}
QPushButton[role="accent"]:hover {{ background-color: #a6c8ff; }}

QPushButton[role="success"] {{
    color: {FIELD}; font-weight: 700;
    background-color: {GREEN};
    border: none;
    padding: 10px 14px;
}}
QPushButton[role="success"]:hover {{ background-color: #b9f0b4; }}

QPushButton[role="flat-danger"] {{
    color: {RED}; background-color: transparent;
    border-color: transparent; padding: 2px 8px;
}}
QPushButton[role="flat-danger"]:hover {{ background-color: {_rgba(RED, 0.15)}; }}

QLineEdit {{
    background-color: {FIELD}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {BLUE};
    selection-color: {FIELD};
}}
QLineEdit:focus {{ border-color: {BLUE}; }}

QPlainTextEdit, QTextEdit {{
    background-color: {FIELD}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {BLUE};
    selection-color: {FIELD};
    padding: 4px;
}}

QComboBox {{
    background-color: {FIELD}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {BLUE}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {BORDER};
    selection-color: {TEXT};
    outline: none;
}}

QSplitter::handle {{ background-color: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 0;
}}
QScrollBar::handle {{
    background-color: #45475a; border-radius: 6px;
    min-height: 20px; min-width: 20px;
}}
QScrollBar::handle:hover {{ background-color: #585b70; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background-color: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {BORDER}; }}

QToolTip {{
    background-color: {FIELD}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 4px;
}}

QStatusBar {{
    background-color: {FIELD}; color: {SUBTLE};
    border-top: 1px solid {BORDER};
}}

QFrame#terminal-wrap {{
    background-color: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
"""


def install_theme(app: QApplication | None = None) -> None:
    """Ładuje ciemny motyw Qt (QPalette + QSS).

    Bezpieczne do wywołania zarówno z `run()`, jak i z `MainWindow.__init__`
    — jeśli `app` nie zostanie podane, bierze `QApplication.instance()`.
    """
    app = app or QApplication.instance()
    if app is None:
        return

    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(FIELD))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(BORDER))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(BLUE))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(FIELD))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(FIELD))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(DIM))
    app.setPalette(palette)

    app.setStyleSheet(QSS)