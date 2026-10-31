"""YouTube без ключів і без Google Cloud: списки — через yt-dlp, субтитри — через youtube-transcript-api.

Програма лише читає: нічого не змінює в YouTube.
Урок прототипу: YouTube блокує субтитри з IP після кількох десятків запитів на добу, тому
між запитами — пауза (див. pipeline), а при блокуванні — зупинка, а не повтори.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
WATCH_URL = "https://www.youtube.com/watch?v={}"


class ListError(Exception):
    """Не вдалося отримати список (приватний плейлист, неправильне посилання, немає мережі)."""


class BlockedError(Exception):
    """YouTube обмежує запити з нашої IP — треба зупинитися й почекати."""


class AudioError(Exception):
    """Не вдалося завантажити звук відео (приватне, видалене, з віковим обмеженням)."""


@dataclass
class Entry:
    id: str
    title: str
    channel: str = ""
    duration: int | None = None


@dataclass
class Listing:
    title: str                       # назва плейлиста / каналу; "" для окремих відео
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Transcript:
    text: str                        # текст із мітками часу [мм:сс] приблизно щохвилини
    language: str
    generated: bool
    recognized: bool = False         # не субтитри, а розпізнане мовлення (Whisper)


def video_id(url: str) -> str | None:
    """ID відео з посилання (watch, youtu.be, shorts, live, embed) або сам ID; інакше None."""
    s = url.strip()
    if VIDEO_ID.match(s):
        return s
    u = urllib.parse.urlparse(s if "://" in s else "https://" + s)
    host = (u.hostname or "").removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
        return cand if VIDEO_ID.match(cand) else None
    if host.endswith("youtube.com"):
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/watch" and "list" not in q:
            cand = (q.get("v") or [""])[0]
            return cand if VIDEO_ID.match(cand) else None
        m = re.match(r"^/(shorts|live|embed)/([A-Za-z0-9_-]{11})", u.path)
        if m:
            return m.group(2)
    return None


def normalize_list_url(url: str) -> str:
    """Канал без вкладки -> вкладка «Відео»; watch?v=..&list=.. -> сам плейлист; WL -> «Дивитися пізніше»."""
    s = url.strip()
    if s.upper() in ("WL", "WATCH LATER"):
        return "https://www.youtube.com/playlist?list=WL"
    u = urllib.parse.urlparse(s if "://" in s else "https://" + s)
    q = urllib.parse.parse_qs(u.query)
    if "list" in q:
        return "https://www.youtube.com/playlist?list=" + q["list"][0]
    path = u.path.rstrip("/")
    if re.match(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)$", path):
        return f"https://www.youtube.com{path}/videos"
    return s


def list_url(url: str, cookies_file: str | None = None) -> Listing:
    """Список відео за посиланням: плейлист, канал або окреме відео."""
    vid = video_id(url)
    if vid:
        return Listing("", [_single_video(vid)])
    return _flat_playlist(normalize_list_url(url), cookies_file)


class _Quiet:
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def _flat_playlist(url: str, cookies_file: str | None) -> Listing:
    import yt_dlp

    opts: dict[str, Any] = {"extract_flat": "in_playlist", "skip_download": True, "quiet": True,
                            "no_warnings": True, "logger": _Quiet()}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # yt-dlp кидає власні типи помилок; для нас усі вони — «список недоступний»
        raise ListError(_first_line(str(e))) from e
    if not info:
        raise ListError(url)
    entries = []
    for entry in info.get("entries") or []:
        entry = entry or {}
        vid = entry.get("id")
        if not vid or not VIDEO_ID.match(vid) or entry.get("availability") in ("private", "needs_auth"):
            continue   # приватні / видалені відео в плейлисті
        title = entry.get("title") or vid
        if title in ("[Private video]", "[Deleted video]"):
            continue
        duration = entry.get("duration")
        entries.append(Entry(vid, title, entry.get("channel") or entry.get("uploader") or "",
                             int(duration) if duration else None))
    title = str(info.get("title") or "")
    title = re.sub(r"\s+-\s+Videos$", "", title)   # «Канал - Videos» -> «Канал»
    return Listing(title, entries)


def _single_video(vid: str) -> Entry:
    """Назва й канал окремого відео через oEmbed (легкий запит, без субтитрів і без ключів)."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(WATCH_URL.format(vid))
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return Entry(vid, data.get("title") or vid, data.get("author_name") or "")
    except (OSError, ValueError):
        return Entry(vid, vid)


