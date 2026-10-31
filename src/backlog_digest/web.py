"""Веб-статті: звідки брати посилання і як дістати текст статті.

Посилання: пряме посилання; файл закладок браузера (HTML-експорт Chrome, Firefox, Edge, Safari —
теки закладок стають списками); експорт Pocket (HTML або CSV); експорт Instapaper (CSV);
будь-який CSV зі стовпцем url; текстовий файл — по посиланню в рядку.
Посилання на відео YouTube стають відео (той самий ключ, що й у джерелі YouTube — один конспект).

Текст: сторінку завантажуємо самі, основний текст (без меню, реклами й коментарів) витягує trafilatura.
Посилання на PDF читаємо як книгу.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from . import __version__, readers, youtube
from .i18n import t

IMPORT_EXT = (".html", ".htm", ".csv", ".txt")
USER_AGENT = f"Mozilla/5.0 (compatible; backlog-digest/{__version__})"
MAX_BYTES = 20 << 20    # більші сторінки й PDF не завантажуємо
TIMEOUT = 30
# мітки реклами й відстеження: та сама стаття з різних розсилок — одне посилання
_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|mc_cid|mc_eid|ref|ref_src|igshid|si)$", re.I)


@dataclass
class Link:
    url: str
    title: str = ""
    list_name: str = ""     # тека закладок / папка Instapaper; "" — без списку


@dataclass
class Article:
    text: str
    title: str
    author: str
    date: str
    site: str


class FetchError(Exception):
    """Сторінку не вдалося завантажити — можна спробувати пізніше."""


class Gone(Exception):
    """Сторінки більше немає (404, 410) — повторювати марно."""


# ---------------------------------------------------------------- посилання

def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://\S+$", s.strip(), re.I))


def is_youtube(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def normalize(url: str) -> str:
    """Без #якоря, без міток відстеження, без кінцевого «/»: одна стаття — одне посилання."""
    parts = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                                    urllib.parse.urlencode(query), ""))


def item_key(url: str) -> tuple[str, str]:
    """(ключ, вид елемента): відео YouTube — youtube:<id>, решта — url:<хеш адреси>."""
    if is_youtube(url):
        vid = youtube.video_id(url)
        if vid:
            return f"youtube:{vid}", "video"
    return "url:" + hashlib.sha1(normalize(url).encode("utf-8")).hexdigest()[:20], "article"


def site_name(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def read_links(path: Path) -> list[Link]:
    """Посилання з файлу імпорту (закладки, Pocket, Instapaper, CSV, TXT)."""
    raw = path.read_bytes()
    text = next((raw.decode(enc) for enc in ("utf-8-sig", "cp1251") if _decodes(raw, enc)), raw.decode("latin-1"))
    ext = path.suffix.lower()
    if ext in (".html", ".htm"):
        links = _bookmarks(text)
    elif ext == ".csv":
        links = _csv_links(text)
    else:
        links = [Link(line.strip()) for line in text.splitlines() if is_url(line)]
    seen: set[str] = set()
    out = []
    for link in links:   # той самий URL двічі — лише перший
        if not is_url(link.url):
            continue
        try:
            key = normalize(link.url)
        except ValueError:   # зіпсована адреса (напр. «http://[сміття]/») — пропускаємо, а не падаємо
            continue
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


def in_folders(list_name: str, folders: list[str]) -> bool:
    """Чи лежить посилання в одній із вибраних тек (разом із вкладеними). Порожній вибір — усі теки."""
    if not folders:
        return True
    name = list_name.lower()
    return any(name == f.lower() or name.startswith(f.lower() + "/") for f in folders)


def relative_list(list_name: str, folders: list[str]) -> str:
    """Назва списку від вибраної теки: «База/Наука/Фізика» при виборі «База/Наука» → «Наука/Фізика»."""
    name = list_name.lower()
    for f in sorted(folders, key=len, reverse=True):
        if name == f.lower() or name.startswith(f.lower() + "/"):
            parent = f.rpartition("/")[0]
            return list_name[len(parent) + 1:] if parent else list_name
    return list_name


def folder_counts(links: list[Link]) -> dict[str, int]:
    """Тека → скільки посилань у ній разом із вкладеними ("" — усі)."""
    counts: dict[str, int] = {}
    for link in links:
        parts = link.list_name.split("/") if link.list_name else []
        for depth in range(len(parts) + 1):
            key = "/".join(parts[:depth])
            counts[key] = counts.get(key, 0) + 1
    return counts


def _decodes(raw: bytes, enc: str) -> bool:
    try:
        raw.decode(enc)
    except UnicodeDecodeError:
        return False
    return True


class _Bookmarks(HTMLParser):
    """Формат «Netscape Bookmark File»: <DT><H3>тека</H3><DL> ... <DT><A HREF=...>назва</A> ... </DL>.
    Так експортують закладки всі браузери, а також Pocket (HTML)."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[Link] = []
        self.folders: list[str] = []
        self._pending = ""          # назва теки, чий <DL> ще не відкрився
        self._in: str | None = None  # "h3" / "a"
        self._text = ""
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self._in, self._text = "h3", ""
        elif tag == "a":
            self._in, self._text, self._href = "a", "", dict(attrs).get("href") or ""
        elif tag == "dl":
            self.folders.append(self._pending)
            self._pending = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in == "h3":
            self._pending = self._text.strip()
            self._in = None
        elif tag == "a" and self._in == "a":
            path = "/".join(f for f in self.folders if f)
            self.links.append(Link(self._href, self._text.strip(), path))
            self._in = None
        elif tag == "dl" and self.folders:
            self.folders.pop()

    def handle_data(self, data: str) -> None:
        if self._in:
            self._text += data


def _bookmarks(html: str) -> list[Link]:
    parser = _Bookmarks()
    parser.feed(html)
    links = parser.links
    # Панель закладок браузера — службова тека, не тема: її назву прибираємо зі шляху
    toolbar = {"bookmarks bar", "bookmarks toolbar", "панель закладок", "favorites bar", "панель избранного",
               "barre de favoris", "barra de marcadores", "barre personnelle", "панель вибраного"}
    for link in links:
        first, _, rest = link.list_name.partition("/")
        if first.lower() in toolbar:
            link.list_name = rest
    # одна тека-обгортка навколо всього (напр. «!Tabsbook Export») — теж не тема
    roots = {link.list_name.partition("/")[0] for link in links}
    if len(links) > 1 and len(roots) == 1 and "" not in roots:
        for link in links:
            link.list_name = link.list_name.partition("/")[2]
    return links


def _csv_links(text: str) -> list[Link]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    columns = {c.lower().strip(): c for c in rows[0] if c}
    url_col = columns.get("url") or columns.get("link") or columns.get("href")
    if not url_col:
        return []
    title_col = columns.get("title")
    folder_col = columns.get("folder")   # Instapaper; у Pocket тек немає
    return [Link((r.get(url_col) or "").strip(), (r.get(title_col) or "").strip() if title_col else "",
                 _folder(r.get(folder_col) or "") if folder_col else "") for r in rows]


def _folder(name: str) -> str:
    # Instapaper: Unread — це «вхідні», Archive — прочитане; це стани, а не теми
    return "" if name.strip().lower() in ("unread", "archive", "starred") else name.strip()


# ---------------------------------------------------------------- текст статті

def fetch_article(url: str) -> Article:
    """Завантажує сторінку й витягує основний текст. Gone — сторінки немає; FetchError — помилка
    (спробуємо пізніше); readers.NoText — тексту статті на сторінці немає (вхід, платний доступ, лише JS)."""
    data, content_type, final_url = _download(url)
    if "pdf" in content_type or (not content_type and final_url.lower().endswith(".pdf")):
        return _pdf(data, final_url)
    import trafilatura  # повільний імпорт — лише коли справді потрібен

    # без «широкого пошуку» дати: інакше рік у «© 2026» стає датою статті «2026-01-01»
    doc = trafilatura.bare_extraction(data, url=final_url, with_metadata=True, include_comments=False,
                                      include_tables=True, favor_recall=True,
                                      date_extraction_params={"extensive_search": False, "original_date": True})
    text = (getattr(doc, "text", None) or "").strip() if doc is not None else ""
    if len(text) < readers.MIN_SCAN_TEXT:
        raise readers.NoText(t("web.no_article"))
    return Article(text=text, title=(getattr(doc, "title", None) or "").strip(),
                   author=(getattr(doc, "author", None) or "").strip(), date=(getattr(doc, "date", None) or ""),
                   site=site_name(final_url))   # адреса сайту, а не його «назва» (там буває власник)


def _download(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise FetchError(t("web.too_big", mb=MAX_BYTES >> 20))
            return data, (resp.headers.get("Content-Type") or "").lower(), resp.geturl()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise Gone(t("web.gone", code=e.code)) from e
        raise FetchError(t("web.http_error", code=e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(t("web.network_error", detail=str(getattr(e, "reason", e))[:200])) from e


def _pdf(data: bytes, url: str) -> Article:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page.pdf"
        path.write_bytes(data)
        try:
            text = readers.extract_text(path)
        except readers.ReadError as e:
            raise FetchError(str(e)) from e
    name = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).stem)
    return Article(text=text, title=name, author="", date="", site=site_name(url))
