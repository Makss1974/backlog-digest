"""Конвеєр: оновлення списків джерел (sync) і обробка черги (process).

Ніколи не губимо прогрес: кожен елемент зберігається одразу, тож будь-яку зупинку
(Ctrl+C, вичерпаний ліміт моделей, блокування YouTube) можна просто продовжити наступним запуском.
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db, notes, summarize, youtube
from .i18n import t
from .llm import Chain, LLMError, QuotaExhausted, short
from .paths import Paths

YOUTUBE = "youtube"
SOURCE_TYPES = (YOUTUBE,)

Log = Callable[[str], None]


# ---------------------------------------------------------------- джерела

def add_youtube_source(conn: sqlite3.Connection, name: str, urls: list[str], folder: str = "") -> int:
    return db.add_source(conn, YOUTUBE, name, {"urls": urls}, folder or name)


def sync_source(conn: sqlite3.Connection, source: dict[str, Any], log: Log = print) -> tuple[int, int]:
    """Оновлює список елементів джерела. Повертає (нових, усього в джерелі)."""
    new = 0
    settings = source["settings"]
    for url in settings.get("urls", []):
        try:
            listing = youtube.list_url(url, settings.get("cookies_file") or None)
        except youtube.ListError as e:
            log(t("sync.list_error", url=url, detail=str(e)))
            continue
        list_name = listing.title or source["name"]
        added = 0
        for pos, entry in enumerate(listing.entries):
            if db.upsert_item(conn, source["id"], f"youtube:{entry.id}", list_name, kind="video",
                              title=entry.title, author=entry.channel, url=youtube.WATCH_URL.format(entry.id),
                              duration=entry.duration, position=pos):
                added += 1
        new += added
        log(t("sync.list_done", name=list_name, total=len(listing.entries), new=added))
    total = conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (source["id"],)).fetchone()[0]
    return new, int(total)


def sync_all(conn: sqlite3.Connection, log: Log = print) -> int:
    new = 0
    for source in db.list_sources(conn):
        if source["enabled"]:
            log(t("sync.source", name=source["name"]))
            new += sync_source(conn, source, log)[0]
    return new


# ---------------------------------------------------------------- обробка

@dataclass
class RunStats:
    done: int = 0
    reused: int = 0
    no_text: int = 0
    errors: int = 0
    tokens: int = 0
    stopped: str = ""   # чому зупинились раніше: blocked / quota / ""


class Processor:
    def __init__(self, conn: sqlite3.Connection, cfg: dict[str, Any], paths: Paths, *, log: Log = print,
                 chain: Chain | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.conn = conn
        self.cfg = cfg
        self.paths = paths
        self.log = log
        self.sleep = sleep
        self.chain = chain or Chain.from_config(cfg, paths, log=log)
        self.language = cfg["general"]["summary_language"]
        self.root = Path(cfg["output"]["path"]).expanduser()
        self.sources = {s["id"]: s for s in db.list_sources(conn)}
        self._labels: dict[str, str] | None = None
        self._fetched_before = False

    # -- підготовка

    def check_ready(self) -> bool:
        if not self.cfg["output"]["path"]:
            self.log(t("process.no_output"))
            return False
        if not self.chain.active():
            self.log(t("process.no_models") if not self.chain.slots else t("process.all_exhausted"))
            return False
        return True

    def labels(self) -> dict[str, str]:
        if self._labels is None:
            self._labels = notes.load_labels(self.paths, self.language, self._translate_labels)
        return self._labels

    def _translate_labels(self, english: dict[str, str]) -> dict[str, str]:
        prompt = summarize.LABELS_PROMPT.format(language=summarize.language_name(self.language),
                                                labels=json.dumps(english, ensure_ascii=False))
        res = self.chain.complete("You translate short UI labels.", prompt)
        return summarize.parse_labels(res.text, list(english))

    # -- головний цикл

    def run(self, limit: int, source_id: int | None = None) -> RunStats:
        stats = RunStats()
        if not self.check_ready():
            return stats
        left_today = self.cfg["limits"]["per_day"] - db.done_today(self.conn)
        if left_today <= 0:
            self.log(t("process.daily_limit", limit=self.cfg["limits"]["per_day"]))
            return stats
        rows = db.pending(self.conn, min(limit, left_today), source_id,
                          newest_first=bool(self.cfg["youtube"]["newest_first"]))
        if not rows:
            self.log(t("process.nothing"))
            return stats
        self.log(t("process.start", n=len(rows), models=", ".join(self.chain.active()[:3])))
        for i, row in enumerate(rows, 1):
            item = dict(row)
            self.log(f"\n[{i}/{len(rows)}] {item['title']}  ({item['list_name']})")
            try:
                self._process(item, stats)
            except youtube.BlockedError as e:
                self.log(t("process.blocked", detail=str(e)))
                stats.stopped = "blocked"
                break
            except QuotaExhausted:
                self.log(t("process.quota"))
                stats.stopped = "quota"
                break
        self.log("\n" + t("process.summary", done=stats.done, reused=stats.reused, no_text=stats.no_text,
                                  errors=stats.errors, tokens=stats.tokens))
        return stats

    def _process(self, item: dict[str, Any], stats: RunStats) -> None:
        saved = db.get_result(self.conn, item["key"], self.language)
        if saved:   # той самий елемент уже конспектовано в іншому списку чи джерелі — без моделі
            self._write(item, json.loads(saved["summary_json"]))
            stats.reused += 1
            self.log("  " + t("process.reused"))
            return

        if item["kind"] != "video":
            self._error(item, stats, t("process.unsupported", kind=item["kind"]))
            return
        vid = item["key"].removeprefix("youtube:")
        self._pause()
        try:
            tr = youtube.fetch_transcript(vid, self._transcript_languages())
        except youtube.BlockedError:
            raise
        except Exception as e:  # будь-яка інша помилка субтитрів — це помилка елемента, не всього запуску
            self._error(item, stats, t("process.transcript_error", detail=short(str(e), 200)))
            return
        if tr is None:
            db.mark_no_text(self.conn, item["id"], "no subtitles")
            stats.no_text += 1
            self.log("  " + t("process.no_subtitles"))
            return
        self.log("  " + t("process.subtitles", lang=tr.language, chars=len(tr.text),
                          auto=t("process.auto") if tr.generated else ""))

        system = summarize.system_prompt(self.language)
        user = summarize.user_prompt(item["title"] or "", item["author"] or "",
                                     youtube.format_duration(item["duration"]), tr.text, tr.language,
                                     tr.generated, self.cfg["models"]["max_chars"])
        deep = self._deep(len(tr.text))
        summary = None
        error = ""
        for _attempt in range(2):   # зламаний JSON — ще одна спроба з поясненням
            try:
                res = self.chain.complete(system, user + (summarize.RETRY_NOTE.format(error=error) if error else ""),
                                          deep=deep)
            except QuotaExhausted:
                raise
            except LLMError as e:
                self._error(item, stats, t("process.model_error", detail=short(str(e), 200)))
                return
            stats.tokens += res.tokens_in + res.tokens_out
            try:
                summary = summarize.parse_summary(res.text)
                break
            except ValueError as e:
                error = short(str(e), 120)
                self.log("  " + t("process.bad_answer", model=res.model, detail=error))
        if summary is None:
            self._error(item, stats, t("process.bad_answer_final", detail=error))
            return
        db.save_result(self.conn, item["key"], self.language, summary, source_language=tr.language,
                       model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
        path = self._write(item, summary)
        stats.done += 1
        self.log("  " + t("process.done", score=summary["score"],
                          verdict=self.labels()[f"verdict_{summary['verdict']}"], model=res.model))
        self.log(f"  → {path}")

    def _write(self, item: dict[str, Any], summary: dict[str, Any]) -> Path:
        source = self.sources.get(item["source_id"]) or {}
        directory = notes.note_dir(self.root, source.get("output_dir") or source.get("name") or "",
                                   item["list_name"])
        path = Path(item["note_path"]) if item.get("note_path") else notes.note_path(
            directory, item["title"] or "", item["key"].split(":")[-1],
            lambda p: db.note_path_taken(self.conn, str(p), item["id"]))
        content = notes.render_video_note(item, summary, self.labels(), language=self.language,
                                          processed=db.now(), duration=youtube.format_duration(item["duration"]))
        notes.write_note(path, content)
        db.mark_done(self.conn, item["id"], str(path))
        return path

    def _error(self, item: dict[str, Any], stats: RunStats, message: str) -> None:
        status = db.mark_error(self.conn, item["id"], message)
        stats.errors += 1
        self.log("  ✗ " + message + (" " + t("process.failed_now") if status == db.FAILED else ""))

    def _pause(self) -> None:
        """Пауза між запитами субтитрів: YouTube блокує IP за часті запити."""
        if self._fetched_before:
            yt = self.cfg["youtube"]
            delay = random.uniform(float(yt["delay_min"]), float(yt["delay_max"]))
            if delay > 0:
                self.log("  " + t("process.pause", seconds=round(delay)))
                self.sleep(delay)
        self._fetched_before = True

    def _transcript_languages(self) -> list[str]:
        """Бажані мови субтитрів: з налаштувань, інакше англійська (дешевше за токенами), мова конспекту
        й мова інтерфейсу."""
        langs = list(self.cfg["youtube"]["languages"]) or ["en", self.language,
                                                            self.cfg["general"]["ui_language"]]
        return list(dict.fromkeys(code.split("-")[0] for code in langs))

    def _deep(self, length: int) -> bool:
        mode = self.cfg["models"]["thinking"]
        return mode == "deep" or (mode == "auto" and length > 60000)


def queue_eta_days(conn: sqlite3.Connection, per_day: int) -> int:
    queued = sum(db.status_counts(conn)[s] for s in db.QUEUED)
    return -(-queued // per_day) if queued else 0

