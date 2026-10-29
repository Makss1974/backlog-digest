"""SQLite-база: джерела, елементи з їхніми статусами, конспекти.

Модель даних (спільна для всіх типів джерел):
  sources  — джерело: YouTube, папка, веб-статті, Raindrop, Telegram...
  items    — елемент джерела у конкретному «списку» (плейлист / підпапка / канал / колекція)
  transcripts — зібраний текст (субтитри) за ключем елемента; збирається окремим кроком
  results  — конспект; один на ключ елемента, тож те саме відео з YouTube і з Telegram
             (однаковий ключ, напр. youtube:<id>) конспектується один раз

Статуси: new -> done / no_text / error (повтор) -> failed (після MAX_ATTEMPTS невдач поспіль).
Повторний запуск бере лише new і error.

Міграції: номер схеми — у PRAGMA user_version; кожна міграція — окремий крок у MIGRATIONS.
Наявні кроки не змінювати, лише дописувати нові в кінець.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

NEW, DONE, NO_TEXT, ERROR, FAILED = "new", "done", "no_text", "error", "failed"
STATUSES = (NEW, DONE, NO_TEXT, ERROR, FAILED)
QUEUED = (NEW, ERROR)
MAX_ATTEMPTS = 3

MIGRATIONS: list[str] = [
    # 1 — початкова схема
    """
    CREATE TABLE sources (
        id         INTEGER PRIMARY KEY,
        type       TEXT NOT NULL,                 -- youtube / folder / web / raindrop / telegram ...
        name       TEXT NOT NULL UNIQUE,
        settings   TEXT NOT NULL DEFAULT '{}',    -- json: плейлисти, шлях до папки тощо
        output_dir TEXT NOT NULL DEFAULT '',      -- тека для конспектів усередині сховища
        enabled    INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE items (
        id           INTEGER PRIMARY KEY,
        source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        list_name    TEXT NOT NULL DEFAULT '',    -- плейлист / підпапка / канал / колекція
        key          TEXT NOT NULL,               -- глобальний ключ: youtube:<id>, file:<sha1>, url:<...>
        kind         TEXT NOT NULL DEFAULT 'text',-- video / text / audio
        title        TEXT,
        author       TEXT,                        -- канал / автор
        url          TEXT,
        path         TEXT,                        -- локальний файл
        duration     INTEGER,                     -- секунди
        position     INTEGER,                     -- порядок у списку
        status       TEXT NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new', 'done', 'no_text', 'error', 'failed')),
        attempts     INTEGER NOT NULL DEFAULT 0,  -- невдалих спроб поспіль
        error        TEXT,
        added_at     TEXT NOT NULL,
        processed_at TEXT,
        UNIQUE (source_id, list_name, key)
    );
    CREATE INDEX idx_items_status ON items(status);
    CREATE INDEX idx_items_key ON items(key);

    CREATE TABLE results (
        key          TEXT PRIMARY KEY,
        language     TEXT,
        model        TEXT,
        tokens_in    INTEGER,
        tokens_out   INTEGER,
        score        INTEGER,
        verdict      TEXT,
        summary_json TEXT,
        file_path    TEXT,
        created_at   TEXT NOT NULL
    );
    """,
    # 2 — конспект залежить і від мови конспекту; шлях нотатки зберігається в елемента
    #     (той самий конспект у двох списках — дві нотатки). На етапі 1 results ще не заповнювалась.
    """
    DROP TABLE results;
    CREATE TABLE results (
        key             TEXT NOT NULL,
        language        TEXT NOT NULL,            -- мова конспекту
        source_language TEXT,                     -- мова субтитрів / тексту
        model           TEXT,
        tokens_in       INTEGER,
        tokens_out      INTEGER,
        score           INTEGER,
        verdict         TEXT,                     -- watch / partial / summary_enough / skip
        summary_json    TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        PRIMARY KEY (key, language)
    );
    ALTER TABLE items ADD COLUMN note_path TEXT;
    """,
    # 3 — тексти (субтитри) зберігаються окремо від конспектів: збирати їх і конспектувати можна
    #     в різний час (YouTube обмежує запити з IP, моделі — денні ліміти)
    """
    CREATE TABLE transcripts (
        key        TEXT PRIMARY KEY,
        language   TEXT,
        generated  INTEGER NOT NULL DEFAULT 0,    -- 1 — автоматичні субтитри
        text       TEXT,                          -- NULL — тексту немає (немає субтитрів)
        fetched_at TEXT NOT NULL
    );
    """,
]
SCHEMA_VERSION = len(MIGRATIONS)


class DbError(Exception):
    pass


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Доводить схему до SCHEMA_VERSION. Кожен крок — в окремій транзакції. Повертає к-сть кроків."""
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        # базу створила новіша версія програми — краще не чіпати
        raise DbError(f"database schema v{current} is newer than supported v{SCHEMA_VERSION}")
    for version in range(current + 1, SCHEMA_VERSION + 1):
        script = MIGRATIONS[version - 1]
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            conn.rollback()
            raise
    return SCHEMA_VERSION - current


# ---------------------------------------------------------------- джерела

def add_source(conn: sqlite3.Connection, type_: str, name: str, settings: dict[str, Any] | None = None,
               output_dir: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO sources (type, name, settings, output_dir, created_at) VALUES (?, ?, ?, ?, ?)",
        (type_, name, json.dumps(settings or {}, ensure_ascii=False), output_dir, now()),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM sources ORDER BY id"):
        d = dict(r)
        d["settings"] = json.loads(d["settings"] or "{}")
        out.append(d)
    return out


def get_source(conn: sqlite3.Connection, source_id: int) -> dict[str, Any] | None:
    return next((s for s in list_sources(conn) if s["id"] == source_id), None)


def update_source(conn: sqlite3.Connection, source_id: int, **fields: Any) -> None:
    if "settings" in fields:
        fields["settings"] = json.dumps(fields["settings"], ensure_ascii=False)
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE sources SET {assignments} WHERE id=?", (*fields.values(), source_id))
    conn.commit()


def delete_source(conn: sqlite3.Connection, source_id: int) -> int:
    """Видаляє джерело з його елементами (файли конспектів не чіпає). Повертає к-сть елементів."""
    n = conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (source_id,)).fetchone()[0]
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    conn.commit()
    return int(n)


def queued_by_source(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Джерела, у яких є що обробляти: id, name, type, queued."""
    return conn.execute(
        "SELECT s.id, s.name, s.type, COUNT(i.id) AS queued FROM sources s "
        "JOIN items i ON i.source_id = s.id AND i.status IN ('new', 'error') "
        "GROUP BY s.id ORDER BY s.id").fetchall()


# ---------------------------------------------------------------- елементи

_ITEM_META = ("kind", "title", "author", "url", "path", "duration", "position")


def upsert_item(conn: sqlite3.Connection, source_id: int, key: str, list_name: str = "",
                **meta: Any) -> bool:
    """Додає елемент або оновлює його метадані (статус не чіпає). True — елемент новий."""
    unknown = set(meta) - set(_ITEM_META)
    if unknown:
        raise ValueError(f"unknown item fields: {sorted(unknown)}")
    row = conn.execute("SELECT id FROM items WHERE source_id=? AND list_name=? AND key=?",
                       (source_id, list_name, key)).fetchone()
    if row:
        if meta:
            assignments = ", ".join(f"{k}=?" for k in meta)
            conn.execute(f"UPDATE items SET {assignments} WHERE id=?", (*meta.values(), row["id"]))
            conn.commit()
        return False
    cols = ["source_id", "list_name", "key", "added_at", *meta]
    conn.execute(f"INSERT INTO items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 (source_id, list_name, key, now(), *meta.values()))
    conn.commit()
    return True


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return row


ALL, FETCH, SUMMARIZE = "all", "fetch", "summarize"


def _stage_filter(stage: str, language: str) -> tuple[str, list[Any]]:
    """Умова для кроку обробки. fetch — відео, для яких ще не зібрано текст і немає конспекту;
    summarize — ті, для яких текст уже є (або конспект з іншого списку, або це не відео)."""
    has_text = "key IN (SELECT key FROM transcripts)"
    has_result = "key IN (SELECT key FROM results WHERE language=?)"
    if stage == FETCH:
        return f" AND kind='video' AND NOT {has_text} AND NOT {has_result}", [language]
    if stage == SUMMARIZE:
        return f" AND (kind<>'video' OR {has_text} OR {has_result})", [language]
    return "", []


def pending(conn: sqlite3.Connection, limit: int, source_id: int | None = None,
            list_name: str | None = None, newest_first: bool = False, stage: str = ALL,
            language: str = "") -> list[sqlite3.Row]:
    """Черга: спершу нові, потім ті, що впали з помилкою; у порядку списку (або з кінця списку)."""
    where, args = _stage_filter(stage, language)
    sql = "SELECT * FROM items WHERE status IN (?, ?)" + where
    args = [*QUEUED, *args]
    if source_id is not None:
        sql += " AND source_id=?"
        args.append(source_id)
    if list_name is not None:
        sql += " AND list_name=?"
        args.append(list_name)
    order = "DESC" if newest_first else "ASC"
    sql += f" ORDER BY status = 'error', list_name, position {order}, id {order} LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def _set(conn: sqlite3.Connection, item_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))
    conn.commit()


def mark_done(conn: sqlite3.Connection, item_id: int, note_path: str | None = None) -> None:
    _set(conn, item_id, status=DONE, attempts=0, error=None, processed_at=now(), note_path=note_path)


def mark_no_text(conn: sqlite3.Connection, item_id: int, reason: str | None = None) -> None:
    """Тексту немає (немає субтитрів, скан без OCR) — не помилка, повторювати не треба."""
    _set(conn, item_id, status=NO_TEXT, error=reason, processed_at=now())


def mark_error(conn: sqlite3.Connection, item_id: int, error: str) -> str:
    """Записує невдачу. Після MAX_ATTEMPTS невдач поспіль — failed (автоматично більше не пробуємо,
    щоб постійна помилка щодня не з'їдала квоти). Повертає новий статус."""
    row = conn.execute("SELECT attempts FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    attempts = int(row["attempts"]) + 1
    status = FAILED if attempts >= MAX_ATTEMPTS else ERROR
    _set(conn, item_id, status=status, attempts=attempts, error=error, processed_at=now())
    return status


def retry_failed(conn: sqlite3.Connection, source_id: int | None = None) -> int:
    """Повертає failed у чергу (вручну, коли причину усунуто). Повертає к-сть елементів."""
    sql = "UPDATE items SET status=?, attempts=0, error=NULL WHERE status=?"
    args: list[Any] = [NEW, FAILED]
    if source_id is not None:
        sql += " AND source_id=?"
        args.append(source_id)
    n = conn.execute(sql, args).rowcount
    conn.commit()
    return n


def stage_counts(conn: sqlite3.Connection, language: str) -> tuple[int, int]:
    """Скільки елементів у черзі чекає (тексту, конспекту)."""
    out = []
    for stage in (FETCH, SUMMARIZE):
        where, args = _stage_filter(stage, language)
        out.append(int(conn.execute("SELECT COUNT(*) FROM items WHERE status IN (?, ?)" + where,
                                    [*QUEUED, *args]).fetchone()[0]))
    return out[0], out[1]


def note_path_taken(conn: sqlite3.Connection, note_path: str, item_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM items WHERE lower(note_path)=lower(?) AND id<>? LIMIT 1",
                       (note_path, item_id)).fetchone()
    return row is not None


# ---------------------------------------------------------------- тексти

def save_transcript(conn: sqlite3.Connection, key: str, text: str | None, language: str | None = None,
                    generated: bool = False) -> None:
    """Зберігає зібраний текст. text=None — тексту немає (теж запам'ятовуємо, щоб не питати знову)."""
    conn.execute("INSERT OR REPLACE INTO transcripts (key, language, generated, text, fetched_at) "
                 "VALUES (?, ?, ?, ?, ?)", (key, language, int(generated), text, now()))
    conn.commit()


def get_transcript(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM transcripts WHERE key=?", (key,)).fetchone()
    return row


def fetched_today(conn: sqlite3.Connection) -> int:
    """Скільки текстів зібрано сьогодні (для добового ліміту запитів до YouTube)."""
    today = datetime.now().date().isoformat()
    row = conn.execute("SELECT COUNT(*) FROM transcripts WHERE substr(fetched_at, 1, 10)=?", (today,)).fetchone()
    return int(row[0])


# ---------------------------------------------------------------- конспекти

def get_result(conn: sqlite3.Connection, key: str, language: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM results WHERE key=? AND language=?", (key, language)).fetchone()
    return row


def save_result(conn: sqlite3.Connection, key: str, language: str, summary: dict[str, Any], *,
                source_language: str | None, model: str, tokens_in: int, tokens_out: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO results (key, language, source_language, model, tokens_in, tokens_out, "
        "score, verdict, summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, language, source_language, model, tokens_in, tokens_out, summary.get("score"),
         summary.get("verdict"), json.dumps(summary, ensure_ascii=False), now()))
    conn.commit()


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for r in conn.execute("SELECT status, COUNT(*) n FROM items GROUP BY status"):
        counts[r["status"]] = r["n"]
    return counts
