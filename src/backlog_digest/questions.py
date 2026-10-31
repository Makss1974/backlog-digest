"""Питання до бібліотеки (етап 7): питання → відповідь з усіх готових конспектів, з посиланнями на нотатки,
згрупованими повторами й позначкою обґрунтованості.

Уся бібліотека в один запит не влазить (буває десятки тисяч конспектів), тому так:
1. модель дає слова для пошуку — мовою конспектів і англійською, з синонімами;
2. за ними з бази відбираються кандидати (назва, суть, теги, тези) — без моделі;
3. модель частинами по BATCH вибирає справді доречні;
4. лише їхні повні конспекти (до MAX_SOURCES) йдуть у відповідь.
Відповідь — нотатка в теці «_Питання» сховища, номери джерел [n] у ній — посилання на нотатки.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db, library, notes, summarize
from .llm import Chain

SMALL = 120          # бібліотеку до такого розміру модель переглядає всю, без пошуку за словами
CANDIDATES = 300     # скільки кандидатів після пошуку за словами
BATCH = 100          # скільки коротких описів за один запит вибору
MAX_SOURCES = 40     # скільки повних конспектів іде у відповідь
MAX_CHARS = 120_000  # і не більше стількох символів конспектів

KEYWORDS_PROMPT = """You help search a personal library of summaries (videos, articles, books, posts).
The summaries are written in {language}. For the user's question give 10-25 search keywords and short phrases:
in {language} AND in English, with synonyms, related terms and names. Prefer words that would appear in a summary.
Answer ONLY with JSON: {{"keywords": ["...", ...]}}"""

SELECT_PROMPT = """You choose which summaries can help answer the user's question.
Question: {question}
Below is a numbered list: title | what it is about | tags | score. Choose ONLY items that contain an answer
or important facts for this question — not just the same broad topic. An empty list is a valid answer.
Answer ONLY with JSON: {{"relevant": [numbers]}}"""

ANSWER_PROMPT = """You answer a question using ONLY the numbered summaries below (the user's own library).
Write every text value in {language}. Cite sources by their numbers in square brackets, e.g. [3] or [2, 7].
Do not add facts that are not in the summaries; if the library does not answer something, say so in "gaps".
Be critical: weak or unscientific sources must not be presented as facts; mark disagreements.
Answer ONLY with JSON:
{{"answer": "the answer: 1-5 short paragraphs (use \\n\\n between them), with [n] citations",
  "points": [{{"text": "a key point of the answer", "sources": [numbers]}}],
  "repeats": [{{"ids": [numbers], "why": "what exactly they repeat", "best": number}}],
  "disputed": [{{"text": "a point where sources disagree, or a questionable claim", "sources": [numbers]}}],
  "grounding": "high" | "medium" | "low" | "unscientific",
  "grounding_note": "one or two sentences: what the answer relies on (research, experts, personal experience)",
  "gaps": "what the library does not answer; empty string if nothing"}}
"repeats" — ONLY sources that tell almost the same thing, so that one of them is enough. An empty list is fine.
"grounding" — how well the ANSWER is grounded, judged by the sources' evidence and agreement."""

Log = Callable[[str], None]


@dataclass
class Answer:
    question: str
    sources: list[dict[str, Any]] = field(default_factory=list)   # елементи з конспектом, номер = індекс + 1
    answer: str = ""
    points: list[dict[str, Any]] = field(default_factory=list)
    repeats: list[dict[str, Any]] = field(default_factory=list)
    disputed: list[dict[str, Any]] = field(default_factory=list)
    grounding: str = ""
    grounding_note: str = ""
    gaps: str = ""
    searched: int = 0     # скільки конспектів у бібліотеці
    candidates: int = 0   # скільки переглянула модель


# ---------------------------------------------------------------- пошук

def library_items(conn: sqlite3.Connection, language: str, source_id: int | None = None) -> list[dict[str, Any]]:
    """Усі готові конспекти мовою language; той самий конспект у двох списках — один раз."""
    sql = ("SELECT i.*, r.summary_json FROM items i JOIN results r ON r.key=i.key AND r.language=? "
           "WHERE i.status='done'" + (" AND i.source_id=?" if source_id else "") + " ORDER BY i.id")
    out, seen = [], set()
    for r in conn.execute(sql, (language, source_id) if source_id else (language,)):
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        d = dict(r)
        d["s"] = json.loads(d.pop("summary_json"))
        out.append(d)
    return out


def stems(text: str) -> set[str]:
    """Грубі основи слів: без закінчень, щоб «сну», «сон», «сном» збігалися (для будь-якої мови)."""
    out = set()
    for w in re.findall(r"\w+", text.lower()):
        if len(w) < 3 or w.isdigit():
            continue
        out.add(w[:max(4, len(w) - 2)] if len(w) > 5 else w)
    return out


def _fields(it: dict[str, Any]) -> list[tuple[str, int]]:
    s = it["s"]
    return [(it.get("title") or "", 3), (str(s.get("about") or ""), 2), (" ".join(s.get("tags") or []), 2),
            (" ".join(s.get("key_points") or []) + " " + " ".join(s.get("useful") or []), 1)]


def rank(items: list[dict[str, Any]], words: list[str], limit: int = CANDIDATES) -> list[dict[str, Any]]:
    """Кандидати за збігом основ слів: назва важить більше за тези. Без збігів — не кандидат."""
    query = set().union(*(stems(w) for w in words)) if words else set()
    scored = []
    for it in items:
        score = 0
        for text, weight in _fields(it):
            have = stems(text)
            score += weight * sum(1 for q in query if q in have or any(h.startswith(q) for h in have if len(q) >= 4))
        if score:
            scored.append((score, int(it["s"].get("score") or 0), it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [it for _, _, it in scored[:limit]]


def keywords(chain: Chain, question: str, language: str) -> list[str]:
    system = KEYWORDS_PROMPT.format(language=summarize.language_name(language))
    raw = library.parse_json(chain.complete(system, question).text)
    return [str(w) for w in raw.get("keywords") or [] if str(w).strip()][:40] + [question]


def short_line(i: int, it: dict[str, Any]) -> str:
    s = it["s"]
    return (f"{i}. {it.get('title') or ''} | {str(s.get('about') or '')[:300]} | "
            f"{', '.join(s.get('tags') or [])} | {s.get('score')}/5")


def select(chain: Chain, question: str, items: list[dict[str, Any]], log: Log) -> list[dict[str, Any]]:
    """Модель частинами вибирає доречні; порядок кандидатів (за збігом слів) зберігається."""
    chosen: list[dict[str, Any]] = []
    for start in range(0, len(items), BATCH):
        part = items[start:start + BATCH]
        raw = library.parse_json(chain.complete(SELECT_PROMPT.format(question=question),
                                                "\n".join(short_line(i, it) for i, it in enumerate(part, 1))).text)
        picked = {int(x) for x in raw.get("relevant") or [] if str(x).strip().isdigit()}
        chosen += [it for i, it in enumerate(part, 1) if i in picked]
    return chosen


def full_text(i: int, it: dict[str, Any]) -> str:
    s = it["s"]
    lines = [f"[{i}] {it.get('title') or ''} — {it.get('author') or ''} ({it.get('kind') or 'video'})",
             f"About: {s.get('about', '')}",
             "Key points: " + " | ".join(s.get("key_points") or []),
             "Useful: " + " | ".join(s.get("useful") or []),
             f"Evidence: {s.get('evidence', '?')} — {s.get('evidence_notes', '')}",
             f"Score: {s.get('score')}/5, verdict: {s.get('verdict')}"]
    if s.get("questionable_claims"):
        lines.append("Questionable: " + " | ".join(s["questionable_claims"]))
    return "\n".join(lines)


def ask(conn: sqlite3.Connection, chain: Chain, question: str, language: str, *, source_id: int | None = None,
        log: Log = print, on_step: Callable[[str, int], None] | None = None) -> Answer:
    """Відповідь на питання з бібліотеки. on_step(крок, число) — для повідомлень про хід роботи:
    "library" (скільки конспектів), "candidates" (скільки переглядає модель), "relevant" (скільки доречних)."""
    step = on_step or (lambda name, n: None)
    items = library_items(conn, language, source_id)
    result = Answer(question, searched=len(items))
    step("library", len(items))
    if not items:
        return result
    candidates = items if len(items) <= SMALL else rank(items, keywords(chain, question, language))
    result.candidates = len(candidates)
    step("candidates", len(candidates))
    if not candidates:
        return result
    chosen = select(chain, question, candidates, log)
    step("relevant", len(chosen))
    if not chosen:
        return result
    chosen.sort(key=lambda it: -int(it["s"].get("score") or 0))   # кращі — першими і з меншими номерами
    texts: list[str] = []
    size = 0
    for it in chosen[:MAX_SOURCES]:
        text = full_text(len(texts) + 1, it)
        if size + len(text) > MAX_CHARS and texts:
            break
        texts.append(text)
        result.sources.append(it)
        size += len(text)
    system = ANSWER_PROMPT.format(language=summarize.language_name(language))
    raw = library.parse_json(chain.complete(system, f"Question: {question}\n\n" + "\n\n".join(texts),
                                            deep=True).text)
    n = len(result.sources)

    def nums(xs: Any) -> list[int]:
        return list(dict.fromkeys(int(x) for x in (xs or []) if str(x).strip().isdigit() and 1 <= int(x) <= n))

    result.answer = str(raw.get("answer") or "").strip()
    result.points = [{"text": str(p.get("text") or ""), "sources": nums(p.get("sources"))}
                     for p in raw.get("points") or [] if isinstance(p, dict) and p.get("text")]
    result.disputed = [{"text": str(p.get("text") or ""), "sources": nums(p.get("sources"))}
                       for p in raw.get("disputed") or [] if isinstance(p, dict) and p.get("text")]
    for g in raw.get("repeats") or []:
        if isinstance(g, dict) and len(ids := nums(g.get("ids"))) >= 2:
            best = nums([g.get("best")])
            result.repeats.append({"ids": ids, "why": str(g.get("why") or ""),
                                   "best": best[0] if best and best[0] in ids else None})
    grounding = str(raw.get("grounding") or "").lower()
    result.grounding = grounding if grounding in summarize.EVIDENCE else ""
    result.grounding_note = str(raw.get("grounding_note") or "").strip()
    result.gaps = str(raw.get("gaps") or "").strip()
    return result


# ---------------------------------------------------------------- нотатка

def answer_path(root: Path, question: str, labels: dict[str, str]) -> Path:
    return root / f"_{notes.sanitize(labels['questions'], 60)}" / f"{notes.sanitize(question, 100)}.md"


def render(result: Answer, labels: dict[str, str], *, kind: str, root: Path, path: Path) -> str:
    L = labels

    def link(i: int, text: str | None = None) -> str:
        it = result.sources[i - 1]
        title = text or (it.get("title") or L["untitled"]).replace("[", "(").replace("]", ")").replace("|", "/")
        note = Path(it.get("note_path") or "")
        if not it.get("note_path"):
            return f"[{title}]({it['url']})" if it.get("url") else title
        if kind == "obsidian":
            try:
                rel = note.relative_to(root).with_suffix("").as_posix()
            except ValueError:
                rel = note.stem
            return f"[[{rel}|{title}]]"
        try:
            rel = os.path.relpath(note, path.parent).replace(os.sep, "/")
        except ValueError:   # нотатка на іншому диску
            rel = note.as_uri()
        return f"[{title}](<{rel}>)"

    def cite(text: str) -> str:
        """[3] і [2, 7] у тексті — посилання на нотатки джерел."""
        def one(m: re.Match[str]) -> str:
            ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
            if not ids or any(not 1 <= x <= len(result.sources) for x in ids):
                return m.group(0)
            return "[" + ", ".join(link(x, str(x)) for x in ids) + "]"
        return re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", one, text)

    def refs(ids: list[int]) -> str:
        return (" [" + ", ".join(link(x, str(x)) for x in ids) + "]") if ids else ""

    out = [notes.frontmatter({"question": result.question, "asked": db.now()[:16].replace("T", " "),
                              "sources": len(result.sources), "grounding": result.grounding or None}),
           f"# {result.question}", ""]
    if not result.sources:
        out += [L["nothing_found"], ""]
        return "\n".join(out)
    if result.grounding:
        out += [f"> **{L['grounding']}:** {L['evidence_' + result.grounding]}"
                + (f" — {result.grounding_note}" if result.grounding_note else ""), ""]
    out += [f"## {L['answer']}", "", cite(result.answer), ""]
    if result.points:
        out += [f"## {L['main_points']}", *[f"- {cite(p['text'])}{refs(p['sources'])}" for p in result.points], ""]
    if result.disputed:
        out += [f"## ⚖️ {L['disputed']}", *[f"- {cite(p['text'])}{refs(p['sources'])}" for p in result.disputed], ""]
    if result.repeats:
        out += [f"## 🔁 {L['similar']}", L["similar_hint"], ""]
        for g in result.repeats:
            out.append(f"- **{g['why']}**")
            out += [f"    - {'✅ ' if x == g.get('best') else ''}{link(x)}" for x in g["ids"]]
        out.append("")
    if result.gaps:
        out += [f"## {L['gaps']}", result.gaps, ""]
    out.append(f"## {L['sources']}")
    for i, it in enumerate(result.sources, 1):
        s = it["s"]
        verdict = notes.verdict_label(L, it.get("kind") or "video", str(s.get("verdict") or ""))
        out.append(f"{i}. {link(i)} — {'★' * int(s.get('score') or 0)} · {verdict} · "
                   f"{L['evidence_level']}: {L.get('evidence_' + str(s.get('evidence')), '?')}")
    out.append("")
    return "\n".join(out)


def write_answer(result: Answer, labels: dict[str, str], *, kind: str, root: Path) -> Path:
    path = answer_path(root, result.question, labels)
    notes.write_note(path, render(result, labels, kind=kind, root=root, path=path))
    return path


# ---------------------------------------------------------------- запуск (меню й команда bdig ask)

def run(conn: sqlite3.Connection, cfg: dict[str, Any], paths: Any, question: str,
        source_id: int | None = None) -> int:
    """Питання → відповідь у терміналі й нотатка в сховищі. 0 — є відповідь, 1 — не вийшло."""
    from . import pipeline
    from .i18n import t
    from .llm import LLMError

    question = " ".join(question.split())
    if not question:
        return 1
    proc = pipeline.Processor(conn, cfg, paths)
    if not proc.check_ready():
        return 1

    def on_step(name: str, n: int) -> None:
        print(t(f"questions.{name}", n=n))

    try:
        result = ask(conn, proc.chain, question, proc.language, source_id=source_id, on_step=on_step)
    except (LLMError, ValueError) as e:
        print(t("questions.failed", detail=str(e)[:300]))
        return 1
    if not result.searched:
        print(t("questions.empty"))
        return 1
    labels = proc.labels()
    if result.sources:
        print(f"\n{result.answer}\n")
        if result.grounding:
            print(f"{labels['grounding']}: {labels['evidence_' + result.grounding]}"
                  + (f" — {result.grounding_note}" if result.grounding_note else ""))
        for i, it in enumerate(result.sources, 1):
            print(f"  [{i}] {it.get('title') or labels['untitled']}")
    else:
        print(t("questions.nothing"))
    path = write_answer(result, labels, kind=cfg["output"]["kind"], root=proc.root)
    print("\n" + t("questions.saved", path=str(path)))
    return 0 if result.sources else 1
