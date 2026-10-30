"""Нотатка конспекту: Markdown з YAML-шапкою (frontmatter) — для Obsidian або будь-якої теки Markdown.

Підписи в нотатці (заголовки, вердикти) — мовою конспекту:
- для мов інтерфейсу (uk, en, fr, es) — з каталогів перекладів (ключі note.*);
- для інших мов — модель перекладає їх один раз, результат лежить у теці налаштувань
  note_labels/<код>.toml, і його можна виправити вручну;
- якщо перекласти не вдалося — англійські.
"""
from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config, i18n
from .paths import Paths

LABEL_KEYS = [
    "about", "key_points", "useful", "grounding", "evidence_level", "recommendations", "questionable",
    "water", "audience", "verdict", "score", "open_youtube", "open_file", "open_article", "untitled",
    "verdict_watch", "verdict_partial", "verdict_summary_enough", "verdict_skip",
    "verdict_read", "verdict_read_partial",
    "evidence_high", "evidence_medium", "evidence_low", "evidence_unscientific",
]

# Символи, заборонені в іменах файлів Windows, і ті, що ламають [[посилання]] в Obsidian
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f#^\[\]]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_PATH = 240   # з запасом до межі Windows у 260 символів
MAX_DIR_DEPTH = 5           # глибші теки (закладки бувають вкладені на 10+ рівнів) зливаються в останню
MAX_DIR = MAX_PATH - 80     # решта — для назви нотатки


def catalog_labels(lang: str) -> dict[str, str]:
    cat = i18n.catalog(lang)
    return {k: str(cat[f"note.{k}"]) for k in LABEL_KEYS}


def labels_file(paths: Paths, lang: str) -> Path:
    return paths.config_dir / "note_labels" / f"{lang}.toml"


def load_labels(paths: Paths, lang: str, translate: Callable[[dict[str, str]], dict[str, str]] | None = None
                ) -> dict[str, str]:
    """Підписи для мови конспекту. translate(англійські) -> перекладені (через модель), або None."""
    english = catalog_labels("en")
    if lang in i18n.UI_LANGUAGES:
        return catalog_labels(lang)
    path = labels_file(paths, lang)
    if path.exists():
        try:
            saved = tomllib.loads(path.read_text(encoding="utf-8-sig"))
            return {k: str(saved.get(k) or english[k]) for k in LABEL_KEYS}
        except (tomllib.TOMLDecodeError, OSError):
            pass
    if translate is None:
        return english
    try:
        labels = translate(english)
    except Exception:  # переклад підписів — не головне; без нього нотатка буде з англійськими
        return english
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.dumps(labels), encoding="utf-8")
    return labels


# ---------------------------------------------------------------- шляхи

def sanitize(name: str, max_len: int = 120, fallback: str = "_") -> str:
    s = (name or "").replace("[", "(").replace("]", ")")
    s = _FORBIDDEN.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    if len(s) > max_len:
        s = s[:max_len].rstrip(". ")
    if not s:
        s = fallback
    if s.upper() in _RESERVED:
        s = f"_{s}"
    return s


def note_dir(root: Path, *parts: str) -> Path:
    """<коренева тека>/<тека джерела>/<список>; тека джерела може бути вкладеною: «Відео/Наука».
    Не глибше MAX_DIR_DEPTH і не довше MAX_DIR: решта рівнів — в одній теці «Г · Ґ · Д»."""
    pieces = [sanitize(p, 60) for part in parts for p in re.split(r"[/\\]", part or "") if p.strip()]
    d = root
    for i, piece in enumerate(pieces):
        if i == MAX_DIR_DEPTH - 1 or len(str(d / piece)) > MAX_DIR:
            return d / sanitize(" · ".join(pieces[i:]), max(20, MAX_DIR - len(str(d)) - 1))
        d = d / piece
    return d


