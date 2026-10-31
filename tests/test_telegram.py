from __future__ import annotations

import json

import pytest

import samples
from backlog_digest import db, pipeline, telegram, web
from test_pipeline import GOOD, FakeChain, env, sync  # noqa: F401  (env — фікстура)

LONG = ("Why sleep matters. " + "Sleep consolidates memory and clears waste from the brain. " * 20).strip()


def msg(i, text, **kw):
    return {"id": i, "type": "message", "date": f"2026-10-0{i % 9 + 1}T10:00:00", "from": "Me", "text": text, **kw}


def write_export(root, single=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "files").mkdir(exist_ok=True)
    samples.pdf(root / "files" / "paper.pdf", [samples.PARA])
    (root / "voice_messages").mkdir(exist_ok=True)
    (root / "voice_messages" / "audio_1.ogg").write_bytes(b"ogg")
    saved = {"type": "saved_messages", "id": 1, "messages": [
        msg(1, "look https://www.youtube.com/watch?v=vid00000001 later",
            text_entities=[{"type": "plain", "text": "look "},
                           {"type": "link", "text": "https://www.youtube.com/watch?v=vid00000001"},
                           {"type": "plain", "text": " later"}]),
        msg(2, ["an ", {"type": "text_link", "text": "article", "href": "https://example.com/a?utm_source=tg"}]),
        msg(3, [{"type": "link", "text": "example.com/b"}]),                      # без протоколу
        msg(4, [{"type": "link", "text": "https://t.me/channel/5"},              # пост Telegram — пропускаємо
                {"type": "link", "text": "https://www.youtube.com/@somechannel"}]),  # канал — не стаття
        msg(5, LONG),
        msg(6, "", file="files/paper.pdf", file_name="Paper on sleep.pdf", mime_type="application/pdf"),
        msg(7, "", file="(File not included. Change data exporting settings to download.)", file_name="x.pdf"),
        msg(8, "", file="voice_messages/audio_1.ogg", media_type="voice_message"),
        {"id": 9, "type": "service", "action": "pin_message", "text": ""},
        msg(10, [{"type": "link", "text": "https://example.com/a"}]),             # повтор — один раз
    ]}
    channel = {"type": "public_channel", "name": "Science news", "id": 77, "messages": [
        msg(1, [{"type": "link", "text": "https://youtu.be/vid00000002"}]),
    ]}
    data = saved if single else {"chats": {"list": [saved, channel]}}
    (root / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def test_read_export(tmp_path):
    root = write_export(tmp_path / "Export")
    assert telegram.find_result(root) == root / "result.json" == telegram.find_result(root / "result.json")
    ex = telegram.read_export(root, saved_name="Saved")
    assert [(lk.url, lk.list_name) for lk in ex.links] == [
        ("https://www.youtube.com/watch?v=vid00000001", "Saved"), ("https://example.com/a?utm_source=tg", "Saved"),
        ("https://example.com/b", "Saved"), ("https://youtu.be/vid00000002", "Science news")]
    assert [(p.key, p.list_name, p.title, p.date) for p in ex.posts] == [
        ("telegram:1:5", "Saved",
         "Why sleep matters. Sleep consolidates memory and clears waste from the brain. Sleep…", "2026-10-06")]
    assert [(f.path.name, f.title) for f in ex.files] == [("paper.pdf", "Paper on sleep"),
                                                          ("audio_1.ogg", "Saved 2026-10-09 10:00")]
    assert [(c.name, c.messages) for c in ex.chats] == [("Saved", 10), ("Science news", 1)]
    only = telegram.read_export(root, ["Science news"], saved_name="Saved")
    assert len(only.links) == 1 and not only.posts and not only.files


def test_single_chat_export_and_errors(tmp_path):
    root = write_export(tmp_path / "Chat", single=True)
    assert [c.name for c in telegram.list_chats(root)] == ["Saved Messages"]
    assert not telegram.is_export(tmp_path)
    with pytest.raises(telegram.ExportError):
        telegram.read_export(tmp_path)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "result.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(telegram.ExportError):
        telegram.read_export(tmp_path / "bad")


def test_telegram_source_sync_and_summaries(env, tmp_path, monkeypatch):
    sync(env)                                                    # YouTube-джерело з vid00000001..3
    env.make().run(limit=1)                                      # vid00000001 уже має конспект
    root = write_export(tmp_path / "Export")
    sid = pipeline.add_telegram_source(env.conn, "Telegram", str(root), [], "TG")
    new, total = pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)
    assert (new, total) == (7, 7)                                # 4 посилання + пост + PDF + голосове
    kinds = sorted(r["kind"] for r in env.conn.execute("SELECT kind FROM items WHERE source_id=?", (sid,)))
    assert kinds == ["article", "article", "media", "post", "text", "video", "video"]
    assert any("long posts: 1, files: 2" in line for line in env.logs)
    assert pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)[0] == 0   # без повторів

    def gone(url):
        raise web.Gone("404")

    monkeypatch.setattr(web, "fetch_article", gone)
    chain = FakeChain([GOOD.replace('"summary_enough"', '"watch"')] * 3)
    stats = env.make(chain).run(limit=10, source_id=sid, stage=pipeline.SUMMARIZE)
    # пост і PDF — конспекти; відео з YouTube-джерела — без моделі; 2 статті (404) і голосове (немає Whisper) —
    # без тексту; vid00000002 чекає збору субтитрів
    assert (stats.done, stats.reused, stats.no_text) == (2, 1, 3)
    post = env.conn.execute("SELECT * FROM items WHERE kind='post'").fetchone()
    assert post["status"] == db.DONE
    assert any("Telegram post, Saved Messages, 2026-10-06" in c for c in chain.calls)
    text = next((env.root / "TG" / "Saved Messages").glob("Why sleep matters*.md")).read_text(encoding="utf-8")
    assert "Read it" in text and "2026-10-06" in text and "Open the article" not in text


