from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from .platform_utils import (
    FLAVOR_POWERSHELL,
    IS_WINDOWS,
    default_flavor,
    find_tool,
    subprocess_flags,
    tool_env,
    workspace_root,
)


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
def get_workspace_root() -> Path:
    return workspace_root()


def ensure_workspace() -> Path:
    """Tworzy strukturę ~/AudioWorkspace/links, commands, downloads."""
    root = get_workspace_root()
    for sub in ("links", "commands", "downloads"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Ekstrakcja linków z playlisty
# ---------------------------------------------------------------------------
def missing_ytdlp_message() -> str:
    if IS_WINDOWS:
        return (
            "Nie znaleziono yt-dlp. Kliknij „Zainstaluj yt-dlp i ffmpeg” "
            "albo zainstaluj ręcznie: winget install yt-dlp.yt-dlp"
        )
    return "Nie znaleziono yt-dlp w PATH (np. sudo apt install yt-dlp ffmpeg)."


def extract_playlist_links(
    playlist_url: str,
    output_file: Path,
    timeout: int = 900,
) -> tuple[int, str]:
    """
    Uruchamia yt-dlp --flat-playlist i zapisuje wynik do output_file.
    Zwraca (returncode, stderr). Kod 0 = sukces.
    """
    exe = find_tool("yt-dlp")
    if exe is None:
        return (127, missing_ytdlp_message())

    argv = [
        exe,
        "--flat-playlist",
        "--no-warnings",
        "--sleep-requests", "1",
        "--print", "- [%(title)s](https://www.youtube.com/watch?v=%(id)s)",        playlist_url,
    ]
    env = tool_env()
    env["PYTHONIOENCODING"] = "utf-8"  # polskie znaki także przez potok na Windows
    env["PYTHONUTF8"] = "1"
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            **subprocess_flags(),
        )
    except FileNotFoundError:
        return (127, missing_ytdlp_message())
    except subprocess.TimeoutExpired:
        return (124, f"Timeout po {timeout}s — playlista zbyt duża?")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(result.stdout, encoding="utf-8")

    if result.returncode != 0:
        return (result.returncode, (result.stderr or "").strip())
    return (0, "")


# Format linii:   - [Tytuł utworu](https://www.youtube.com/watch?v=XXXX)
_MD_LINK_RE = re.compile(r"^\s*-\s*\[.*?\]\((https?://\S+?)\)\s*$")


def parse_markdown_links(text: str) -> list[str]:
    """Wyciąga URL-e z linii Markdown, usuwa duplikaty, zachowuje kolejność."""
    urls: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _MD_LINK_RE.match(line)
        if not m:
            continue
        url = m.group(1)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Tempo (pauzy między pobraniami — ochrona przed blokadą IP)
# ---------------------------------------------------------------------------
DEFAULT_PACE = "Bezpieczne (5-15 s)"

# Każdy preset definiuje:
#   sleep_interval, max_sleep_interval  -> wewnętrzne pauzy yt-dlp między żądaniami
#   extra_sleep                         -> pauza `sleep N` PO każdym pobraniu
PACE_PRESETS: dict[str, dict[str, int]] = {
    "Szybkie (1-3 s)":         {"sleep_interval": 1,  "max_sleep_interval": 3,  "extra_sleep": 3},
    "Bezpieczne (5-15 s)":     {"sleep_interval": 5,  "max_sleep_interval": 15, "extra_sleep": 10},
    "Wolne (15-30 s)":         {"sleep_interval": 15, "max_sleep_interval": 30, "extra_sleep": 20},
    "Bardzo wolne (30-60 s)":  {"sleep_interval": 30, "max_sleep_interval": 60, "extra_sleep": 30},
}


def _pace_params(pace: str) -> dict[str, int]:
    return PACE_PRESETS.get(pace) or PACE_PRESETS[DEFAULT_PACE]


def estimate_seconds(n_urls: int, pace: str) -> int:
    """Przybliżony czas samych pauz między pobraniami (w sekundach)."""
    p = _pace_params(pace)
    avg = (p["sleep_interval"] + p["max_sleep_interval"]) / 2
    return int(n_urls * (avg + p["extra_sleep"]))


# ---------------------------------------------------------------------------
# Quoting zależne od powłoki
# ---------------------------------------------------------------------------
_PS_SAFE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"  # PowerShell traktuje je wszystkie jako apostrof


def quote(arg: str, flavor: str | None = None) -> str:
    """Bezpiecznie cytuje argument dla bash/zsh (posix) albo PowerShella."""
    flavor = flavor or default_flavor()
    if flavor == FLAVOR_POWERSHELL:
        if _PS_SAFE_RE.match(arg):
            return arg
        escaped = "".join(ch * 2 if ch in _PS_QUOTES else ch for ch in arg)
        return f"'{escaped}'"
    return shlex.quote(arg)


# ---------------------------------------------------------------------------
# Generator komend
# ---------------------------------------------------------------------------
def build_cd_command(path: Path, flavor: str | None = None) -> str:
    """Bezpieczna komenda zmiany katalogu do wysłania w terminal."""
    flavor = flavor or default_flavor()
    if flavor == FLAVOR_POWERSHELL:
        # -LiteralPath: nawiasy [] w nazwie folderu nie są traktowane jako wildcard
        return f"Set-Location -LiteralPath {quote(str(path), flavor)}"
    return f"cd {quote(str(path), flavor)}"


def _sleep_command(seconds: int, flavor: str) -> str:
    return f"Start-Sleep -Seconds {seconds}" if flavor == FLAVOR_POWERSHELL else f"sleep {seconds}"


def build_ytdlp_command(
    url: str,
    fmt: str = "251",
    audio_format: str = "mp3",
    audio_quality: str = "320K",
    pace: str = DEFAULT_PACE,
    flavor: str | None = None,
) -> str:
    """
    Pojedyncza komenda yt-dlp + pauza po niej.
    Wszystkie argumenty są cytowane odpowiednio do powłoki (bash/zsh lub PowerShell).
    """
    flavor = flavor or default_flavor()
    q = lambda s: quote(s, flavor)  # noqa: E731
    p = _pace_params(pace)
    return (
        f"yt-dlp -f {q(fmt)} "
        f"-x --audio-format {q(audio_format)} "
        f"--audio-quality {q(audio_quality)} "
        f"--no-playlist "
        f"--sleep-interval {p['sleep_interval']} "
        f"--max-sleep-interval {p['max_sleep_interval']} "
        f"--sleep-requests 2 "
        f"--limit-rate 3M "
        f"--retries 10 "
        f"--retry-sleep {q('linear=2::3')} "
        f"--throttled-rate 100K "
        f"--download-archive .archive.txt "
        f"-o {q('%(title)s.%(ext)s')} "
        f"{q(url)}"
        f"; {_sleep_command(p['extra_sleep'], flavor)}"
    )


def build_all_commands(urls: list[str], **kwargs) -> str:
    """Cały tekst: jedna komenda na linię (z trailing newline na końcu)."""
    lines = [build_ytdlp_command(u, **kwargs) for u in urls]
    return "\n".join(lines) + ("\n" if lines else "")