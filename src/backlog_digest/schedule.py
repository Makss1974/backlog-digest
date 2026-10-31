"""Розклад (етап 5): завдання «що й коли робити» і їх виконання командою `bdig run-jobs`.

Системний планувальник (autostart.py) запускає `run-jobs` щогодини й після входу в систему, а вже тут
вирішуємо, чи настав час: «не раніше ГГ:ХХ», раз на день чи на тиждень. Комп'ютер був вимкнений —
завдання виконається при першому запуску після свого часу (наздоганяння).
Паралельно не запускаємось: якщо обробка вже йде (у меню чи попередній запуск), чекаємо наступного разу.
Журнал — файл у теці даних (logs/) і коротка нотатка в сховищі.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import db, notes, runstate
from .i18n import t
from .paths import Paths

STEPS = ("sync", "process", "report", "raindrop")
DEFAULT_STEPS = {"sync": True, "process": True, "report": False, "raindrop": False}
JOURNAL_ROWS = 200

Log = Callable[[str], None]


# ---------------------------------------------------------------- завдання

def list_jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_job(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id")]


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job(row) if row else None


def add_job(conn: sqlite3.Connection, name: str, *, source_id: int | None = None, list_name: str | None = None,
            lim: int = 30, steps: dict[str, bool] | None = None, frequency: str = "daily",
            at_time: str = "09:00") -> int:
    cur = conn.execute(
        "INSERT INTO jobs (name, source_id, list_name, lim, steps, frequency, at_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, source_id, list_name, lim, json.dumps({**DEFAULT_STEPS, **(steps or {})}), frequency, at_time))
    conn.commit()
    return int(cur.lastrowid or 0)


def update_job(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    if "steps" in fields:
        fields["steps"] = json.dumps(fields["steps"])
    conn.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), job_id))
    conn.commit()


def delete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()


def _job(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["steps"] = {**DEFAULT_STEPS, **json.loads(d["steps"])}
    return d


def parse_time(text: str) -> str | None:
    """«9», «9:5», «09:05» → «09:05»; неправильне — None."""
    parts = text.strip().replace(".", ":").split(":")
    if not 1 <= len(parts) <= 2 or not all(p.isdigit() for p in parts):
        return None
    h, m = int(parts[0]), int(parts[1]) if len(parts) == 2 else 0
    return f"{h:02d}:{m:02d}" if 0 <= h < 24 and 0 <= m < 60 else None


def is_due(job: dict[str, Any], now: datetime | None = None) -> bool:
    """Час запускати: настав at_time і в цей період (день / тиждень) ще не запускали."""
    now = now or datetime.now()
    if not job["enabled"]:
        return False
    h, m = (int(x) for x in job["at_time"].split(":"))
    last = datetime.fromisoformat(job["last_run"]) if job["last_run"] else None
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)   # початок поточного періоду
    day = str(job["frequency"])[7:] if str(job["frequency"]).startswith("weekly") else None
    if day == "":     # раз на тиждень, будь-який день: щонайменше через 6½ доби після минулого запуску
        return now >= start and (last is None or now - last >= timedelta(days=6, hours=12))
    if day is not None:
        start -= timedelta(days=(now.weekday() - int(day)) % 7)
    if start > now:
        start -= timedelta(days=7 if day is not None else 1)
    if last is None:
        # нове завдання: не запускаємо «за минулий період» — лише коли час настав сьогодні
        return start.date() == now.date()
    return last < start


def describe(conn: sqlite3.Connection, job: dict[str, Any]) -> str:
    source = db.get_source(conn, job["source_id"]) if job["source_id"] else None
    scope = f"{source['name'] if source else t('schedule.all_sources')} / {job['list_name'] or t('schedule.all_lists')}"
    freq = str(job["frequency"])
    if freq.startswith("weekly"):
        day = freq[7:]
        when = t("schedule.weekly", day=t(f"schedule.day_{day}") if day.isdigit() else t("schedule.any_day"))
    else:
        when = t("schedule.daily")
    steps = " → ".join(t(f"schedule.step_{s}") for s in STEPS if job["steps"].get(s))
    mark = "✅" if job["enabled"] else "⏸"
    return (f"{mark} {job['name']}: {scope} · {t('schedule.up_to', n=job['lim'])} · {when} "
            f"{t('schedule.after', time=job['at_time'])}\n       {steps}")


# ---------------------------------------------------------------- виконання

class Runner:
    """Виконує кроки завдань. Кроки дістають залежності тут, щоб тести могли їх підмінити."""

    def __init__(self, conn: sqlite3.Connection, cfg: dict[str, Any], paths: Paths, log: Log) -> None:
        self.conn = conn
        self.cfg = cfg
        self.paths = paths
        self.log = log

    def scope_sources(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        return [s for s in db.list_sources(self.conn) if job["source_id"] in (None, s["id"])]

    def lists(self, job: dict[str, Any]) -> list[tuple[int, str]]:
        language = self.cfg["general"]["summary_language"]
        return [(int(r["source_id"]), str(r["list_name"])) for r in db.done_lists(self.conn, language)
                if job["source_id"] in (None, r["source_id"]) and job["list_name"] in (None, r["list_name"])]

    def done_count(self, job: dict[str, Any]) -> int:
        sql, args = "SELECT COUNT(*) FROM items WHERE status='done'", []
        if job["source_id"]:
            sql += " AND source_id=?"
            args.append(job["source_id"])
        if job["list_name"] is not None:
            sql += " AND list_name=?"
            args.append(job["list_name"])
        return int(self.conn.execute(sql, args).fetchone()[0])

    def queued(self, job: dict[str, Any]) -> int:
        sql, args = "SELECT COUNT(*) FROM items WHERE status IN (?, ?)", list(db.QUEUED)
        if job["source_id"]:
            sql += " AND source_id=?"
            args.append(job["source_id"])
        if job["list_name"] is not None:
            sql += " AND list_name=?"
            args.append(job["list_name"])
        return int(self.conn.execute(sql, args).fetchone()[0])

    def run_job(self, job: dict[str, Any]) -> str:
        from . import pipeline

        steps = job["steps"]
        if job["source_id"] and not db.get_source(self.conn, job["source_id"]):
            return t("schedule.source_gone")
        before = self.done_count(job)
        notes_: list[str] = []
        if steps.get("sync"):
            self.log(f"\n--- {t('schedule.step_sync')}")
            for source in self.scope_sources(job):
                pipeline.sync_source(self.conn, source, self.log)
        stopped = ""
        if steps.get("process"):
            self.log(f"\n--- {t('schedule.step_process')}")
            stats = pipeline.Processor(self.conn, self.cfg, self.paths, log=self.log).run(
                int(job["lim"]), job["source_id"], db.ALL, job["list_name"])
            stopped = stats.stopped or ""
        done = self.done_count(job) - before
        notes_.append(t("schedule.new_done", n=done))
        if steps.get("report") and done:
            notes_ += self.report(job)
        if steps.get("raindrop"):
            notes_ += self.raindrop(job)
        left = self.queued(job)
        notes_.append(t("schedule.left", n=left))
        if stopped:
            notes_.append(t(f"schedule.stopped_{stopped}"))
        return ", ".join(notes_)

    def report(self, job: dict[str, Any]) -> list[str]:
        from . import library, pipeline
        from .llm import LLMError

        self.log(f"\n--- {t('schedule.step_report')}")
        proc = pipeline.Processor(self.conn, self.cfg, self.paths, log=self.log)
        if not proc.chain.active():
            return [t("schedule.report_failed")]
        for source_id, list_name in self.lists(job):
            try:
                library.make_report(self.conn, proc.chain, source_id, list_name, proc.language)
                proc.write_index(source_id, list_name)
            except (LLMError, ValueError, OSError) as e:
                self.log(f"  ! {str(e)[:200]}")
                return [t("schedule.report_failed")]
        return []

    def raindrop(self, job: dict[str, Any]) -> list[str]:
        from . import export, keys, raindrop

        self.log(f"\n--- {t('schedule.step_raindrop')}")
        token = keys.get_secret(raindrop.TOKEN, self.paths)
        if not token:
            self.log(t("raindrop.no_token"))
            return [t("schedule.raindrop_failed")]
        language = self.cfg["general"]["summary_language"]
        root = self.cfg["raindrop"]["export_root"] or export.DEFAULT_ROOT
        try:
            stats = export.export(self.conn, token, self.lists(job), root, language,
                                  notes.load_labels(self.paths, language), log=self.log)
        except raindrop.RaindropError as e:
            self.log(f"  ! {e}")
            return [t("schedule.raindrop_failed")]
        return [t("schedule.raindrop_done", n=stats.created + stats.updated)] if stats.created or stats.updated else []


def run_due(conn: sqlite3.Connection, cfg: dict[str, Any], paths: Paths, *, force: bool = False,
            only: list[int] | None = None, log: Log = print) -> list[tuple[str, str]]:
    """Завдання, яким настав час (або вибрані / усі з force). Повертає [(назва, підсумок)]."""
    busy = runstate.current(paths)
    if busy:
        log(t("schedule.busy", since=str(busy.get("started", ""))[11:16]))
        return []
    jobs = [j for j in list_jobs(conn)
            if (j["id"] in only if only else (force and j["enabled"]) or is_due(j))]
    if not jobs:
        log(t("schedule.nothing_due"))
        return []
    log_file = paths.data_dir / "logs" / f"schedule-{datetime.now():%Y-%m-%d}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    out: list[tuple[str, str]] = []
    with open(log_file, "a", encoding="utf-8") as fh:
        def both(text: str) -> None:
            with contextlib.suppress(OSError, ValueError):   # фоновий запуск без консолі
                log(text)
            fh.write(text + "\n")
            fh.flush()

        runstate.start(paths, "schedule", db.now())
        try:
            for job in jobs:
                started = datetime.now()
                both(f"\n===== {started:%Y-%m-%d %H:%M} · {job['name']} =====")
                try:
                    result = Runner(conn, cfg, paths, both).run_job(job)
                except Exception as e:  # noqa: BLE001 — одне завдання не зупиняє інші
                    result = t("schedule.error", detail=str(e)[:300])
                both(f"===== {result}")
                update_job(conn, job["id"], last_run=started.isoformat(timespec="seconds"), last_result=result)
                journal(cfg, paths, job["name"], started, result)
                out.append((job["name"], result))
        finally:
            runstate.finish(paths)
    log(t("schedule.log_file", path=str(log_file)))
    return out


def journal_path(cfg: dict[str, Any], labels: dict[str, str]) -> Path:
    return Path(cfg["output"]["path"]).expanduser() / f"_{labels['journal']}.md"


def journal(cfg: dict[str, Any], paths: Paths, name: str, started: datetime, result: str) -> None:
    """Нотатка-журнал у сховищі: найновіші запуски зверху."""
    if not cfg["output"]["path"]:
        return
    labels = notes.load_labels(paths, cfg["general"]["summary_language"])
    path = journal_path(cfg, labels)
    head = (f"# {labels['journal']}\n\n| {labels['col_when']} | {labels['col_job']} | {labels['col_result']} |\n"
            "|---|---|---|")
    line = f"| {started:%Y-%m-%d %H:%M} | {name.replace('|', '/')} | {result.replace('|', '/')} |"
    rows: list[str] = []
    try:
        if path.exists():
            rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("| 2")]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([head, line, *rows[:JOURNAL_ROWS - 1]]) + "\n", encoding="utf-8")
    except OSError:
        pass      # журнал у сховищі — зручність; файл у logs/ уже записано