def video_duration(vid: str) -> int | None:
    """Тривалість відео в секундах зі сторінки відео (окремі посилання її не мають; без субтитрів і ключів)."""
    req = urllib.request.Request(WATCH_URL.format(vid), headers={"User-Agent": "Mozilla/5.0",
                                                                  "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            page = resp.read().decode("utf-8", "replace")
    except OSError:
        return None
    m = re.search(r'"lengthSeconds":"(\d+)"', page)
    return int(m.group(1)) if m else None


_BLOCK_HINTS = ("429", "Too Many Requests", "not a bot", "Sign in to confirm")


def download_audio(vid: str, folder: Path, cookies_file: str | None = None) -> Path:
    """Лише звукова доріжка відео (без ffmpeg: як є, m4a чи webm) — для розпізнавання мовлення."""
    import yt_dlp

    folder.mkdir(parents=True, exist_ok=True)
    opts: dict[str, Any] = {"format": "bestaudio[ext=m4a]/bestaudio", "outtmpl": str(folder / f"{vid}.%(ext)s"),
                            "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
                            "logger": _Quiet()}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(WATCH_URL.format(vid), download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as e:  # yt-dlp кидає власні типи помилок
        msg = _first_line(str(e))
        if any(h in str(e) for h in _BLOCK_HINTS):
            raise BlockedError(msg) from e
        raise AudioError(msg) from e
    if not path.exists():
        raise AudioError(f"no audio file: {path.name}")
    return path


def _first_line(s: str) -> str:
    line = next((ln.strip() for ln in s.splitlines() if ln.strip()), s)
    return re.sub(r"^ERROR:\s*", "", line)[:200]


# ---------------------------------------------------------------- субтитри

def fetch_transcript(vid: str, languages: list[str]) -> Transcript | None:
    """Субтитри відео або None, якщо їх немає. При блокуванні YouTube кидає BlockedError.

    Порядок: ручні субтитри бажаними мовами -> автоматичні бажаними мовами -> будь-які (ручні першими).
    """
    from youtube_transcript_api import (
        AgeRestricted,
        IpBlocked,
        NoTranscriptFound,
        RequestBlocked,
        TranscriptsDisabled,
        VideoUnavailable,
        VideoUnplayable,
        YouTubeRequestFailed,
        YouTubeTranscriptApi,
    )

    try:
        tlist = YouTubeTranscriptApi().list(vid)
        chosen = _choose(tlist, languages, NoTranscriptFound)
        if chosen is None:
            return None
        fetched = chosen.fetch()
    except (IpBlocked, RequestBlocked) as e:
        raise BlockedError(f"{type(e).__name__}: {_first_line(str(e))}") from e
    except YouTubeRequestFailed as e:
        if "429" in str(e) or "Too Many Requests" in str(e):
            raise BlockedError("HTTP 429 Too Many Requests") from e
        raise
    except (TranscriptsDisabled, NoTranscriptFound, AgeRestricted, VideoUnplayable, VideoUnavailable):
        return None
    snippets = [(float(s.start), str(s.text)) for s in fetched]
    return Transcript(with_timestamps(snippets), chosen.language_code, bool(chosen.is_generated))


def _choose(tlist: Any, languages: list[str], not_found: type[Exception]) -> Any:
    for finder in (tlist.find_manually_created_transcript, tlist.find_generated_transcript):
        try:
            return finder(languages)
        except not_found:
            pass
    available = sorted(tlist, key=lambda tr: tr.is_generated)
    return available[0] if available else None


def with_timestamps(snippets: list[tuple[float, str]], every: float = 60.0) -> str:
    """Текст субтитрів з мітками [мм:сс] приблизно щохвилини — модель зможе вказати, де «вода»."""
    parts: list[str] = []
    next_mark = 0.0
    for start, text in snippets:
        if start >= next_mark:
            parts.append(f"\n[{format_duration(int(start))}] ")
            next_mark = start + every
        parts.append(text.replace("\n", " ").strip() + " ")
    return "".join(parts).strip()


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