def note_path(directory: Path, title: str, unique: str, is_taken: Callable[[Path], bool]) -> Path:
    """Шлях нотатки. Довгу назву скорочуємо (довгі шляхи Windows); зайнята — додаємо (id)."""
    room = max(20, MAX_PATH - len(str(directory)) - len(unique) - 8)
    path = directory / f"{sanitize(title, min(120, room))}.md"
    if is_taken(path) or path.exists():
        path = directory / f"{sanitize(title, min(100, room))} ({unique}).md"
    return path


def write_note(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- вміст

def frontmatter(data: dict[str, Any]) -> str:
    """YAML-шапка. Рядки й списки — у форматі JSON, який YAML читає без змін (і без пасток з лапками)."""
    lines = ["---"]
    for k, v in data.items():
        if v is None or v == "":
            continue
        plain_int = isinstance(v, int) and not isinstance(v, bool)
        lines.append(f"{k}: {v if plain_int else json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def verdict_label(labels: dict[str, str], kind: str, verdict: str) -> str:
    """Вердикт словами: для текстів «Читати» / «Прочитати частину» замість «Дивитися» / «Переглянути частину»."""
    if kind != "video" and verdict in ("watch", "partial"):
        return labels["verdict_read" if verdict == "watch" else "verdict_read_partial"]
    return labels[f"verdict_{verdict}"]


def render_note(item: dict[str, Any], s: dict[str, Any], labels: dict[str, str], *, language: str,
                processed: str, duration: str = "") -> str:
    L = labels
    kind = item.get("kind") or "video"
    verdict = verdict_label(L, kind, s["verdict"])
    evidence = L[f"evidence_{s['evidence']}"]
    stars = "★" * s["score"] + "☆" * (5 - s["score"])
    if kind == "video":
        url = item.get("url") or ""
        channel = item.get("author") or ""
        head = " · ".join(x for x in (channel, duration, f"[{L['open_youtube']}]({url})" if url else "") if x)
        meta: dict[str, Any] = {"url": url, "channel": channel, "duration": duration}
    elif kind == "article":
        url = item.get("url") or ""
        site = item.get("author") or ""
        author, date = s.get("source_author") or "", s.get("source_date") or ""
        head = " · ".join(x for x in (site, author, date, f"[{L['open_article']}]({url})" if url else "") if x)
        meta = {"url": url, "site": site, "author": author, "date": date}
    else:
        file = item.get("path") or ""
        file_format = item.get("author") or ""
        link = f"[{L['open_file']}](<{Path(file).as_uri()}>)" if file and Path(file).is_absolute() else ""
        head = " · ".join(x for x in (s.get("book_title") or "", file_format, link) if x)
        meta = {"book": s.get("book_title") or "", "file": file, "format": file_format}
    front = frontmatter({
        "title": item.get("title") or "",
        **meta,
        "list": item.get("list_name") or "",
        "score": s["score"],
        "verdict": verdict,
        "evidence": evidence,
        "language": language,
        "tags": s["tags"],
        "processed": processed,
    })
    def bullets(xs: list[str]) -> list[str]:
        return [f"- {x}" for x in xs] or ["- —"]

    lines = [
        front, "",
        f"# {item.get('title') or L['untitled']}", "",
        f"> {head}", "",
        f"**{L['verdict']}: {verdict}** · {L['score']}: {s['score']}/5 {stars}", "",
        f"## {L['about']}", s["about"], "",
        f"## {L['key_points']}", *bullets(s["key_points"]), "",
        f"## {L['useful']}", *bullets(s["useful"]), "",
        f"## {L['grounding']}",
        f"**{L['evidence_level']}: {evidence}** · {L['recommendations']}: {s['recommendations_count']}",
    ]
    if s["evidence_notes"]:
        lines.append(s["evidence_notes"])
    lines.append("")
    if s["questionable_claims"]:
        lines += [f"**{L['questionable']}:**", *[f"- ⚠️ {c}" for c in s["questionable_claims"]], ""]
    lines += [
        f"## {L['water']}", f"**~{s['water_percent']}%** — {s['water_parts'] or '—'}", "",
        f"## {L['audience']}", s["audience"] or "—", "",
        f"## {L['verdict']}: {verdict}", s["verdict_reason"] or "—", "",
    ]
    return "\n".join(lines)
