"""Текст із файлів: книги й документи.

Вбудовано (+ pypdf): PDF, EPUB, FB2, FB2.ZIP, DOCX, ODT, RTF, HTML, TXT, MD.
Через безкоштовні зовнішні програми, якщо встановлені: DJVU (DjVuLibre), MOBI/AZW3/AZW/DOC (Calibre).

Два види невдач:
- NoText — файл прочитано, але тексту в ньому немає (скан без розпізнавання): це не помилка,
  повторювати не треба;
- ReadError — файл не вдалося прочитати (пошкоджений, немає потрібної програми): буде повтор.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from . import tools
from .i18n import t

TEXT_EXT = (".pdf", ".epub", ".fb2", ".fb2.zip", ".docx", ".odt", ".rtf", ".html", ".htm", ".txt", ".md",
            ".djvu", ".djv", ".mobi", ".azw3", ".azw", ".doc")

MIN_SCAN_TEXT = 200   # менше символів на весь файл — це скан без текстового шару


class ReadError(Exception):
    pass


class NoText(Exception):
    pass


def file_ext(path: Path) -> str:
    name = path.name.lower()
    return ".fb2.zip" if name.endswith(".fb2.zip") else path.suffix.lower()


def is_supported(path: Path) -> bool:
    return file_ext(path) in TEXT_EXT


def title_of(path: Path) -> str:
    """Назва файлу без розширення (і без подвійного .fb2.zip)."""
    ext = file_ext(path)
    return path.name[: -len(ext)] if ext and path.name.lower().endswith(ext) else path.stem


def extract_text(path: Path) -> str:
    ext = file_ext(path)
    reader = _READERS.get(ext)
    if reader is None:
        raise ReadError(t("read.unsupported", ext=ext))
    if path.stat().st_size == 0:
        raise ReadError(t("read.empty_file"))
    try:
        text = reader(path)
    except (ReadError, NoText):
        raise
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, ValueError) as e:
        raise ReadError(t("read.broken", detail=str(e)[:200])) from e
    if len(text.strip()) < MIN_SCAN_TEXT:
        raise NoText(t("read.no_text"))
    return text


# ---------------------------------------------------------------- зовнішні програми

def _tool(tool_id: str) -> str:
    tool = next(x for x in tools.TOOLS if x.id == tool_id)
    exe = tools.find(tool)
    if not exe:
        raise ReadError(t(f"read.need_{tool_id}"))
    return exe


def _read_djvu(path: Path) -> str:
    exe = _tool("djvulibre")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.txt"
        r = subprocess.run([exe, str(path), str(out)], capture_output=True, timeout=600)
        if r.returncode != 0 or not out.exists():
            raise ReadError(t("read.tool_failed", tool="djvutxt", detail=r.stderr.decode("utf-8", "replace")[:200]))
        return squeeze(out.read_bytes().decode("utf-8", errors="replace"))


def _read_calibre(path: Path) -> str:
    exe = _tool("calibre")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.txt"
        r = subprocess.run([exe, str(path), str(out)], capture_output=True, timeout=900,
                           env={**os.environ, "CALIBRE_NO_NATIVE_FILEDIALOGS": "1"})
        if r.returncode != 0 or not out.exists():
            raise ReadError(t("read.tool_failed", tool="ebook-convert",
                              detail=r.stderr.decode("utf-8", "replace")[-200:]))
        return squeeze(out.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- вбудовані формати

def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ReadError(t("read.encrypted"))
        pages = []
        for i, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[p. {i}]\n{text}")   # номери сторінок — щоб модель могла на них посилатися
    except PdfReadError as e:
        raise ReadError(t("read.broken", detail=str(e)[:200])) from e
    return "\n\n".join(pages)


def _read_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        # порядок читання — зі spine файлу .opf
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        opf_path = rootfile.get("full-path", "") if rootfile is not None else ""
        opf = ElementTree.fromstring(z.read(opf_path))
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        manifest = {i.get("id"): i.get("href") for i in opf.iterfind(".//{*}item")}
        order = [manifest[r.get("idref")] for r in opf.iterfind(".//{*}itemref") if r.get("idref") in manifest]
        chunks = []
        for href in order:
            try:
                html = z.read(base + str(href)).decode("utf-8", errors="replace")
            except KeyError:
                continue
            chunks.append(html_text(html))
    return squeeze("\n".join(chunks))


def _read_fb2(path: Path) -> str:
    return _fb2_text(path.read_bytes())


def _read_fb2_zip(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".fb2")), None)
        if not name:
            raise ReadError(t("read.no_fb2_in_zip"))
        return _fb2_text(z.read(name))


def _fb2_text(data: bytes) -> str:
    root = ElementTree.fromstring(data)
    parts = []
    for body in root.iterfind(".//{*}body"):
        if body.get("name") == "notes":   # виноски пропускаємо
            continue
        in_titles = {id(el) for title in body.iterfind(".//{*}title") for el in title.iter()}
        for el in body.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "title":
                parts.append("\n## " + " ".join(x.strip() for x in el.itertext() if x.strip()))
            elif tag in ("p", "v", "subtitle") and id(el) not in in_titles:
                parts.append("".join(el.itertext()))
    return squeeze("\n".join(parts))


def _read_docx(path: Path) -> str:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paras = ["".join(x.text or "" for x in p.iter(f"{w}t")) for p in root.iter(f"{w}p")]
    return "\n".join(p for p in paras if p.strip())


def _read_odt(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("content.xml"))
    paras = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("p", "h"):
            text = "".join(el.itertext()).strip()
            if text:
                paras.append(("## " if tag == "h" else "") + text)
    return "\n".join(paras)


def _read_html(path: Path) -> str:
    return squeeze(html_text(_read_plain(path)))


def _read_rtf(path: Path) -> str:
    """Спрощений RTF: текст, абзаци, кирилиця (\\'hh у cp1251 та \\uN)."""
    s = path.read_bytes().decode("latin-1")
    # службові групи (шрифти, кольори, стилі, картинки) прибираємо
    s = re.sub(r"\{\\\*[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", s)
    s = re.sub(r"\{\\(?:fonttbl|colortbl|stylesheet|info|pict)[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", s)
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            m = re.match(r"\\'([0-9a-fA-F]{2})", s[i:])
            if m:
                out.append(bytes([int(m.group(1), 16)]).decode("cp1251", errors="replace"))
                i += 4
                continue
            m = re.match(r"\\u(-?\d+)\??", s[i:])
            if m:
                out.append(chr(int(m.group(1)) % 65536))
                i += len(m.group(0))
                if i < len(s) and s[i] not in "\\{}":   # символ-заміна після \uN
                    i += 1
                continue
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if m:
                if m.group(1) in ("par", "line", "sect", "page"):
                    out.append("\n")
                elif m.group(1) == "tab":
                    out.append("\t")
                i += len(m.group(0))
                continue
            if i + 1 < len(s):
                out.append(s[i + 1] if s[i + 1] in "\\{}" else "")
            i += 2
            continue
        if ch not in "{}\r\n":
            out.append(ch)
        i += 1
    return squeeze("".join(out))


def _read_plain(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


_READERS: dict[str, Callable[[Path], str]] = {
    ".pdf": _read_pdf, ".epub": _read_epub, ".fb2": _read_fb2, ".fb2.zip": _read_fb2_zip,
    ".docx": _read_docx, ".odt": _read_odt, ".rtf": _read_rtf, ".html": _read_html, ".htm": _read_html,
    ".txt": _read_plain, ".md": _read_plain,
    ".djvu": _read_djvu, ".djv": _read_djvu,
    ".mobi": _read_calibre, ".azw3": _read_calibre, ".azw": _read_calibre, ".doc": _read_calibre,
}


# ---------------------------------------------------------------- спільне

class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("## ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return "".join(parser.parts)


def squeeze(s: str) -> str:
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def split_chunks(text: str, size: int) -> list[str]:
    """Ділить довгий текст на частини ~size символів, по межах абзаців."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for para in text.split("\n"):
        if cur_len + len(para) > size and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        while len(para) > size:   # дуже довгий абзац
            chunks.append(para[:size])
            para = para[size:]
        cur.append(para)
        cur_len += len(para) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks
