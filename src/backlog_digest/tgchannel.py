"""Публічні канали Telegram без входу й без експорту: сторінка t.me/s/<канал>.

Таку сторінку Telegram показує будь-кому в браузері: ~20 останніх публікацій, а посилання «раніше»
(?before=<номер>) — попередні. Тож новий канал читається частинами, від нових до старих, не більше
PAGES сторінок за одне оновлення й з паузою між ними; стан (найновіший і найстаріший прочитаний номер)
зберігається в джерелі, і наступне оновлення бере спершу нове, а потім продовжує старіше.
Приватні канали так не прочитати — для них експорт Telegram Desktop (telegram.py).
Повідомлення віддаються в тому самому вигляді, що й з експорту, тож далі все спільне з telegram.py.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from . import web

BASE = "https://t.me/s/"
PAGES = 25            # сторінок (~20 публікацій кожна) за одне оновлення каналу
PAUSE = 1.5           # секунд між сторінками
TIMEOUT = 30
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_RESERVED = {"joinchat", "addstickers", "addemoji", "share", "proxy", "socks", "iv", "login", "setlanguage"}

Get = Callable[[str], tuple[str, str]]   # url -> (html, кінцева адреса після переадресацій)


class ChannelError(Exception):
    """Канал не публічний, не існує або сторінка недоступна."""


def channel_name(s: str) -> str | None:
    """«@name», «t.me/name», «https://t.me/s/name», «t.me/name/123» -> «name».
    Інше (запрошення «+...», не t.me) -> None."""
    s = s.strip()
    if s.startswith("@"):
        name = s[1:]
    else:
        u = urllib.parse.urlparse(s if "://" in s else "https://" + s)
        if u.netloc.lower().removeprefix("www.") not in ("t.me", "telegram.me"):
            return None
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] == "s":
            parts = parts[1:]
        if not parts:
            return None
        name = parts[0]
    return name if _NAME.match(name) and name.lower() not in _RESERVED else None


def is_channel_link(s: str) -> bool:
    """Лише посилання t.me (без «@»: «@name» у командному рядку — це канал YouTube)."""
    return not s.strip().startswith("@") and channel_name(s) is not None


@dataclass
class Page:
    title: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    older: int | None = None    # номер для ?before= — є старіші публікації


_VOID = ("br", "img", "meta", "link", "input", "hr", "source", "wbr")


class _ChannelPage(HTMLParser):
    """div.tgme_widget_message[data-post] — публікація; div.tgme_widget_message_text — текст (крім цитати
    відповіді a.tgme_widget_message_reply); a.tgme_widget_message_link_preview — посилання з прев'ю;
    time[datetime] — дата; <link rel="prev"> — старіші; og:title — назва каналу."""

    def __init__(self) -> None:
        super().__init__()
        self.page = Page()
        self._depth = 0
        self._msg: dict[str, Any] | None = None
        self._msg_depth = self._text_depth = self._skip_depth = -1
        self._link: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        classes = set(a.get("class", "").split())
        if tag == "meta" and a.get("property") == "og:title":
            self.page.title = a.get("content", "").strip()
        elif tag == "link" and a.get("rel") == "prev":
            m = re.search(r"before=(\d+)", a.get("href", ""))
            self.page.older = int(m.group(1)) if m else None
        elif tag == "br":
            self._data("\n")
        if tag in _VOID:
            return
        self._depth += 1
        if tag == "div" and "tgme_widget_message" in classes and a.get("data-post"):
            post_id = a["data-post"].rsplit("/", 1)[-1]
            if post_id.isdigit():
                self._msg = {"id": int(post_id), "type": "message", "text_entities": []}
                self._msg_depth = self._depth
            return
        if self._msg is None:
            return
        if tag == "a" and "tgme_widget_message_reply" in classes and self._skip_depth < 0:
            self._skip_depth = self._depth
        elif self._skip_depth >= 0:
            return
        elif tag == "div" and "tgme_widget_message_text" in classes and self._text_depth < 0:
            self._text_depth = self._depth
        elif tag == "a" and "tgme_widget_message_link_preview" in classes and a.get("href"):
            self._msg["text_entities"].append({"type": "text_link", "text": "", "href": a["href"]})
        elif tag == "a" and self._text_depth >= 0 and re.match(r"^https?://", a.get("href", ""), re.I):
            self._link = {"type": "text_link", "text": "", "href": a["href"]}
        elif tag == "time" and a.get("datetime") and "date" not in self._msg:
            self._msg["date"] = a["datetime"][:19]

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if tag == "a" and self._link is not None and self._msg is not None:
            self._msg["text_entities"].append(self._link)
            self._link = None
        if self._depth == self._skip_depth:
            self._skip_depth = -1
        if self._depth == self._text_depth:
            self._text_depth = -1
        if self._depth == self._msg_depth and self._msg is not None:
            self.page.messages.append(self._msg)
            self._msg, self._msg_depth = None, -1
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        self._data(data)

    def _data(self, data: str) -> None:
        if self._msg is None or self._text_depth < 0 or self._skip_depth >= 0:
            return
        if self._link is not None:
            self._link["text"] += data
        else:
            self._msg["text_entities"].append({"type": "plain", "text": data})


def parse_page(html: str) -> Page:
    parser = _ChannelPage()
    parser.feed(html)
    parser.close()
    return parser.page


def _get(url: str) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": web.USER_AGENT, "Accept-Language": "*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(5 << 20).decode("utf-8", errors="replace"), resp.geturl()
    except urllib.error.HTTPError as e:
        raise ChannelError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ChannelError(str(getattr(e, "reason", e))[:200]) from e


def fetch_page(name: str, before: int | None = None, get: Get | None = None) -> Page:
    html, final = (get or _get)(BASE + name + (f"?before={before}" if before else ""))
    if "/s/" not in final:   # Telegram переадресовує на t.me/<name>, коли перегляду без входу немає
        raise ChannelError("not public")
    page = parse_page(html)
    if not page.messages and before is None and "tgme_channel_info" not in html:
        raise ChannelError("not public")
    return page


def fetch(name: str, state: dict[str, Any], *, pages: int = PAGES, get: Get | None = None,
          sleep: Callable[[float], None] | None = None) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Нові публікації згори, далі — старіші за вже прочитані. -> (назва, публікації, новий стан).
    Стан: newest — найновіший прочитаний номер, oldest — найстаріший, done — дійшли до початку каналу."""
    newest, oldest, done = int(state.get("newest") or 0), state.get("oldest"), bool(state.get("done"))
    title, got = "", {}
    used = 0

    def load(before: int | None) -> Page:
        nonlocal used, title
        if used:
            (sleep or time.sleep)(PAUSE)
        used += 1
        page = fetch_page(name, before, get)
        title = title or page.title
        for m in page.messages:
            got[m["id"]] = m
        return page

    # 1) нове: від верху, доки не дійдемо до вже прочитаного
    page = load(None)
    top = max((m["id"] for m in page.messages), default=newest)
    caught_up = True
    while newest and page.messages and min(m["id"] for m in page.messages) > newest and page.older:
        if used >= pages:
            caught_up = False
            break
        page = load(page.older)
    if not newest:   # перше читання: продовжуємо вниз від верхньої сторінки
        oldest, done = min(got, default=None), page.older is None
        cursor = page.older
    else:
        cursor = int(oldest) if oldest is not None and caught_up and not done else None
    # 2) старіше: від найстарішого прочитаного вниз, скільки дозволяє ліміт сторінок
    while cursor and not done and used < pages:
        page = load(cursor)
        if page.messages:
            oldest = min(oldest or cursor, *(m["id"] for m in page.messages))
        cursor = page.older
        done = cursor is None
    new_state = {"newest": max(top, newest) if caught_up else newest, "oldest": oldest, "done": done}
    return title or name, [got[k] for k in sorted(got)], new_state
