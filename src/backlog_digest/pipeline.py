"""Конвеєр: оновлення списків джерел (sync) і обробка черги: збір текстів (fetch) і конспекти (summarize).

Ніколи не губимо прогрес: кожен елемент зберігається одразу, тож будь-яку зупинку
(Ctrl+C, вичерпаний ліміт моделей, блокування YouTube) можна просто продовжити наступним запуском.
"""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db, folders, notes, readers, summarize, youtube
from .i18n import t
from .llm import Chain, LLMError, QuotaExhausted, short
from .paths import Paths

ALL, FETCH, SUMMARIZE = db.ALL, db.FETCH, db.SUMMARIZE

YOUTUBE = "youtube"
FOLDER = "folder"
SOURCE_TYPES = (YOUTUBE, FOLDER)

Log = Callable[[str], None]


# ---------------------------------------------------------------- джерела

def add_youtube_source(conn: sqlite3.Connection, name: str, urls: list[str], folder: str = "") -> int:
    return db.add_source(conn, YOUTUBE, name, {"urls": urls}, folder or name)


def add_folder_source(conn: sqlite3.Connection, name: str, path: str, folder: str = "") -> int:
    return db.add_source(conn, FOLDER, name, {"path": path}, folder or name)


def sync_source(conn: sqlite3.Connection, source: dict[str, Any], log: Log = print) -> tuple[int, int]:
    """Оновлює список елементів джерела. Повертає (нових, усього в джерелі)."""
    new = _sync_folder(conn, source, log) if source["type"] == FOLDER else _sync_youtube(conn, source, log)
    total = conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (source["id"],)).fetchone()[0]
    return new, int(total)


def _sync_folder(conn: sqlite3.Connection, source: dict[str, Any], log: Log) -> int:
    root = Path(source["settings"].get("path", ""))
    if not root.is_dir():
        log(t("sync.folder_missing", path=str(root)))
        return 0
    added: dict[str, int] = {}
    total: dict[str, int] = {}
    for pos, entry in enumerate(folders.scan(root)):
        total[entry.list_name] = total.get(entry.list_name, 0) + 1
        if db.upsert_item(conn, source["id"], entry.key, entry.list_name, kind="text", title=entry.title,
                          author=entry.format, path=str(entry.path), position=pos):
            added[entry.list_name] = added.get(entry.list_name, 0) + 1
    for name, n in total.items():
        log(t("sync.list_done", name=name or source["name"], total=n, new=added.get(name, 0)))
    if not total:
        log(t("sync.folder_empty", path=str(root)))
    return sum(added.values())


