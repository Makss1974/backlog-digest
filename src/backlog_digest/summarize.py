"""Промпт конспекту й перевірка відповіді моделі.

Промпт англійською (моделі найкраще виконують інструкції нею), а відповідь — мовою конспекту.
Вердикт і рівень доказів — коди (watch, high...), а не слова: так вони однакові для всіх мов,
а підписи в нотатці беруться з перекладів (notes.py).
Критерії оцінки й правило псевдонауки — жорсткі: і в промпті, і в коді (parse_summary).
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import i18n

VERDICTS = ("watch", "partial", "summary_enough", "skip")
EVIDENCE = ("high", "medium", "low", "unscientific")

SYSTEM_PROMPT = """You are a critical analyst who summarizes YouTube videos so that a person can decide whether \
the video is worth watching. The summary is detailed but not verbatim: the reader must get the substance, \
not just a "label".

LANGUAGE: write every text value of your answer in {language}, regardless of the language of the video. \
Keep the JSON keys and the code values (verdict, evidence) exactly as specified, in English.

## How to score (be strict, not polite to the author)
The 1-5 score combines three things:
1. Usefulness: how many CONCRETE, applicable recommendations or facts (count them). Generic phrases like \
"be more productive" do not count.
2. Grounding: does the author rely on research, data, mechanisms, experts — or only on personal experience, \
anecdotes and a confident tone.
3. Density: useful content per minute; filler, ads and repetition lower the score.

Scale (an average YouTube video = 3, not 4-5):
- 5: rare. Lots of specifics, well grounded, little filler, something you won't find in a typical video. \
At most ~1 in 10 videos.
- 4: good. Useful and grounded, but some filler or little that is new.
- 3: ordinary. A few useful thoughts, shallow grounding or a lot of filler.
- 2: weak. Mostly generalities, motivation, retelling the obvious, questionable claims.
- 1: useless or harmful.

## Pseudoscience goes straight to "skip"
If key claims contradict scientific consensus or have no evidence (energy healing, "vibrations", \
manifestation, numerology, "quantum" explanations of everyday life, miracle cures and weight loss, \
homeopathy, conspiracy theories, "scientists are hiding it", etc.): evidence = "unscientific", score <= 2, \
verdict = "skip", and list those claims in questionable_claims.
Isolated exaggerations in an otherwise normal video also go to questionable_claims, but without the \
automatic "skip". "Studies show" without specifics is weak grounding, not strong.

## Verdict
- "watch": high value AND the video itself gives more than the summary (demonstrations, nuances, examples).
- "partial": only some fragments are valuable — name them with timestamps.
- "summary_enough": the content is useful, but the summary conveys almost all of it; no need to watch.
- "skip": ONLY for weak content: little of use, generalities, lots of filler, questionable or \
unscientific claims.

