"""Організація бібліотеки: сортування списку за темами (етап 4).

Режими: «Мої теми» (структуру задає користувач), «Важливість» (кошики за правилом користувача),
«Авто» (модель пропонує теми, користувач править). Спершу — попередній перегляд, нотатки переносяться
в теки тем лише після згоди. Схема зберігається, і нові конспекти цього списку розкладаються в неї самі.

Модель бачить лише стислий опис конспектів (назва, про що, теги, оцінка), а не тексти: так на сотню
елементів іде кілька запитів. Назви тем — мовою конспектів.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from . import db, notes, summarize
from .i18n import t
from .llm import Chain

MODES = ("manual", "importance", "auto")
BATCH = 40          # скільки конспектів розкладати за один запит
MAX_TOPIC = 60      # назва теми — це й назва теки

PROPOSE_PROMPT = """You organize a library of summaries of videos, articles and books.
Propose a tree of topics for this list: {n} top-level topics, and 2-5 subtopics inside the big ones.
Topics must be meaningful, must not overlap and must cover almost the whole list.
Do not create a catch-all topic like "Other": it is added automatically.
Write the topic names in {language}. Never use "/" inside a name.
{criteria}
Answer ONLY with JSON: {{"topics": ["Topic", "Topic/Subtopic", ...]}}"""

CLASSIFY_PROMPT = """You sort summaries into topics. Allowed topics (choose ONLY from this list, exact text):
{topics}
Choose the deepest fitting topic (a subtopic when it fits). If nothing fits, choose "{other}".
{criteria}
Answer ONLY with JSON: {{"assign": {{"<number>": "<topic>", ...}}}} for EVERY number below."""

Log = Callable[[str], None]


# ---------------------------------------------------------------- теми

def compact(items: list[dict[str, Any]], with_points: bool = False) -> str:
    """Короткий опис кожного елемента для моделі: номер, назва, суть, теги, оцінка."""
    lines = []
    for i, it in enumerate(items, 1):
        s = it["s"]
        line = (f"{i}. {it.get('title') or ''} | {s.get('about', '')} | tags: {', '.join(s.get('tags', []))} | "
                f"{s.get('score')}/5, {s.get('verdict')}, evidence: {s.get('evidence', '?')}")
        if with_points:
            line += " | " + "; ".join(s.get("key_points", [])[:3])
        lines.append(line)
    return "\n".join(lines)


def clean_topic(name: str) -> str:
    return notes.sanitize(name.replace("/", " "), MAX_TOPIC, fallback="") if name else ""


def normalize_topics(topics: list[str], other: str) -> list[str]:
    """Не глибше двох рівнів, без повторів; батьківська тема — перед підтемами; «Інше» — останнім."""
    out: list[str] = []
    for topic in topics:
        parts = [clean_topic(p.strip()) for p in str(topic).split("/") if p.strip()][:2]
        path = "/".join(p for p in parts if p)
        if not path or path in out or path.lower() == other.lower():
            continue
        if "/" in path and path.split("/")[0] not in out:
            out.append(path.split("/")[0])
        out.append(path)
    return out + [other]


def parse_tree(text: str, other: str) -> list[str]:
    """Текст із теми в рядку: підтема — з відступом (пробіли або таб) або як «Тема/Підтема».
    Рядки з # — коментарі."""
    topics: list[str] = []
    parent = ""
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        name = raw.strip().lstrip("-•*├└─│ ").strip()
        if "/" in name:
            topics.append(name)
            parent = name.split("/")[0].strip()
        elif raw[:1] in (" ", "\t") and parent:
            topics.append(f"{parent}/{name}")
        else:
            parent = name
            topics.append(name)
    return normalize_topics(topics, other)


def tree_text(topics: list[str]) -> str:
    return "\n".join(("    └ " + x.split("/", 1)[1]) if "/" in x else x for x in topics)


def draft_text(topics: list[str], other: str) -> str:
    """Теми як текст для редагування: підтема — з відступом."""
    return "\n".join(("  " + x.split("/", 1)[1]) if "/" in x else x for x in topics if x != other)


