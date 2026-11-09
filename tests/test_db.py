from __future__ import annotations

import sqlite3

import pytest

from backlog_digest import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "data" / "test.sqlite")
    yield c
    c.close()


@pytest.fixture
def source(conn):
    return db.add_source(conn, "youtube", "YouTube", {"playlists": ["WL"]}, "YouTube")


def item_ids(conn):
    return [r["id"] for r in conn.execute("SELECT id FROM items ORDER BY id")]


def test_fresh_database_is_migrated(conn):
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "items", "results"} <= tables


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "x.sqlite"
    db.connect(path).close()
    c = db.connect(path)
    assert db.migrate(c) == 0
    c.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "x.sqlite"
    c = sqlite3.connect(path)
    c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    c.close()
    with pytest.raises(db.DbError):
        db.connect(path)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    broken = [*db.MIGRATIONS, "CREATE TABLE t2 (x); THIS IS NOT SQL;"]
    monkeypatch.setattr(db, "MIGRATIONS", broken)
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(broken))
    with pytest.raises(sqlite3.Error):
        db.connect(tmp_path / "x.sqlite")
    c = sqlite3.connect(tmp_path / "x.sqlite")
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(broken) - 1
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='t2'").fetchone()
    c.close()


def test_sources(conn, source):
    [s] = db.list_sources(conn)
    assert s["id"] == source and s["settings"] == {"playlists": ["WL"]}


def test_upsert_item(conn, source):
    assert db.upsert_item(conn, source, "youtube:abc", "WL", title="Old", kind="video") is True
    assert db.upsert_item(conn, source, "youtube:abc", "WL", title="New") is False
    assert db.upsert_item(conn, source, "youtube:abc", "Other list") is True   # той самий ключ в іншому списку
    rows = conn.execute("SELECT * FROM items WHERE list_name='WL'").fetchall()
    assert len(rows) == 1 and rows[0]["title"] == "New" and rows[0]["status"] == db.NEW
    with pytest.raises(ValueError):
        db.upsert_item(conn, source, "youtube:x", bogus=1)


def test_pending_order_and_filters(conn, source):
    for i, key in enumerate(["a", "b", "c"]):
        db.upsert_item(conn, source, key, "L", position=i)
    a, b, _ = item_ids(conn)
    db.mark_error(conn, a, "boom")
    db.mark_done(conn, b)
    assert [r["key"] for r in db.pending(conn, 10)] == ["c", "a"]   # спершу нові, потім помилки
    assert [r["key"] for r in db.pending(conn, 1)] == ["c"]
    assert db.pending(conn, 10, list_name="other") == []
    assert len(db.pending(conn, 10, source_id=source)) == 2


def test_pending_takes_sources_in_turn(conn, source):
    big = db.add_source(conn, "raindrop", "Big", {}, "Big")
    for i in range(5):
        db.upsert_item(conn, big, f"b{i}", "0. A", position=i)   # «0. A» за абеткою раніше за «L»
    for i in range(2):
        db.upsert_item(conn, source, f"y{i}", "L", position=i)
    assert [r["key"] for r in db.pending(conn, 5)] == ["y0", "b0", "y1", "b1", "b2"]
    assert [r["key"] for r in db.pending(conn, 2, source_id=big)] == ["b0", "b1"]


def test_attempt_limit(conn, source):
    db.upsert_item(conn, source, "k")
    [item] = item_ids(conn)
    assert db.MAX_ATTEMPTS == 3
    assert db.mark_error(conn, item, "1") == db.ERROR
    assert db.mark_error(conn, item, "2") == db.ERROR
    assert db.mark_error(conn, item, "3") == db.FAILED
    assert db.pending(conn, 10) == []                       # failed автоматично не повторюється
    assert db.retry_failed(conn) == 1
    row = db.get_item(conn, item)
    assert row["status"] == db.NEW and row["attempts"] == 0 and row["error"] is None


def test_done_resets_attempts_and_no_text_is_final(conn, source):
    db.upsert_item(conn, source, "k1")
    db.upsert_item(conn, source, "k2")
    i1, i2 = item_ids(conn)
    db.mark_error(conn, i1, "temporary")
    db.mark_done(conn, i1)
    assert db.get_item(conn, i1)["attempts"] == 0
    db.mark_no_text(conn, i2, "no subtitles")
    assert db.status_counts(conn) == {"new": 0, "done": 1, "no_text": 1, "error": 0, "failed": 0}
    assert db.pending(conn, 10) == []


def test_status_is_constrained(conn, source):
    db.upsert_item(conn, source, "k")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE items SET status='bogus'")


def test_deleting_source_deletes_items(conn, source):
    db.upsert_item(conn, source, "k")
    conn.execute("DELETE FROM sources WHERE id=?", (source,))
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