Answer with ONE JSON object only, no explanations and no ```, strictly following this schema:
{{
  "about": "one sentence: what the video is about",
  "key_points": ["5-15 detailed points: key ideas, facts, numbers, arguments"],
  "useful": ["what is new or practically useful (concretely)"],
  "recommendations_count": integer number of concrete applicable recommendations,
  "evidence": "high" | "medium" | "low" | "unscientific",
  "evidence_notes": "what the author relies on: research, data, experience, nothing",
  "questionable_claims": ["questionable or unscientific claims; [] if none"],
  "water_percent": integer 0-100, share of filler: intros, ads, repetition, digressions,
  "water_parts": "where exactly the filler is, with timestamps [mm:ss] if possible",
  "audience": "who this video is for",
  "verdict": "watch" | "partial" | "summary_enough" | "skip",
  "verdict_reason": "why this verdict; for \\"partial\\" name the fragments (timestamps)",
  "score": integer 1-5 on the scale above,
  "tags": ["3-7 short lowercase tags in {language}"]
}}"""

def _text_prompt(video_prompt: str) -> str:
    """Той самий промпт для книг, статей і документів: ті самі критерії, інші слова й поле book_title."""
    swaps = [
        ("summarizes YouTube videos so that a person can decide whether the video is worth watching",
         "summarizes books, articles and documents so that a person can decide whether they are worth reading"),
        ("regardless of the language of the video", "regardless of the language of the text"),
        ("useful content per minute", "useful content per page"),
        ("an average YouTube video = 3", "an average popular book or article = 3"),
        ("you won't find in a typical video. At most ~1 in 10 videos.",
         "you won't find in a typical book. At most ~1 in 10."),
        ("in an otherwise normal video", "in an otherwise normal text"),
        ('"watch": high value AND the video itself gives more than the summary (demonstrations, nuances, '
         'examples).',
         '"watch" (here it means: read it): high value AND the text itself gives much more than the summary '
         '(argumentation, examples, exercises).'),
        ("only some fragments are valuable — name them with timestamps.",
         "only some chapters or sections are valuable — name them."),
        ("no need to watch.", "no need to read."),
        ("what the video is about", "what this text or book is about"),
        ("with timestamps [mm:ss] if possible", "name chapters or pages [p. N] if possible"),
        ("who this video is for", "who this text is for"),
        ('for \\"partial\\" name the fragments (timestamps)', 'for \\"partial\\" name the chapters'),
    ]
    p = video_prompt
    for old, new in swaps:
        if old not in p:
            raise AssertionError(f"text prompt swap not found: {old}")
        p = p.replace(old, new)
    return p + """

For a book, key_points may be grouped by chapter ("Chapter N: ..."), up to 25 points if the book is dense.
Add one more JSON field "book_title": "Title — Author(s), year" in the original language, as seen in the text \
("" if unknown).
questionable_claims are only what the author CLAIMS, not topics or other people's views the author merely \
describes or discusses."""


TEXT_SYSTEM_PROMPT = _text_prompt(SYSTEM_PROMPT)

CHUNK_PROMPT = """You are reading one part of a long text (a book or a document) and making working notes \
for a final summary later. Write in {language}, as a bulleted list, without introductions:
- key ideas, facts, numbers, arguments (in detail, with chapter names if there are any);
- concrete practical recommendations;
- what the author relies on (research, data, experience);
- questionable or unscientific claims (mark them with "⚠️");
- the approximate share of filler in this part.
Do not invent anything: only what is in the text."""

RETRY_NOTE = ("\n\nYour previous answer could not be parsed ({error}). "
              "Answer again with exactly one valid JSON object following the schema.")

LABELS_PROMPT = """Translate the values of this JSON object (short labels for a note about a video) into \
{language}. Keep the keys unchanged. Answer with one JSON object only.
{labels}"""


def language_name(code: str) -> str:
    name = i18n.SUMMARY_LANGUAGES.get(code)
    return f"{name} (language code: {code})" if name else f"the language with code \"{code}\""


def system_prompt(language: str, kind: str = "video") -> str:
    template = SYSTEM_PROMPT if kind == "video" else TEXT_SYSTEM_PROMPT
    return template.format(language=language_name(language))


def chunk_prompt(language: str) -> str:
    return CHUNK_PROMPT.format(language=language_name(language))


def text_prompt(title: str, file_format: str, body: str, from_chunks: int = 0) -> str:
    head = f"Title: {title}\nFormat: {file_format}\n"
    if from_chunks:
        return head + (f"The text is long, so below are working notes from its {from_chunks} parts, in order. "
                       f"Make one final summary from them.\n\nNOTES OF THE PARTS:\n{body}")
    return head + f"\nTEXT:\n{body}"


def chunk_user_prompt(title: str, n: int, total: int, chunk: str) -> str:
    return f"Title: {title}\nPart {n} of {total}:\n\n{chunk}"


def user_prompt(title: str, channel: str, duration: str, transcript: str, transcript_lang: str,
                generated: bool, max_chars: int) -> str:
    truncated = len(transcript) > max_chars
    lines = [f"Title: {title}", f"Channel: {channel}"]
    if duration:
        lines.append(f"Duration: {duration}")
    lines.append(f"Subtitles: {transcript_lang}, {'automatic' if generated else 'manual'}")
    if truncated:
        lines.append("(The transcript was cut because of its length: the end of the video is not shown.)")
    return "\n".join(lines) + f"\n\nTRANSCRIPT:\n{transcript[:max_chars]}"


def parse_summary(text: str) -> dict[str, Any]:
    """Розбирає й нормалізує відповідь моделі. ValueError — відповідь непридатна (варто повторити)."""
    data = _first_object(text)

    out: dict[str, Any] = {
        "about": _str(data.get("about")),
        "key_points": _list(data.get("key_points")),
        "useful": _list(data.get("useful")),
        "recommendations_count": _int(data.get("recommendations_count"), 0, 1000, 0),
        "evidence": _code(data.get("evidence"), EVIDENCE, "medium"),
        "evidence_notes": _str(data.get("evidence_notes")),
        "questionable_claims": _list(data.get("questionable_claims")),
        "water_percent": _int(data.get("water_percent"), 0, 100, 0),
        "water_parts": _str(data.get("water_parts")),
        "audience": _str(data.get("audience")),
        "verdict": _code(data.get("verdict"), VERDICTS, "partial"),
        "verdict_reason": _str(data.get("verdict_reason")),
        "score": _int(data.get("score"), 1, 5, 3),
        "tags": [tg for tg in (_tag(x) for x in _list(data.get("tags"))) if tg][:7],
        "book_title": _str(data.get("book_title")),   # лише для текстів; у відео — порожньо
    }
    if out["evidence"] == "unscientific":   # правило «в топку» не залежить від ввічливості моделі
        out["score"] = min(out["score"], 2)
        out["verdict"] = "skip"
    if not out["about"] or not out["key_points"]:
        raise ValueError("no 'about' or 'key_points' in the answer")
    return out


def parse_labels(text: str, expected: list[str]) -> dict[str, str]:
    data = _first_object(text)
    labels = {k: _str(data.get(k)) for k in expected}
    missing = [k for k, v in labels.items() if not v]
    if missing:
        raise ValueError(f"missing labels: {missing}")
    return labels


def _first_object(text: str) -> dict[str, Any]:
    """Перший цілий JSON-об'єкт у відповіді: модель інколи додає ``` або текст до чи після нього."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in the answer")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")
    return data


def _str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"\n+", v) if x.strip()]
    if not isinstance(v, list):
        v = [v]
    return [str(x).strip().lstrip("-•* ").strip() for x in v if str(x).strip()]


def _int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(round(float(str(v).strip().rstrip("%"))))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _code(v: Any, allowed: tuple[str, ...], default: str) -> str:
    s = _str(v).lower().replace(" ", "_").replace("-", "_")
    return s if s in allowed else default


def _tag(v: str) -> str:
    # теги Obsidian: без пробілів і #, лише літери, цифри, - _ /. Звичайний апостроф у тегах заборонений,
    # тому — літерний «ʼ» (U+02BC), як радить український правопис: «здоровʼя», а не «здоровя»
    s = re.sub(r"\s+", "-", v.strip().lstrip("#").lower())
    s = re.sub("['’`]", "ʼ", s)
    return re.sub(r"[^\w\-/]", "", s)
