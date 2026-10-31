"""Telegram без входу: імпорт експорту Telegram Desktop (Налаштування → Розширені → Експорт даних Telegram,
або в чаті ⋮ → Експорт історії чату; формат — JSON).

З експорту беремо: посилання на YouTube (стають відео) і на статті, довгі пости (стають текстами),
документи й книги, а також аудіо, відео й голосові (їх розпізнає Whisper), якщо файли є в експорті.
Список елемента — назва чату чи каналу. Програма лише читає експорт: у Telegram нічого не змінюється.
Урок прототипу: коди входу в Telegram інколи «тихо» не приходять — тому шлях без входу йде першим.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import readers, web, youtube

RESULT = "result.json"
MIN_POST = 800            # коротші пости — це підписи до посилань, а не окремі тексти для конспекту
TITLE_CHARS = 90
_SKIP_HOSTS = ("t.me", "telegram.me", "telegram.org", "telegra.ph")   # посилання на інші пости — не статті


class ExportError(Exception):
    """Не знайдено чи не вдалося прочитати result.json."""


@dataclass
class Post:
    key: str
    list_name: str
    title: str
    text: str
    date: str


@dataclass
class FileRef:
    path: Path
    list_name: str
    title: str


@dataclass
class Chat:
    id: str
    name: str
    messages: int


@dataclass
class Export:
    links: list[web.Link] = field(default_factory=list)
    posts: list[Post] = field(default_factory=list)
    files: list[FileRef] = field(default_factory=list)
    chats: list[Chat] = field(default_factory=list)


def find_result(path: Path) -> Path | None:
    """result.json: сам файл або тека експорту, що його містить."""
    if path.is_file() and path.name.lower() == RESULT:
        return path
    candidate = path / RESULT
    return candidate if candidate.is_file() else None


def is_export(path: Path) -> bool:
    return find_result(path) is not None


def _load(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    result = find_result(path)
    if result is None:
        raise ExportError(str(path))
    try:
        data = json.loads(result.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ExportError(f"{result}: {str(e)[:200]}") from e
    if isinstance(data.get("chats"), dict):            # експорт усього облікового запису
        chats = list(data["chats"].get("list") or [])
        chats += list((data.get("left_chats") or {}).get("list") or [])
    elif isinstance(data.get("messages"), list):       # експорт одного чату
        chats = [data]
    else:
        raise ExportError(f"{result}: no chats")
    return result.parent, [c for c in chats if isinstance(c, dict)]


def chat_name(chat: dict[str, Any], saved_name: str) -> str:
    if chat.get("type") == "saved_messages":
        return saved_name
    return str(chat.get("name") or chat.get("id") or "Telegram").strip()


def list_chats(path: Path, saved_name: str = "Saved Messages") -> list[Chat]:
    _, chats = _load(path)
    return [Chat(str(c.get("id", "")), chat_name(c, saved_name), len(c.get("messages") or [])) for c in chats]


def read_export(path: Path, chosen: list[str] | None = None, saved_name: str = "Saved Messages",
                min_post: int = MIN_POST) -> Export:
    """Усе корисне з експорту. chosen — назви чатів (порожньо — усі)."""
    root, chats = _load(path)
    out = Export()
    seen_links: set[str] = set()
    for chat in chats:
        name = chat_name(chat, saved_name)
        messages = [m for m in chat.get("messages") or [] if isinstance(m, dict)]
        out.chats.append(Chat(str(chat.get("id", "")), name, len(messages)))
        if chosen and name not in chosen:
            continue
        for msg in messages:
            if msg.get("type", "message") != "message":
                continue   # службові: «приєднався», «закріпив»...
            text, links = message_text(msg)
            for url in links:
                key, _kind = web.item_key(url)
                if key not in seen_links:
                    seen_links.add(key)
                    out.links.append(web.Link(url, "", name))
            if len(text) >= min_post:
                out.posts.append(Post(f"telegram:{chat.get('id', name)}:{msg.get('id')}", name, title_of(text),
                                      text, str(msg.get("date") or "")[:10]))
            ref = _file(root, msg, name)
            if ref:
                out.files.append(ref)
    return out


def message_text(msg: dict[str, Any]) -> tuple[str, list[str]]:
    """Текст повідомлення і посилання з нього (і явні, і сховані під словами)."""
    parts = msg.get("text_entities")
    if not isinstance(parts, list):
        raw = msg.get("text")
        parts = raw if isinstance(raw, list) else [raw or ""]
    text: list[str] = []
    links: list[str] = []
    for part in parts:
        if isinstance(part, str):
            text.append(part)
            continue
        if not isinstance(part, dict):
            continue
        value = str(part.get("text") or "")
        text.append(value)
        kind = part.get("type")
        url = part.get("href") if kind == "text_link" else value if kind == "link" else None
        if url:
            if not re.match(r"^https?://", url, re.I):
                url = "https://" + url   # «example.com/page» без протоколу
            if _useful_link(url):
                links.append(url)
    return "".join(text).strip(), links


def _useful_link(url: str) -> bool:
    if not web.is_url(url):
        return False
    host = (url.split("/")[2] if url.count("/") >= 2 else "").lower().removeprefix("www.")
    if host in _SKIP_HOSTS:
        return False
    if web.is_youtube(url):
        return youtube.video_id(url) is not None   # канал чи плейлист у пості — не стаття
    return True


def title_of(text: str) -> str:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if len(first) <= TITLE_CHARS:
        return first
    cut = first[:TITLE_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:—-") + "…"


def _file(root: Path, msg: dict[str, Any], list_name: str) -> FileRef | None:
    rel = msg.get("file")
    if not isinstance(rel, str) or not rel or rel.startswith("("):   # «(File not included...)»
        return None
    path = root / rel
    if not path.is_file() or not readers.is_supported(path):
        return None
    if msg.get("media_type") == "voice_message":   # у голосових назви немає — чат і час
        return FileRef(path, list_name, f"{list_name} {str(msg.get('date') or '')[:16].replace('T', ' ')}")
    name = str(msg.get("file_name") or "")
    return FileRef(path, list_name, readers.title_of(Path(name)) if name else readers.title_of(path))
