from __future__ import annotations

import json

from backlog_digest import db, library, pipeline, sorting_menu
from test_menu import feed
from test_pipeline import GOOD, FakeChain, env, sync  # noqa: F401  (env — фікстура)


def summarize_all(env):
    sync(env)
    assert env.make().run(limit=10).done == 3


def test_topics_text_and_normalize():
    tree = "# comment\nHealth\n  Sleep\n\tFood\nTech/AI\nOther\nHealth\n"
    assert library.parse_tree(tree, "Other") == ["Health", "Health/Sleep", "Health/Food", "Tech", "Tech/AI", "Other"]
    assert library.normalize_topics(["A/B/C", "x:y"], "Other") == ["A", "A/B", "x y", "Other"]
    assert library.draft_text(["A", "A/B", "Other"], "Other") == "A\n  B"
    assert library.closest("health/SLEEP", ["Health", "Health/Sleep", "Other"], "Other") == "Health/Sleep"
    assert library.closest("Health/Nothing", ["Health", "Other"], "Other") == "Health"
    assert library.closest("Cooking", ["Health", "Other"], "Other") == "Other"


def test_classify_in_batches_and_apply_moves_notes(env, monkeypatch):
    summarize_all(env)
    items = db.done_items(env.conn, env.sid, "Playlist", "en")
    assert [it["s"]["score"] for it in items] == [3, 3, 3]
    monkeypatch.setattr(library, "BATCH", 2)
    chain = FakeChain([json.dumps({"assign": {"1": "Health/Sleep", "2": "nonsense"}}),
                       json.dumps({"assign": {"1": "tech"}})])
    topics = ["Health", "Health/Sleep", "Tech", "Other"]
    assign = library.classify(chain, items, topics, "", "Other", log=lambda _: None)
    assert list(assign.values()) == ["Health/Sleep", "Other", "Tech"]
    proc = env.make()
    old = env.root / "Video" / "Playlist" / "Video 1.md"
    assert old.exists()
    assert library.apply(env.conn, items, assign, proc.rewrite) == 3
    new = env.root / "Video" / "Playlist" / "Health" / "Sleep" / "Video 1.md"
    assert new.exists() and not old.exists()
    assert 'topic: "Health/Sleep"' in new.read_text(encoding="utf-8")
    row = env.conn.execute("SELECT topic, note_path FROM items WHERE key='youtube:vid00000001'").fetchone()
    assert (row["topic"], row["note_path"]) == ("Health/Sleep", str(new))
    # тема змінилась ще раз — нотатка переїжджає, порожні теки прибираються
    items[0]["topic"] = "Tech"
    library.apply(env.conn, items[:1], {items[0]["id"]: "Tech"}, proc.rewrite)
    assert (env.root / "Video" / "Playlist" / "Tech" / "Video 1.md").exists()
    assert not (env.root / "Video" / "Playlist" / "Health").exists()


def test_new_summaries_go_into_existing_scheme(env):
    sync(env)
    env.make().run(limit=1)
    db.save_scheme(env.conn, env.sid, "Playlist", "manual", ["Health", "Other"], "")
    chain = FakeChain([GOOD, GOOD, json.dumps({"assign": {"1": "Health", "2": "Health", "3": "Other"}})])
    stats = env.make(chain).run(limit=10)
    assert stats.done == 2
    topics = [r[0] for r in env.conn.execute("SELECT topic FROM items ORDER BY position")]
    assert topics == ["Health", "Health", "Other"]     # розкладено й першу, ще без теми
    assert (env.root / "Video" / "Playlist" / "Other" / "Video 3.md").exists()


def test_sort_menu_auto_mode(env, paths, monkeypatch):
    summarize_all(env)
    chain = FakeChain([json.dumps({"topics": ["Science", "Science/Space", "Life"]}),
                       json.dumps({"assign": {"1": "Science/Space", "2": "Life", "3": "Life"}}),
                       json.dumps({"theme": "Videos about life", "similar": [{"ids": [2, 3, 9], "why": "same advice",
                                                                               "best": 3}],
                                   "off_topic": [{"id": 1, "why": "about space"}, {"id": 7, "why": "?"}]})])
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: chain))
    # список 1 → сортувати → режим 3 (авто) → критерій 2 → без своїх правил → 4–8 тем → не правити →
    # застосувати → звіт → не відкривати індекс
    feed(monkeypatch, ["1", "1", "3", "2", "", "", "no", "yes", "yes", "no"])
    sorting_menu.run(env.conn, env.cfg, paths)
    scheme = db.get_scheme(env.conn, env.sid, "Playlist")
    assert scheme["mode"] == "auto" and scheme["topics"] == ["Science", "Science/Space", "Life", "Other"]
    assert "value" in scheme["criteria"]
    assert (env.root / "Video" / "Playlist" / "Science" / "Space" / "Video 1.md").exists()
    assert "Video 1" in chain.calls[0]
    report = db.get_scheme(env.conn, env.sid, "Playlist")["report"]
    ids = [r[0] for r in env.conn.execute("SELECT id FROM items ORDER BY position")]
    assert report["similar"] == [{"ids": ids[1:], "why": "same advice", "best": ids[2]}]   # номер 9 відкинуто
    assert report["off_topic"] == [{"id": ids[0], "why": "about space"}]
    index = (env.root / "Video" / "Playlist" / "_Index — Playlist.md").read_text(encoding="utf-8")
    assert "> Videos about life" in index and "### Science (1)" in index and "#### Space (1)" in index
    assert "[[Video/Playlist/Science/Space/Video 1\\|Video 1]]" in index      # Obsidian: «|» у таблиці
    assert "✅ [[Video/Playlist/Life/Video 3|Video 3]]" in index and "about space" in index


def test_sort_menu_cancel_changes_nothing(env, paths, monkeypatch):
    summarize_all(env)
    chain = FakeChain([json.dumps({"assign": {"1": "A", "2": "B", "3": "A"}})])
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: chain))
    feed(monkeypatch, ["1", "1", "2", "", "", "A, B", "rule", "no"])  # «Важливість», кошики A і B, не застосовувати
    sorting_menu.run(env.conn, env.cfg, paths)
    assert db.get_scheme(env.conn, env.sid, "Playlist") is None
    assert (env.root / "Video" / "Playlist" / "Video 1.md").exists()


def test_index_without_scheme_markdown_links_and_problems(env, paths):
    summarize_all(env)
    env.cfg["output"]["kind"] = "markdown"
    item = env.conn.execute("SELECT id FROM items ORDER BY position").fetchone()[0]
    db.mark_no_text(env.conn, item, "no subtitles")
    proc = env.make()
    path = proc.write_index(env.sid, "Playlist")
    text = path.read_text(encoding="utf-8")
    assert "## All (by score)" in text and "[Video 2](<Video 2.md>)" in text
    assert "## ⚠️ Errors and no text" in text and "no subtitles" in text
    assert "Done **2** of 3" in text


def test_index_updates_after_processing_when_list_has_scheme(env):
    sync(env)
    env.make().run(limit=1)
    db.save_scheme(env.conn, env.sid, "Playlist", "none", [], "")
    env.make().run(limit=1)
    text = (env.root / "Video" / "Playlist" / "_Index — Playlist.md").read_text(encoding="utf-8")
    assert "Video 1" in text and "Video 2" in text