def test_cli_add_telegram(tmp_path, paths, monkeypatch, capsys):
    from backlog_digest import cli

    root = write_export(tmp_path / "Export")
    assert cli.main(["add", str(root), "--only", "Science news", "--no-sync"]) == 0
    conn = db.connect(paths.db_file)
    source = db.list_sources(conn)[0]
    assert (source["type"], source["name"], source["settings"]["chats"]) == ("telegram", "Telegram", ["Science news"])
    assert "Telegram" in capsys.readouterr().out


def test_menu_add_telegram(tmp_path, paths, monkeypatch):
    from backlog_digest import config, menu

    root = write_export(tmp_path / "Export")
    answers = iter([str(root), "2", "", ""])
    monkeypatch.setattr(menu, "ask", lambda prompt="": next(answers))
    conn = db.connect(paths.db_file)
    m = menu.Menu(paths, config.defaults(), conn)
    m.add_telegram()
    source = db.list_sources(conn)[0]
    assert (source["name"], source["settings"]["chats"]) == ("Science news", ["Science news"])
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_video_link_without_title_gets_it_from_oembed(env, tmp_path, monkeypatch):
    from backlog_digest import youtube

    root = write_export(tmp_path / "Export")
    sid = pipeline.add_telegram_source(env.conn, "Telegram", str(root), ["Science news"], "TG")
    pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)
    monkeypatch.setattr(youtube, "video_info", lambda vid: youtube.Entry(vid, "Real title", "Real channel"))
    env.make().run(limit=5, source_id=sid)
    row = env.conn.execute("SELECT * FROM items WHERE source_id=?", (sid,)).fetchone()
    assert (row["title"], row["author"], row["status"]) == ("Real title", "Real channel", db.DONE)
    assert (env.root / "TG" / "Science news" / "Real title.md").exists()
