"""Główne okno aplikacji (Qt) — odpowiednik `MainWindow` z wersji GTK."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .core import (
    DEFAULT_PACE,
    PACE_PRESETS,
    build_all_commands,
    build_cd_command,
    ensure_workspace,
    estimate_seconds,
    extract_playlist_links,
    parse_markdown_links,
)
from .platform_utils import detect_shell, tool_env
from .terminal.widget import TerminalWidget


# ---------------------------------------------------------------------------
# Mostek: wątek roboczy → wątek GUI (sygnały Qt są bezpieczne wątkowo)
# ---------------------------------------------------------------------------
class _ExtractBridge(QObject):
    finished = Signal(int, str)  # (returncode, stderr)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Audio Workspace Mini")
        self.resize(1400, 820)

        self.workspace = ensure_workspace()
        self.playlist_file = self.workspace / "links" / "playlist_links.txt"
        self.commands_file = self.workspace / "commands" / "download_audio.txt"
        self.download_dir = self.workspace / "downloads"

        self._shell = detect_shell()
        self._pending_paste = False

        self._extract_bridge = _ExtractBridge(self)
        self._extract_bridge.finished.connect(self._on_extract_done)

        self._build_ui()
        self._spawn_shell()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizes([720, 680])
        splitter.setChildrenCollapsible(False)

        # ============== LEWA: terminal ==============
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 6, 6, 6)
        left_layout.setSpacing(4)

        self.lbl_term_header = QLabel(f"Terminal — {self.download_dir}")
        self.lbl_term_header.setProperty("role", "path")
        left_layout.addWidget(self.lbl_term_header)

        self.terminal = TerminalWidget()
        self.terminal.shell_exited.connect(self._on_shell_exited)
        self.terminal.enter_pressed.connect(self._on_enter_pressed)

        term_wrap = QFrame()
        term_wrap.setObjectName("terminal-wrap")
        wrap_layout = QVBoxLayout(term_wrap)
        wrap_layout.setContentsMargins(2, 2, 2, 2)
        wrap_layout.addWidget(self.terminal)
        left_layout.addWidget(term_wrap, 1)

        splitter.addWidget(left)

        # ============== PRAWA: generator ==============
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 8, 8, 8)
        rl.setSpacing(8)

        # 1) URL playlisty
        rl.addWidget(QLabel("1. URL playlisty:"))

        url_box = QHBoxLayout()
        url_box.setSpacing(6)
        self.entry_url = QLineEdit()
        self.entry_url.setPlaceholderText("https://www.youtube.com/playlist?list=...")
        self.entry_url.returnPressed.connect(self._on_extract)
        url_box.addWidget(self.entry_url, 1)

        self.btn_extract = QPushButton("Wyodrębnij linki")
        self.btn_extract.clicked.connect(lambda: self._on_extract())
        url_box.addWidget(self.btn_extract)
        rl.addLayout(url_box)

        self.lbl_status = QLabel("Gotowe.")
        self.lbl_status.setProperty("role", "status")
        self.lbl_status.setWordWrap(True)
        rl.addWidget(self.lbl_status)

        # 2) linki
        rl.addWidget(QLabel("2. Linki (edytowalne, jeden na linię):"))

        self.tv_links = QPlainTextEdit()
        self.tv_links.setMinimumHeight(180)
        self._apply_mono(self.tv_links)
        rl.addWidget(self.tv_links, 1)

        # 3) folder zapisu
        rl.addWidget(QLabel("3. Folder zapisu (wysyła `cd` do terminala):"))

        dir_box = QHBoxLayout()
        dir_box.setSpacing(6)
        self.lbl_folder = QLabel(str(self.download_dir))
        self.lbl_folder.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_folder.setWordWrap(False)
        dir_box.addWidget(self.lbl_folder, 1)

        btn_dir = QPushButton("Wybierz folder…")
        btn_dir.clicked.connect(self._on_choose_folder)
        dir_box.addWidget(btn_dir)
        rl.addLayout(dir_box)

        # 4) generuj + tempo
        gen_box = QHBoxLayout()
        gen_box.setSpacing(6)
        self.btn_generate = QPushButton("4. Generuj komendy yt-dlp")
        self.btn_generate.clicked.connect(lambda: self._on_generate())
        gen_box.addWidget(self.btn_generate, 1)

        gen_box.addWidget(QLabel("Tempo:"))
        self.combo_pace = QComboBox()
        for name in PACE_PRESETS:
            self.combo_pace.addItem(name)
        self.combo_pace.setCurrentText(DEFAULT_PACE)
        gen_box.addWidget(self.combo_pace)
        rl.addLayout(gen_box)

        rl.addWidget(QLabel("Komendy (readonly):"))

        self.tv_commands = QPlainTextEdit()
        self.tv_commands.setReadOnly(True)
        self.tv_commands.setMinimumHeight(200)
        self._apply_mono(self.tv_commands)
        rl.addWidget(self.tv_commands, 1)

        # akcje
        actions = QHBoxLayout()
        actions.setSpacing(6)

        btn_copy = QPushButton("Kopiuj")
        btn_copy.clicked.connect(lambda: self._on_copy())
        actions.addWidget(btn_copy)

        btn_save = QPushButton("Zapisz do commands.txt")
        btn_save.clicked.connect(lambda: self._on_save())
        actions.addWidget(btn_save)

        btn_paste = QPushButton("Wklej do terminala (bez Enter)")
        btn_paste.clicked.connect(lambda: self._on_paste_to_terminal())
        actions.addWidget(btn_paste)

        btn_run = QPushButton("Uruchom w terminalu")
        btn_run.setProperty("role", "accent")
        btn_run.clicked.connect(lambda: self._on_run_in_terminal())
        actions.addWidget(btn_run)

        rl.addLayout(actions)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

    @staticmethod
    def _apply_mono(widget: QPlainTextEdit) -> None:
        f = QFont(widget.font())
        f.setFamilies(["JetBrains Mono", "Fira Code", "Cascadia Mono",
                       "DejaVu Sans Mono", "Consolas", "Monospace"])
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setFixedPitch(True)
        f.setPointSize(10)
        widget.setFont(f)

    # ------------------------------------------------------- powłoka
    def _spawn_shell(self) -> None:
        self.terminal.start_shell(
            list(self._shell.argv),
            str(self.download_dir),
            tool_env(),
        )

    def _on_shell_exited(self, code: int) -> None:
        if code == 127:
            self._set_status("Nie udało się uruchomić powłoki.")
            return
        self._set_status(f"Powłoka zakończona (kod {code}) — uruchamiam nową.")
        self._spawn_shell()

    def _on_enter_pressed(self) -> None:
        # Zatwierdzenie Enterem czyści flagę „pending paste”.
        self._pending_paste = False

    # ------------------------------------------------------- folder
    def _on_choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Wybierz folder zapisu",
            str(self.download_dir),
        )
        if chosen:
            self._set_download_dir(Path(chosen))

    def _set_download_dir(self, path: Path) -> None:
        """Zapamiętuje folder i wysyła `cd`/`Set-Location` do terminala."""
        self.download_dir = path
        self.lbl_folder.setText(str(path))
        self.lbl_term_header.setText(f"Terminal — {path}")
        if self._pending_paste:
            self.terminal.send_text("\x03")
            self._pending_paste = False
        cmd = build_cd_command(path, flavor=self._shell.flavor)
        self.terminal.send_text(cmd + "\r")
        self.terminal.setFocus()
        self._set_status(f"Folder zapisu: {path} (terminal przeszedł tam komendą cd).")

    # ------------------------------------------------------- helpers
    def _set_status(self, text: str) -> None:
        self.lbl_status.setText(text)

    def _links_from_view(self) -> list[str]:
        text = self.tv_links.toPlainText()
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _commands_from_view(self) -> str:
        return self.tv_commands.toPlainText()

    # ------------------------------------------------------- akcje
    def _on_extract(self) -> None:
        url = self.entry_url.text().strip()
        if not url:
            self._set_status("Błąd: podaj URL playlisty.")
            return

        self.btn_extract.setEnabled(False)
        self._set_status("Pobieram listę linków z playlisty (może potrwać)...")

        bridge = self._extract_bridge
        playlist_file = self.playlist_file

        def worker() -> None:
            try:
                rc, err = extract_playlist_links(url, playlist_file)
            except Exception as exc:  # przycisk nie może zostać zablokowany
                rc, err = 1, str(exc)
            bridge.finished.emit(rc, err)

        threading.Thread(target=worker, daemon=True).start()

    def _on_extract_done(self, rc: int, err: str) -> None:
        self.btn_extract.setEnabled(True)

        try:
            text = self.playlist_file.read_text(encoding="utf-8")
        except OSError:
            text = ""
        urls = parse_markdown_links(text)

        if rc != 0 and not urls:
            self._set_status(f"Błąd yt-dlp (kod {rc}): {err[:300]}")
            return

        self.tv_links.setPlainText("\n".join(urls))

        msg = f"Znaleziono {len(urls)} linków. Zapisano w: {self.playlist_file}"
        if rc != 0:
            msg += " (uwaga: część pozycji pominięta — niedostępne/prywatne filmy)"
        self._set_status(msg)

    def _on_generate(self) -> None:
        urls = self._links_from_view()
        if not urls:
            self._set_status("Brak linków do przetworzenia.")
            return

        pace = self.combo_pace.currentText() or DEFAULT_PACE
        commands = build_all_commands(urls, pace=pace, flavor=self._shell.flavor)
        self.tv_commands.setPlainText(commands)
        mins = max(1, round(estimate_seconds(len(urls), pace) / 60))
        self._set_status(
            f"Wygenerowano {len(urls)} komend (tempo: {pace}). "
            f"Same pauzy zajmą ok. {mins} min + czas pobierania. "
            f"Pliki: tytuł.mp3 w folderze terminala ({self.download_dir})."
        )

    def _on_copy(self) -> None:
        text = self._commands_from_view()
        if not text.strip():
            self._set_status("Nie ma nic do skopiowania.")
            return
        QGuiApplication.clipboard().setText(text)
        self._set_status("Skopiowano do schowka.")

    def _on_save(self) -> None:
        text = self._commands_from_view()
        if not text.strip():
            self._set_status("Nie ma nic do zapisania.")
            return
        self.commands_file.parent.mkdir(parents=True, exist_ok=True)
        self.commands_file.write_text(text, encoding="utf-8")
        self._set_status(f"Zapisano w: {self.commands_file}")

    def _on_paste_to_terminal(self) -> None:
        text = self._commands_from_view()
        if not text.strip():
            self._set_status("Nie ma nic do wklejenia.")
            return
        # `stage_commands`: bracketed paste jeśli powłoka wspiera, inaczej
        # łączy linie `; ` i wysyła bez końcowego Enter — nic się nie wykona,
        # dopóki user nie naciśnie Enter.
        self.terminal.stage_commands(text)
        self._pending_paste = True
        self.terminal.setFocus()
        self._set_status(
            "Wklejono do terminala. Przejrzyj i naciśnij Enter, aby wykonać."
        )

    def _on_run_in_terminal(self) -> None:
        text = self._commands_from_view()
        if not text.strip():
            self._set_status("Nie ma nic do uruchomienia.")
            return
        if self._pending_paste:
            # Wyczyść poprzednio wklejony, niezatwierdzony tekst (Ctrl+C).
            self.terminal.send_text("\x03")
            self._pending_paste = False
        self.terminal.send_lines(text)
        self.terminal.setFocus()
        n = len([ln for ln in text.splitlines() if ln.strip()])
        self._set_status(f"Uruchomiono {n} komend w terminalu.")

    # ------------------------------------------------------- cykl życia
    def closeEvent(self, event) -> None:
        self.terminal.shutdown()
        super().closeEvent(event)