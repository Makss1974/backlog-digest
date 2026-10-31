"""Організація бібліотеки: сортування списку за темами (етап 4).

Режими: «Мої теми» (структуру задає користувач), «Важливість» (кошики за правилом користувача),
«Авто» (модель пропонує теми, користувач править). Спершу — попередній перегляд, нотатки переносяться
в теки тем лише після згоди. Схема зберігається, і нові конспекти цього списку розкладаються в неї самі.

Модель бачить лише стислий опис конспектів (назва, про що, теги, оцінка), а не тексти: так на сотню
елементів іде кілька запитів. Назви тем — мовою конспектів.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import db, notes, summarize, youtube
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


# ---------------------------------------------------------------- звіт

REPORT_PROMPT = """You analyse the list of summaries "{name}". Write in {language}.
1. similar — ONLY REPEATS: items that tell almost the same thing (the same advice, facts, the same speaker or
   primary source), so that one of the group is enough. A shared general topic ("both are about the brain") is NOT
   a repeat. An empty list is better than invented groups. For each group give the numbers, what exactly repeats,
   and the number of the best one.
2. off_topic — items that clearly do not fit the overall theme of the list (if the list is mixed, do not invent —
   return []).
Answer ONLY with JSON:
{{"theme": "the overall theme of the list in one sentence",
  "similar": [{{"ids": [numbers], "why": "what they share", "best": number}}],
  "off_topic": [{{"id": number, "why": "why it does not fit"}}]}}"""


def make_report(conn: sqlite3.Connection, chain: Chain, source_id: int, list_name: str, language: str
                ) -> dict[str, Any]:
    """Один запит до моделі: загальна тема списку, групи повторів, не в темі. Зберігається в схемі списку."""
    items = db.done_items(conn, source_id, list_name, language)
    system = REPORT_PROMPT.format(name=list_name, language=summarize.language_name(language))
    raw = parse_json(chain.complete(system, compact(items)).text)
    ids = {i: it["id"] for i, it in enumerate(items, 1)}

    def item_id(x: Any) -> int | None:
        return ids.get(int(x)) if str(x).strip().isdigit() else None

    report: dict[str, Any] = {"theme": str(raw.get("theme") or ""), "similar": [], "off_topic": []}
    for g in raw.get("similar") or []:
        if not isinstance(g, dict):
            continue
        members = list(dict.fromkeys(m for m in (item_id(x) for x in g.get("ids") or []) if m))
        if len(members) >= 2:
            best = item_id(g.get("best"))
            report["similar"].append({"ids": members, "why": str(g.get("why") or ""),
                                      "best": best if best in members else None})
    for o in raw.get("off_topic") or []:
        found = item_id(o.get("id")) if isinstance(o, dict) else None
        if found:
            report["off_topic"].append({"id": found, "why": str(o.get("why") or "")})
    if not db.get_scheme(conn, source_id, list_name):
        db.save_scheme(conn, source_id, list_name, "none", [], "")
    db.save_report(conn, source_id, list_name, report)
    return report


# ---------------------------------------------------------------- індекс

GOOD_VERDICTS = ("watch", "partial")


def index_path(base: Path, list_name: str, labels: dict[str, str]) -> Path:
    name = list_name.rsplit("/", 1)[-1] if list_name else ""
    return base / f"_{notes.sanitize(labels['index'] + (' — ' + name if name else ''), 100)}.md"


def write_index(conn: sqlite3.Connection, source_id: int, list_name: str, *, base: Path, root: Path, kind: str,
                labels: dict[str, str], language: str) -> Path:
    """Індексна нотатка списку: найкращі, за темами (або все за оцінкою), повтори, не в темі, ненаукове,
    проблеми. kind — obsidian ([[вікі-посилання]]) або markdown (звичайні посилання з відносним шляхом)."""
    L = labels
    path = index_path(base, list_name, L)
    items = db.done_items(conn, source_id, list_name, language)
    by_id = {it["id"]: it for it in items}
    scheme = db.get_scheme(conn, source_id, list_name)
    topics = scheme["topics"] if scheme else []
    report = scheme["report"] if scheme else None
    other = L["other_topic"]

    def link(it: dict[str, Any], in_table: bool = False) -> str:
        title = (it.get("title") or L["untitled"]).replace("|", "/").replace("[", "(").replace("]", ")")
        note = Path(it.get("note_path") or "")
        if kind == "obsidian":
            try:
                rel = note.relative_to(root).with_suffix("").as_posix()
            except ValueError:
                rel = note.stem
            pipe = "\\|" if in_table else "|"   # у таблицях Obsidian «|» треба екранувати
            return f"[[{rel}{pipe}{title}]]"
        try:
            rel = os.path.relpath(note, path.parent).replace(os.sep, "/")
        except ValueError:   # нотатка на іншому диску
            rel = note.as_uri()
        return f"[{title}](<{rel}>)"

    def details(it: dict[str, Any]) -> str:
        if (it.get("kind") or "video") == "video":
            return youtube.format_duration(it.get("duration"))
        return str(it.get("author") or "")

    def row(it: dict[str, Any]) -> str:
        s = it["s"]
        about = str(s.get("about") or "").replace("|", "/").replace("\n", " ")[:160]
        verdict = notes.verdict_label(L, it.get("kind") or "video", s["verdict"])
        return f"| {link(it, True)} | {'★' * int(s['score'])} | {verdict} | {details(it)} | {about} |"

    head = (f"| {L['col_title']} | {L['score']} | {L['verdict']} | {L['col_details']} | {L['about']} |\n"
            "|---|---|---|---|---|")
    by_score = sorted(items, key=lambda it: (-int(it["s"]["score"]), it.get("position") or 0))
    stat = conn.execute(
        "SELECT COUNT(*) c, SUM(status='done') d, SUM(status IN ('new','error')) q, SUM(status='no_text') nt "
        "FROM items WHERE source_id=? AND list_name=?", (source_id, list_name)).fetchone()
    title = list_name.rsplit("/", 1)[-1] if list_name else ""
    out = [f"# {L['index']}" + (f": {title}" if title else ""), "",
           f"{L['stat_done']} **{stat['d'] or 0}** {L['stat_of']} {stat['c']} · {L['stat_queued']} {stat['q'] or 0}"
           f" · {L['stat_no_text']} {stat['nt'] or 0} · {L['stat_updated']} {db.now()[:16].replace('T', ' ')}", ""]
    if report and report.get("theme"):
        out += [f"> {report['theme']}", ""]

    best = [it for it in by_score if int(it["s"]["score"]) >= 4 and it["s"]["verdict"] in GOOD_VERDICTS]
    if best:
        out += [f"## ⭐ {L['best']}", head, *[row(it) for it in best], ""]

    if topics:
        out += [f"## {L['by_topic']}", ""]
        for topic in topics:
            group = [it for it in by_score if (it.get("topic") or other) == topic]
            if "/" not in topic:
                total = sum(1 for it in items if (it.get("topic") or other) == topic
                            or (it.get("topic") or "").startswith(topic + "/"))
                if total:
                    out += [f"### {topic} ({total})", ""]
            elif group:
                out.append(f"#### {topic.split('/', 1)[1]} ({len(group)})")
            if group:
                out += [head, *[row(it) for it in group], ""]
    else:
        out += [f"## {L['all_by_score']}", head, *[row(it) for it in by_score], ""]

    if report and report.get("similar"):
        groups = []
        for g in report["similar"]:
            members = [by_id[m] for m in g["ids"] if m in by_id]
            if len(members) >= 2:
                groups.append(f"- **{g['why']}**")
                groups += [f"    - {'✅ ' if m['id'] == g.get('best') else ''}{link(m)} ({m['s']['score']}/5)"
                           for m in members]
        if groups:
            out += [f"## 🔁 {L['similar']}", L["similar_hint"], "", *groups, ""]
    if report and report.get("off_topic"):
        rows = [f"- {link(by_id[o['id']])} — {o['why']}" for o in report["off_topic"] if o["id"] in by_id]
        if rows:
            out += [f"## ↪️ {L['off_topic']}", *rows, ""]

    pseudo = [it for it in by_score if it["s"].get("evidence") == "unscientific"]
    if pseudo:
        out += [f"## 🚫 {L['unscientific']}",
                *[f"- {link(it)} — " + "; ".join(it["s"].get("questionable_claims", [])[:2]) for it in pseudo], ""]

    problems = conn.execute(
        "SELECT title, url, path, status, error FROM items WHERE source_id=? AND list_name=? "
        "AND status IN ('error', 'failed', 'no_text') ORDER BY status, position", (source_id, list_name)).fetchall()
    if problems:
        out.append(f"## ⚠️ {L['problems']}")
        for p in problems:
            name = str(p["title"] or p["url"] or p["path"] or "?").replace("[", "(").replace("]", ")")
            shown = f"[{name}]({p['url']})" if p["url"] else name
            what = L[f"status_{p['status']}"] + (f": {str(p['error'])[:100]}" if p["error"] else "")
            out.append(f"- {shown} — {what}")
        out.append("")

    notes.write_note(path, "\n".join(out))
    return path