def _sync_youtube(conn: sqlite3.Connection, source: dict[str, Any], log: Log) -> int:
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
    return new


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
    fetched: int = 0    # зібрано текстів
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
        self._fetching = self._summarizing = True
        self._fetch_left = 0

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

    def run(self, limit: int, source_id: int | None = None, stage: str = ALL) -> RunStats:
        """Обробка черги. stage: fetch — лише зібрати тексти (повільно, з вашої IP, моделі не потрібні);
        summarize — лише конспекти з уже зібраного (YouTube не потрібен); all — обидва кроки поспіль.
        У режимі all блокування YouTube не зупиняє конспекти з уже зібраного, а вичерпані моделі — збір."""
        stats = RunStats()
        self._fetching = stage != SUMMARIZE
        self._summarizing = stage != FETCH
        if self._summarizing and not self.check_ready():
            return stats
        per_day = self.cfg["limits"]["per_day"]
        self._fetch_left = per_day - db.fetched_today(self.conn)   # добовий ліміт — лише на запити до YouTube
        if self._fetching and self._fetch_left <= 0:
            self.log(t("process.daily_limit", limit=per_day))
            if stage == FETCH:
                return stats
            self._fetching = False
            stage = SUMMARIZE
        rows = db.pending(self.conn, min(limit, self._fetch_left) if stage == FETCH else limit, source_id,
                          newest_first=bool(self.cfg["youtube"]["newest_first"]), stage=stage,
                          language=self.language)
        if not rows:
            self.log(t("process.nothing"))
            return stats
        self.log(t("process.start_fetch", n=len(rows)) if stage == FETCH else
                 t("process.start", n=len(rows), models=", ".join(self.chain.active()[:3])))
        limit_logged = False
        for i, row in enumerate(rows, 1):
            if not (self._fetching or self._summarizing):
                break
            item = dict(row)
            text = db.get_transcript(self.conn, item["key"])
            saved = db.get_result(self.conn, item["key"], self.language) if self._summarizing else None
            needs_fetch = item["kind"] == "video" and text is None and saved is None
            if needs_fetch and not self._fetching:   # тексту ще немає, а звертатися до YouTube зараз не можна
                if self._fetch_left <= 0 and not limit_logged:
                    self.log("\n" + t("process.daily_limit", limit=per_day))
                    limit_logged = True
                continue
            if not needs_fetch and not self._summarizing:   # той самий ролик в іншому списку — уже зібрано
                continue
            self.log(f"\n[{i}/{len(rows)}] {item['title']}" + (f"  ({item['list_name']})" if item["list_name"] else ""))
            try:
                self._process(item, text, saved, stats)
            except youtube.BlockedError as e:
                self.log(t("process.blocked", detail=str(e)))
                stats.stopped = "blocked"
                self._fetching = False
            except QuotaExhausted:
                self.log(t("process.quota"))
                stats.stopped = "quota"
                self._summarizing = False
        if stage == FETCH:
            self.log("\n" + t("process.fetch_summary", fetched=stats.fetched, no_text=stats.no_text,
                                      errors=stats.errors))
        else:
            self.log("\n" + t("process.summary", done=stats.done, reused=stats.reused, no_text=stats.no_text,
                                      errors=stats.errors, tokens=stats.tokens))
            if stats.fetched:
                self.log(t("process.fetched_line", n=stats.fetched))
        return stats

    def _process(self, item: dict[str, Any], text: sqlite3.Row | None, saved: sqlite3.Row | None,
                 stats: RunStats) -> None:
        if saved:   # той самий елемент уже конспектовано в іншому списку чи джерелі — без моделі
            self._write(item, json.loads(saved["summary_json"]))
            stats.reused += 1
            self.log("  " + t("process.reused"))
            return

        if item["kind"] == "text":
            if self._summarizing:
                self._summarize_text(item, stats)
            return
        if item["kind"] != "video":
            self._error(item, stats, t("process.unsupported", kind=item["kind"]))
            return
        if text is None:
            tr = self._fetch(item, stats)
            if tr is None:
                return
        elif text["text"] is None:   # уже з'ясовано, що субтитрів немає (те саме відео в іншому списку)
            self._no_text(item, stats)
            return
        else:
            tr = youtube.Transcript(text["text"], text["language"] or "", bool(text["generated"]))
            self.log("  " + t("process.stored", lang=tr.language, chars=len(tr.text)))
        if self._summarizing:
            self._summarize(item, tr, stats)

    def _fetch(self, item: dict[str, Any], stats: RunStats) -> youtube.Transcript | None:
        """Збирає субтитри й одразу зберігає їх у базі. None — тексту немає або помилка (вже враховано)."""
        self._pause()
        try:
            tr = youtube.fetch_transcript(item["key"].removeprefix("youtube:"), self._transcript_languages())
        except youtube.BlockedError:
            raise
        except Exception as e:  # будь-яка інша помилка субтитрів — це помилка елемента, не всього запуску
            self._count_fetch()
            self._error(item, stats, t("process.transcript_error", detail=short(str(e), 200)))
            return None
        self._count_fetch()
        if tr is None:
            db.save_transcript(self.conn, item["key"], None)
            self._no_text(item, stats)
            return None
        db.save_transcript(self.conn, item["key"], tr.text, tr.language, tr.generated)
        stats.fetched += 1
        self.log("  " + t("process.subtitles", lang=tr.language, chars=len(tr.text),
                          auto=t("process.auto") if tr.generated else ""))
        return tr

    def _count_fetch(self) -> None:
        """Кожен запит до YouTube іде в добовий ліміт — і вдалий, і «субтитрів немає», і з помилкою."""
        db.note_fetch(self.conn)
        self._fetch_left -= 1
        if self._fetch_left <= 0:
            self._fetching = False

    def _no_text(self, item: dict[str, Any], stats: RunStats) -> None:
        db.mark_no_text(self.conn, item["id"], "no subtitles")
        stats.no_text += 1
        self.log("  " + t("process.no_subtitles"))

    def _summarize(self, item: dict[str, Any], tr: youtube.Transcript, stats: RunStats) -> None:
        user = summarize.user_prompt(item["title"] or "", item["author"] or "",
                                     youtube.format_duration(item["duration"]), tr.text, tr.language,
                                     tr.generated, self.cfg["models"]["max_chars"])
        if self._finish(item, summarize.system_prompt(self.language), user, self._deep(len(tr.text)),
                        tr.language, stats):
            db.delete_transcript(self.conn, item["key"])   # конспект є — субтитри більше не потрібні

    def _summarize_text(self, item: dict[str, Any], stats: RunStats) -> None:
        """Книга чи документ: читаємо файл; довгий текст — частинами (нотатки частин кешуються на диску,
        тож після зупинки книга продовжиться, а не почнеться заново), потім один підсумковий конспект."""
        try:
            text = readers.extract_text(Path(item["path"] or ""))
        except readers.NoText as e:
            db.mark_no_text(self.conn, item["id"], str(e))
            stats.no_text += 1
            self.log("  — " + str(e))
            return
        except (readers.ReadError, OSError) as e:
            self._error(item, stats, t("process.read_error", detail=short(str(e), 200)))
            return
        chunks = readers.split_chunks(text, self.cfg["models"]["max_chars"])
        self.log("  " + t("process.text", chars=len(text), format=item["author"] or "")
                 + (" · " + t("process.parts", n=len(chunks)) if len(chunks) > 1 else ""))
        deep = self._deep(len(text))
        title, file_format = item["title"] or "", item["author"] or ""
        cache = self.paths.cache_dir / "chunks" / item["key"].replace(":", "_")
        if len(chunks) == 1:
            user = summarize.text_prompt(title, file_format, text)
        else:
            parts = self._chunk_notes(item, chunks, cache, deep, stats)
            if parts is None:
                return
            body = "\n\n".join(f"### Part {n}\n{note}" for n, note in enumerate(parts, 1))
            user = summarize.text_prompt(title, file_format, body, from_chunks=len(chunks))
        if self._finish(item, summarize.system_prompt(self.language, "text"), user, deep, None, stats):
            shutil.rmtree(cache, ignore_errors=True)   # конспект є — нотатки частин більше не потрібні

    def _chunk_notes(self, item: dict[str, Any], chunks: list[str], cache: Path, deep: bool,
                     stats: RunStats) -> list[str] | None:
        out = []
        total = len(chunks)
        for n, chunk in enumerate(chunks, 1):
            cached = cache / f"{self.language}_{total}_{n:03d}.md"
            if cached.exists():
                out.append(cached.read_text(encoding="utf-8"))
                self.log("  " + t("process.part_cached", n=n, total=total))
                continue
            self.log("  " + t("process.part", n=n, total=total))
            try:
                res = self.chain.complete(summarize.chunk_prompt(self.language),
                                          summarize.chunk_user_prompt(item["title"] or "", n, total, chunk),
                                          deep=deep, json_out=False)
            except QuotaExhausted:
                raise
            except LLMError as e:
                self._error(item, stats, t("process.model_error", detail=short(str(e), 200)))
                return None
            stats.tokens += res.tokens_in + res.tokens_out
            note = res.text.strip()
            cache.mkdir(parents=True, exist_ok=True)
            cached.write_text(note, encoding="utf-8")
            out.append(note)
        return out

    def _finish(self, item: dict[str, Any], system: str, user: str, deep: bool, source_language: str | None,
                stats: RunStats) -> bool:
        """Підсумковий конспект: запит до моделі, перевірка відповіді, запис нотатки. True — готово."""
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
                return False
            stats.tokens += res.tokens_in + res.tokens_out
            try:
                summary = summarize.parse_summary(res.text)
                break
            except ValueError as e:
                error = short(str(e), 120)
                self.log("  " + t("process.bad_answer", model=res.model, detail=error))
        if summary is None:
            self._error(item, stats, t("process.bad_answer_final", detail=error))
            return False
        db.save_result(self.conn, item["key"], self.language, summary, source_language=source_language,
                       model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
        path = self._write(item, summary)
        stats.done += 1
        self.log("  " + t("process.done", score=summary["score"],
                          verdict=notes.verdict_label(self.labels(), item["kind"], summary["verdict"]),
                          model=res.model))
        self.log(f"  → {path}")
        return True

    def _write(self, item: dict[str, Any], summary: dict[str, Any]) -> Path:
        source = self.sources.get(item["source_id"]) or {}
        directory = notes.note_dir(self.root, source.get("output_dir") or source.get("name") or "",
                                   item["list_name"])
        path = Path(item["note_path"]) if item.get("note_path") else notes.note_path(
            directory, item["title"] or "", item["key"].split(":")[-1],
            lambda p: db.note_path_taken(self.conn, str(p), item["id"]))
        content = notes.render_note(item, summary, self.labels(), language=self.language,
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


def queue_eta_days(conn: sqlite3.Connection, per_day: int, language: str) -> int:
    """Скільки днів ще збирати тексти при добовому ліміті (конспекти швидші — їх не рахуємо)."""
    to_fetch = db.stage_counts(conn, language)[0]
    return -(-to_fetch // per_day) if to_fetch else 0

