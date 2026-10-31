"""Telegram без входу: імпорт експорту Telegram Desktop (Налаштування → Розширені → Експорт даних Telegram,
або в чаті ⋮ → Експорт історії чату). Формат — JSON або HTML (його Telegram пропонує за замовчуванням).

З експорту беремо: посилання на YouTube (стають відео) і на статті, довгі пости (стають текстами),
документи й книги, а також аудіо, відео й голосові (їх розпізнає Whisper), якщо файли є в експорті.
Список елемента — назва чату чи каналу. Програма лише читає експорт: у Telegram нічого не змінюється.
Урок прототипу: коди входу в Telegram інколи «тихо» не приходять — тому шлях без входу йде першим.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import readers, web, youtube

RESULT = "result.json"
MIN_POST = 800            # коротші пости — це підписи до посилань, а не окремі тексти для конспекту
TITLE_CHARS = 90
_SKIP_HOSTS = ("t.me", "telegram.me", "telegram.org", "telegra.ph")   # посилання на інші пости — не статті


class ExportError(Exception):
    """Не знайдено чи не вдалося прочитати експорт (result.json або messages.html)."""


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


def _html_chat_dirs(path: Path) -> list[Path]:
    """Теки чатів HTML-експорту: увесь обліковий запис (chats/chat_N/) або один чат (messages.html поруч)."""
    if not path.is_dir():
        return []
    if (path / "messages.html").is_file():
        return [path]
    if (path / "export_results.html").is_file() and (path / "chats").is_dir():
        return sorted((d for d in (path / "chats").iterdir() if (d / "messages.html").is_file()),
                      key=lambda d: _number(d.name))
    return []


def is_export(path: Path) -> bool:
    return find_result(path) is not None or bool(_html_chat_dirs(path))


def _load(path: Path) -> tuple[Path, list[dict[str, Any]]]:
    result = find_result(path)
    if result is None:
        dirs = _html_chat_dirs(path)
        if not dirs:
            raise ExportError(str(path))
        chats = [_html_chat(d, path) for d in dirs]
        saved = _html_saved_folder(path)
        for chat in chats:
            if chat["id"] == saved:
                chat["type"] = "saved_messages"
        return path, chats
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


# ---------------------------------------------------------------- HTML-експорт

def _number(name: str) -> int:
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


def _html_saved_folder(root: Path) -> str:
    """Тека «Збереженого»: на сторінці чату в заголовку ім'я власника, а в списку чатів — «Saved messages»."""
    try:
        index = (root / "lists" / "chats.html").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    m = re.search(r'href="[./]*chats/(chat_\d+)/messages\.html[^"]*"(?:(?!</a>).)*?'
                  r'class="name bold">\s*Saved messages\s*<', index, re.S)
    return m.group(1) if m else ""


def _html_chat(folder: Path, root: Path) -> dict[str, Any]:
    """Чат HTML-експорту в тому самому вигляді, що й у JSON: назва, id і повідомлення."""
    pages = sorted(folder.glob("messages*.html"), key=lambda p: _number(p.stem))   # messages, messages2, ...
    parser = _ChatPage(folder.relative_to(root).as_posix() if folder != root else "")
    for page in pages:
        try:
            parser.feed(page.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            raise ExportError(f"{page}: {str(e)[:200]}") from e
        parser.close()
        parser.reset()
    return {"name": parser.title or folder.name, "id": folder.name if folder != root else "chat",
            "type": "html", "messages": parser.messages}


_FILE_MEDIA = {"media_file": "file", "media_audio_file": "audio_file", "media_voice_message": "voice_message",
               "media_video": "video_file"}


class _ChatPage(HTMLParser):
    """Сторінка messages.html: div.message.default — повідомлення, div.text — його текст із посиланнями,
    a.media_file / media_voice_message ... — файл (лише якщо його експортовано), title дати — час."""

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix + "/" if prefix else ""
        self.title = ""
        self.messages: list[dict[str, Any]] = []
        self._depth = 0
        self._msg: dict[str, Any] | None = None
        self._msg_depth = self._text_depth = self._title_depth = -1
        self._link: dict[str, Any] | None = None
        self._title_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        classes = set(a.get("class", "").split())
        if tag == "br":
            self._data("\n")
            return
        if tag in ("img", "hr", "meta", "link", "input"):
            return
        self._depth += 1
        if tag == "div" and "page_header" in classes:
            self._title_depth = self._depth
        elif tag == "div" and {"message", "default"} <= classes:
            self._msg = {"id": a.get("id", "").removeprefix("message"), "type": "message", "text_entities": []}
            self._msg_depth = self._depth
        elif self._msg is None:
            return
        elif tag == "div" and {"date", "details"} <= classes and "date" not in self._msg:
            self._msg["date"] = _html_date(a.get("title", ""))
        elif tag == "div" and classes == {"text"}:
            self._text_depth = self._depth
        elif tag == "a" and self._text_depth >= 0 and re.match(r"^https?://", a.get("href", ""), re.I):
            self._link = {"type": "text_link", "text": "", "href": a["href"]}
        elif tag == "a" and classes & set(_FILE_MEDIA) and a.get("href"):
            self._msg["file"] = self.prefix + a["href"]
            self._msg["media_type"] = _FILE_MEDIA[next(iter(classes & set(_FILE_MEDIA)))]

    def handle_endtag(self, tag: str) -> None:
        if tag in ("br", "img", "hr", "meta", "link", "input"):
            return
        if tag == "a" and self._link is not None and self._msg is not None:
            self._msg["text_entities"].append(self._link)
            self._link = None
        if self._depth == self._text_depth:
            self._text_depth = -1
        if self._depth == self._title_depth:   # заголовок повторюється на кожній сторінці — беремо перший
            self._title_depth = -1
            self._title_done = bool(self.title)
        if self._depth == self._msg_depth and self._msg is not None:
            self.messages.append(self._msg)
            self._msg, self._msg_depth = None, -1
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth >= 0:
            if data.strip() and not self._title_done:
                self.title = (self.title + " " + data.strip()).strip()
        else:
            self._data(data)

    def _data(self, data: str) -> None:
        if self._msg is None or self._text_depth < 0:
            return
        if self._link is not None:
            self._link["text"] += data
        else:
            self._msg["text_entities"].append({"type": "plain", "text": data})


def _html_date(title: str) -> str:
    """«14.03.2023 17:38:19 GMT+01:00» -> «2023-03-14T17:38:19»."""
    try:
        return datetime.strptime(title[:19], "%d.%m.%Y %H:%M:%S").isoformat()
    except ValueError:
        return ""


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
    return collect(chats, root, chosen, saved_name, min_post)


def collect(chats: list[dict[str, Any]], root: Path | None = None, chosen: list[str] | None = None,
            saved_name: str = "Saved Messages", min_post: int = MIN_POST) -> Export:
    """Посилання, довгі пости й файли з чатів (з експорту або з публічних каналів, tgchannel.py)."""
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
            ref = _file(root, msg, name) if root else None
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
