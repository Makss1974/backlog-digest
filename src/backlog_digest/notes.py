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
    "water", "audience", "verdict", "score", "open_youtube", "untitled",
    "verdict_watch", "verdict_partial", "verdict_summary_enough", "verdict_skip",
    "evidence_high", "evidence_medium", "evidence_low", "evidence_unscientific",
]

# Символи, заборонені в іменах файлів Windows, і ті, що ламають [[посилання]] в Obsidian
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f#^\[\]]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_PATH = 240   # з запасом до межі Windows у 260 символів


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
    """<коренева тека>/<тека джерела>/<список>; тека джерела може бути вкладеною: «Відео/Наука»."""
    d = root
    for part in parts:
        for piece in re.split(r"[/\\]", part or ""):
            if piece.strip():
                d = d / sanitize(piece)
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


def render_video_note(item: dict[str, Any], s: dict[str, Any], labels: dict[str, str], *, language: str,
                      processed: str, duration: str) -> str:
    L = labels
    verdict = L[f"verdict_{s['verdict']}"]
    evidence = L[f"evidence_{s['evidence']}"]
    stars = "★" * s["score"] + "☆" * (5 - s["score"])
    url = item.get("url") or ""
    channel = item.get("author") or ""
    head = " · ".join(x for x in (channel, duration, f"[{L['open_youtube']}]({url})" if url else "") if x)
    front = frontmatter({
        "title": item.get("title") or "",
        "url": url,
        "channel": channel,
        "duration": duration,
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
