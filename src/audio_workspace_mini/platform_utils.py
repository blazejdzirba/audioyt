"""Warstwa platformowa: Linux ↔ Windows (powłoka, ścieżki, narzędzia zewnętrzne)."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

WORKSPACE_DIRNAME = "AudioWorkspace"

FLAVOR_POSIX = "posix"
FLAVOR_POWERSHELL = "powershell"


def default_flavor() -> str:
    """Dialekt powłoki, dla którego generujemy komendy."""
    return FLAVOR_POWERSHELL if IS_WINDOWS else FLAVOR_POSIX


# ---------------------------------------------------------------------------
# Ścieżki
# ---------------------------------------------------------------------------
def is_frozen() -> bool:
    """True, gdy działamy jako zbudowany .exe (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Katalog aplikacji (przy .exe: folder, w którym leży plik .exe)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def workspace_root() -> Path:
    return Path.home() / WORKSPACE_DIRNAME


def asset_path(name: str) -> Path:
    """Plik z `assets/` — działa zarówno ze źródeł, jak i z paczki PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "audio_workspace_mini" / "assets" / name


# ---------------------------------------------------------------------------
# Narzędzia zewnętrzne (yt-dlp, ffmpeg)
# ---------------------------------------------------------------------------
def tools_dir() -> Path:
    """Folder, do którego aplikacja sama instaluje yt-dlp/ffmpeg."""
    return workspace_root() / "bin"


def extra_tool_dirs() -> list[Path]:
    """Dodatkowe miejsca poszukiwań (na KOŃCU PATH — systemowe instalacje mają pierwszeństwo)."""
    dirs = [tools_dir()]
    if is_frozen():
        dirs += [app_dir() / "bin", app_dir()]
    return dirs


def tool_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Środowisko dla procesów potomnych (z rozszerzonym PATH)."""
    env = dict(base if base is not None else os.environ)
    extra = os.pathsep.join(str(d) for d in extra_tool_dirs())
    current = env.get("PATH", "")
    env["PATH"] = f"{current}{os.pathsep}{extra}" if current else extra
    if not IS_WINDOWS:
        env.setdefault("TERM", "xterm-256color")
    return env


def find_tool(name: str) -> str | None:
    return shutil.which(name, path=tool_env()["PATH"])


def subprocess_flags() -> dict:
    """Na Windows: nie otwieraj okna konsoli dla yt-dlp uruchamianego z GUI."""
    if IS_WINDOWS:
        import subprocess

        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# ---------------------------------------------------------------------------
# Powłoka dla wbudowanego terminala
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ShellSpec:
    argv: list[str]
    flavor: str
    label: str


def detect_shell() -> ShellSpec:
    if IS_WINDOWS:
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        # UTF-8 na wyjściu, żeby polskie znaki w tytułach nie były krzaczkami.
        init = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)"
        label = "PowerShell 7" if Path(exe).stem.lower() == "pwsh" else "Windows PowerShell"
        return ShellSpec([exe, "-NoLogo", "-NoExit", "-Command", init], FLAVOR_POWERSHELL, label)

    shell = os.environ.get("SHELL")
    if not shell or not os.path.exists(shell):
        try:
            import pwd

            shell = pwd.getpwuid(os.getuid()).pw_shell
        except (ImportError, KeyError):
            shell = None
    if not shell or not os.path.exists(shell):
        shell = shutil.which("bash") or "/bin/sh"
    return ShellSpec([shell, "-i"], FLAVOR_POSIX, Path(shell).name)