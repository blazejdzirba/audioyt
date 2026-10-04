"""Punkt wejścia aplikacji Qt (zastępuje Gtk.Application + do_activate)."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .theme import install_theme
from .window import MainWindow


def run(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Audio Workspace Mini")
    app.setApplicationDisplayName("Audio Workspace Mini")
    app.setOrganizationName("AudioWorkspaceMini")
    app.setDesktopFileName("audio-workspace-mini")

    install_theme(app)

    window = MainWindow()
    window.show()
    return app.exec()