def propose_topics(chain: Chain, items: list[dict[str, Any]], n: str, criteria: str, language: str,
                   other: str) -> list[str]:
    system = PROPOSE_PROMPT.format(n=n, language=summarize.language_name(language),
                                   criteria=f"User criteria: {criteria}" if criteria else "")
    data = parse_json(chain.complete(system, compact(items)).text)
    topics = data.get("topics", [])
    return normalize_topics([str(x) for x in topics] if isinstance(topics, list) else [], other)


def classify(chain: Chain, items: list[dict[str, Any]], topics: list[str], criteria: str, other: str,
             log: Log = print) -> dict[int, str]:
    """id елемента -> тема. Пакетами по BATCH; тема поза списком — найближча або «Інше»."""
    system = CLASSIFY_PROMPT.format(topics="\n".join(f"- {x}" for x in topics), other=other,
                                    criteria=f"How to sort (user criteria): {criteria}" if criteria else "")
    result: dict[int, str] = {}
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        if len(items) > BATCH:
            log(t("sort.classifying", start=start + 1, end=start + len(batch), total=len(items)))
        assign = parse_json(chain.complete(system, compact(batch, with_points=True)).text).get("assign", {})
        if not isinstance(assign, dict):
            assign = {}
        for i, it in enumerate(batch, 1):
            result[it["id"]] = closest(str(assign.get(str(i), "")).strip(), topics, other)
    return result


def closest(topic: str, topics: list[str], other: str) -> str:
    """Модель трохи змінила назву теми — шукаємо без урахування регістру, потім за першою частиною."""
    low = topic.lower().strip()
    for cand in topics:
        if cand.lower() == low:
            return cand
    head = low.split("/")[0].strip()
    for cand in topics:
        if cand.lower() == head:
            return cand
    return other


def preview(items: list[dict[str, Any]], assign: dict[int, str], topics: list[str]) -> list[tuple[str, int, str]]:
    """Рядки попереднього перегляду: (тема, скільки, приклади назв)."""
    groups: dict[str, list[dict[str, Any]]] = {x: [] for x in topics}
    for it in items:
        groups.setdefault(assign.get(it["id"], topics[-1]), []).append(it)
    rows = []
    for x in topics:
        group = sorted(groups.get(x, []), key=lambda it: -(it["s"].get("score") or 0))
        label = ("   └ " + x.split("/", 1)[1]) if "/" in x else x
        rows.append((label, len(group), "; ".join((it.get("title") or "")[:30] for it in group[:2])))
    return rows


# ---------------------------------------------------------------- застосування

def apply(conn: sqlite3.Connection, items: list[dict[str, Any]], assign: dict[int, str],
          rewrite: Callable[[dict[str, Any], dict[str, Any]], Any]) -> int:
    """Записує теми в базу й переписує нотатки в теки тем (rewrite(item, summary)). Повертає скільки."""
    moved = 0
    for it in items:
        topic = assign.get(it["id"])
        if topic is None:
            continue
        db.set_item_meta(conn, it["id"], topic=topic)
        it["topic"] = topic
        rewrite(it, it["s"])
        moved += 1
    return moved


def assign_new(conn: sqlite3.Connection, chain: Chain, language: str, other: str,
               rewrite: Callable[[dict[str, Any], dict[str, Any]], Any], log: Log = print) -> int:
    """Нові конспекти без теми — у наявні схеми їхніх списків (після обробки черги)."""
    total = 0
    for source_id, list_name in db.lists_with_scheme(conn):
        scheme = db.get_scheme(conn, source_id, list_name)
        items = [it for it in db.done_items(conn, source_id, list_name, language) if not it.get("topic")]
        if not scheme or not items:
            continue
        log(t("sort.assigning", n=len(items), name=list_name or "—"))
        assign = classify(chain, items, scheme["topics"], scheme["criteria"], other, log)
        total += apply(conn, items, assign, rewrite)
    return total


def parse_json(text: str) -> dict[str, Any]:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("the model did not return JSON")
    data = json.loads(s[start:end + 1])
    return data if isinstance(data, dict) else {}
