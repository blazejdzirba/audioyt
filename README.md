# Audio Workspace Mini

Terminal + generator komend yt-dlp z playlisty YouTube.
Cross-platform: **PySide6** (Qt 6) + emulator VT na **pyte** + PTY backend
(POSIX `pty` / Windows **ConPTY** przez `pywinpty`).

## Wymagania

- Python 3.11+
- Linux/macOS: `yt-dlp`, `ffmpeg`
- Windows: `yt-dlp`, `ffmpeg` (np. `winget install yt-dlp.yt-dlp Gyan.FFmpeg`)

Zależności Python (instalowane automatycznie): `PySide6-Essentials`, `pyte`,
`pywinpty` (tylko Windows).

## Uruchomienie (dev)

    PYTHONPATH=src python3 -m audio_workspace_mini

## Instalacja (opcjonalnie)

    pip install --user -e .
    audio-workspace-mini

## Workspace

Aplikacja tworzy `~/AudioWorkspace/`:
- `links/playlist_links.txt` — surowy output yt-dlp (Markdown)
- `commands/download_audio.txt` — wygenerowane komendy

## Użycie

1. Wklej URL playlisty.
2. Klik „Wyodrębnij linki".
3. Klik „Generuj komendy yt-dlp".
4. Skopiuj / zapisz / wklej do terminala (przycisk „Wklej do terminala"
   używa bracketed paste, gdy powłoka go wspiera).