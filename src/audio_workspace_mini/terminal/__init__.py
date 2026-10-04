"""Pakiet terminala: backend PTY + widget Qt (emulator VT na pyte)."""
from .backend import PtyBackend, create_backend
from .widget import TerminalWidget

__all__ = ["TerminalWidget", "PtyBackend", "create_backend"]