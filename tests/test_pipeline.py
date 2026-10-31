from __future__ import annotations

import json

import pytest

import samples
from backlog_digest import config, db, llm, pipeline, youtube

GOOD = json.dumps({
    "about": "What it is about.", "key_points": ["k1", "k2"], "useful": ["u1"], "recommendations_count": 2,
    "evidence": "medium", "evidence_notes": "data", "questionable_claims": [], "water_percent": 10,
    "water_parts": "", "audience": "all", "verdict": "summary_enough", "verdict_reason": "r", "score": 3,
    "tags": ["t"],
})


class FakeChain:
    def __init__(self, answers=None, exhausted=False):
        self.answers = list(answers or [])
        self.slots = [object()]
        self.exhausted = exhausted
        self.calls = []

    def active(self):
        return [] if self.exhausted else ["fake/model"]

    def complete(self, system, user, *, deep=False, json_out=True):
        self.calls.append(user)
        reply = self.answers.pop(0) if self.answers else GOOD
        if isinstance(reply, Exception):
            raise reply
        return llm.LLMResult(reply, "fake/model", 100, 50)


@pytest.fixture
def env(paths, tmp_path, monkeypatch):
    cfg = config.defaults()
    cfg["output"]["path"] = str(tmp_path / "vault")
    conn = db.connect(paths.db_file)
    listing = youtube.Listing("Playlist", [youtube.Entry(f"vid{i:08d}", f"Video {i}", "Chan", 60 * i)
                                           for i in range(1, 4)])
    monkeypatch.setattr(youtube, "list_url", lambda url, cookies=None: listing)
    transcripts = {}
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid, langs: transcripts.get(
        vid, youtube.Transcript(f"[00:00] text of {vid}", "en", True)))
    sid = pipeline.add_youtube_source(conn, "YouTube", ["https://www.youtube.com/playlist?list=PL1"], "Video")
    logs = []

    def make(chain=None):
        sleeps = []
        proc = pipeline.Processor(conn, cfg, paths, log=logs.append, chain=chain or FakeChain(),
                                  sleep=sleeps.append)
        proc.sleeps = sleeps
        return proc

    yield type("Env", (), {"cfg": cfg, "conn": conn, "sid": sid, "make": staticmethod(make), "logs": logs,
                           "transcripts": transcripts, "root": tmp_path / "vault"})
    conn.close()


def sync(env):
    return pipeline.sync_source(env.conn, db.get_source(env.conn, env.sid), env.logs.append)


def test_sync_adds_and_does_not_duplicate(env):
    assert sync(env) == (3, 3)
    assert sync(env) == (0, 3)
    row = env.conn.execute("SELECT * FROM items ORDER BY position").fetchone()
    assert (row["key"], row["list_name"], row["url"], row["duration"]) == (
        "youtube:vid00000001", "Playlist", "https://www.youtube.com/watch?v=vid00000001", 60)


def test_sync_reports_broken_list(env, monkeypatch):
    def broken(url, cookies=None):
        raise youtube.ListError("private playlist")

    monkeypatch.setattr(youtube, "list_url", broken)
    assert sync(env) == (0, 0)
    assert any("private playlist" in line for line in env.logs)


def test_process_writes_notes(env):
    sync(env)
    proc = env.make()
    stats = proc.run(limit=10)
    assert (stats.done, stats.errors, stats.tokens) == (3, 0, 450)
    assert len(proc.sleeps) == 2                          # пауза між відео, але не перед першим
    note = env.root / "Video" / "Playlist" / "Video 1.md"
    text = note.read_text(encoding="utf-8")
    assert "What it is about." in text and "Summary is enough" in text
    row = env.conn.execute("SELECT * FROM items WHERE key='youtube:vid00000001'").fetchone()
    assert row["status"] == db.DONE and row["note_path"] == str(note)
    assert db.get_result(env.conn, "youtube:vid00000001", "en")["model"] == "fake/model"


def test_limit_and_daily_limit(env):
    sync(env)
    assert env.make().run(limit=1).done == 1
    env.cfg["limits"]["per_day"] = 2
    assert env.make().run(limit=10).done == 1             # лишалося 2 − 1 на сьогодні
    assert env.make().run(limit=10).done == 0
    assert any("limits.per_day" in line for line in env.logs)


def test_no_subtitles_and_errors(env, monkeypatch):
    sync(env)
    env.transcripts["vid00000001"] = None

    def fetch(vid, langs):
        if vid == "vid00000002":
            raise RuntimeError("weird failure")
        return env.transcripts.get(vid, youtube.Transcript("text", "en", False))

    monkeypatch.setattr(youtube, "fetch_transcript", fetch)
    stats = env.make().run(limit=10)
    assert (stats.done, stats.no_text, stats.errors) == (1, 1, 1)
    counts = db.status_counts(env.conn)
    assert (counts[db.NO_TEXT], counts[db.ERROR], counts[db.DONE]) == (1, 1, 1)


def test_broken_json_is_retried_once(env):
    sync(env)
    chain = FakeChain(["not json", GOOD, "bad", "still bad"])
    stats = env.make(chain).run(limit=2)
    assert (stats.done, stats.errors) == (1, 1)
    assert "could not be parsed" in chain.calls[1]


def test_blocked_youtube_stops_the_run(env, monkeypatch):
    sync(env)

    def blocked(vid, langs):
        raise youtube.BlockedError("HTTP 429")

    monkeypatch.setattr(youtube, "fetch_transcript", blocked)
    stats = env.make().run(limit=10)
    assert stats.stopped == "blocked" and stats.errors == 0
    assert db.status_counts(env.conn)[db.NEW] == 3       # нічого не зіпсовано, усе лишилось у черзі


def test_quota_stops_the_run_without_marking_errors(env):
    sync(env)
    stats = env.make(FakeChain([GOOD, llm.QuotaExhausted("all")])).run(limit=10)
    assert (stats.done, stats.errors, stats.stopped) == (1, 0, "quota")
    assert db.status_counts(env.conn)[db.NEW] == 2


def test_model_error_marks_item_and_continues(env):
    sync(env)
    stats = env.make(FakeChain([llm.LLMError("boom")])).run(limit=10)
    assert (stats.done, stats.errors) == (2, 1)


def test_same_video_in_another_list_reuses_summary(env, monkeypatch):
    sync(env)
    env.make().run(limit=10)
    sid2 = pipeline.add_youtube_source(env.conn, "Other", ["x"], "Other")
    pipeline.sync_source(env.conn, db.get_source(env.conn, sid2), env.logs.append)
    chain = FakeChain()
    stats = env.make(chain).run(limit=10)
    assert (stats.reused, stats.done, chain.calls) == (3, 0, [])
    assert (env.root / "Other" / "Playlist" / "Video 2.md").exists()


def test_not_ready(env):
    sync(env)
    assert env.make(FakeChain(exhausted=True)).run(limit=10).done == 0
    env.cfg["output"]["path"] = ""
    assert env.make().run(limit=10).done == 0
    assert db.status_counts(env.conn)[db.NEW] == 3


def test_newest_first(env):
    sync(env)
    env.cfg["youtube"]["newest_first"] = True
    env.make().run(limit=1)
    assert db.get_result(env.conn, "youtube:vid00000003", "en")


def test_transcript_language_preference(env):
    env.cfg["general"].update(summary_language="sv", ui_language="uk")
    assert env.make()._transcript_languages() == ["en", "sv", "uk"]
    env.cfg["youtube"]["languages"] = ["pt-BR", "pt"]
    assert env.make()._transcript_languages() == ["pt"]


def test_queue_eta(env):
    sync(env)
    assert pipeline.queue_eta_days(env.conn, 2, "en") == 2


# ---------------------------------------------------------------- два кроки: збір текстів і конспекти

def test_fetch_only_needs_no_models_or_output(env):
    sync(env)
    env.cfg["output"]["path"] = ""
    chain = FakeChain(exhausted=True)
    stats = env.make(chain).run(limit=10, stage=pipeline.FETCH)
    assert (stats.fetched, stats.done, chain.calls) == (3, 0, [])
    assert db.get_transcript(env.conn, "youtube:vid00000001")["text"] == "[00:00] text of vid00000001"
    assert db.status_counts(env.conn)[db.NEW] == 3          # конспектів ще немає — елементи в черзі
    assert db.stage_counts(env.conn, "en") == (0, 3)
    assert env.make().run(limit=10, stage=pipeline.FETCH).fetched == 0   # повторно не збирає


def test_summarize_only_uses_stored_text_without_youtube(env, monkeypatch):
    sync(env)
    env.make().run(limit=2, stage=pipeline.FETCH)

    def no_youtube(vid, langs):
        raise AssertionError("YouTube must not be called")

    monkeypatch.setattr(youtube, "fetch_transcript", no_youtube)
    proc = env.make()
    stats = proc.run(limit=10, stage=pipeline.SUMMARIZE)
    assert (stats.done, stats.fetched, proc.sleeps) == (2, 0, [])
    assert db.stage_counts(env.conn, "en") == (1, 0)          # третє відео ще чекає субтитрів
    assert db.get_transcript(env.conn, "youtube:vid00000001") is None   # конспект є — субтитри видалено


def test_fetch_daily_limit_counts_youtube_requests(env):
    sync(env)
    env.cfg["limits"]["per_day"] = 2
    env.transcripts["vid00000001"] = None                     # «немає субтитрів» — теж запит до YouTube
    stats = env.make().run(limit=10, stage=pipeline.FETCH)
    assert (stats.fetched, stats.no_text) == (1, 1)
    assert env.make().run(limit=10, stage=pipeline.FETCH).fetched == 0
    assert any("limits.per_day" in line for line in env.logs)
    stats = env.make().run(limit=10)                          # усе разом: конспекти із зібраного — можна
    assert stats.done == 1
    assert db.fetched_today(env.conn) == 2                    # субтитри видалено, а запити враховано


def test_transcript_errors_count_toward_daily_limit(env, monkeypatch):
    sync(env)
    env.cfg["limits"]["per_day"] = 1

    def broken(vid, langs):
        raise RuntimeError("weird failure")

    monkeypatch.setattr(youtube, "fetch_transcript", broken)
    stats = env.make().run(limit=10, stage=pipeline.FETCH)
    assert (stats.errors, db.fetched_today(env.conn)) == (1, 1)


def test_processed_videos_leave_no_subtitles_behind(env):
    sync(env)
    env.transcripts["vid00000002"] = None
    env.make().run(limit=10)
    rows = env.conn.execute("SELECT key, text FROM transcripts").fetchall()
    assert [tuple(r) for r in rows] == [("youtube:vid00000002", None)]   # лишається лише «субтитрів немає»


def test_blocked_youtube_still_summarizes_stored_text(env, monkeypatch):
    sync(env)
    env.make().run(limit=1, stage=pipeline.FETCH)

    def blocked(vid, langs):
        raise youtube.BlockedError("HTTP 429")

    monkeypatch.setattr(youtube, "fetch_transcript", blocked)
    env.cfg["youtube"]["newest_first"] = True                  # спершу трапиться відео без субтитрів
    stats = env.make().run(limit=10)
    assert (stats.stopped, stats.done, stats.errors) == ("blocked", 1, 0)
    assert db.stage_counts(env.conn, "en") == (2, 0)


def test_quota_keeps_collecting_subtitles(env):
    sync(env)
    stats = env.make(FakeChain([llm.QuotaExhausted("all")])).run(limit=10)
    assert (stats.stopped, stats.done, stats.fetched) == ("quota", 0, 3)
    assert db.stage_counts(env.conn, "en") == (0, 3)


# ---------------------------------------------------------------- папки й книги

@pytest.fixture
def books(env, tmp_path):
    root = tmp_path / "Books"
    (root / "Science").mkdir(parents=True)
    samples.pdf(root / "Science" / "Sleep.pdf", [samples.PARA, samples.PARA])
    samples.pdf(root / "Scan.pdf", [""])
    (root / "Broken.epub").write_bytes(b"not a zip")
    sid = pipeline.add_folder_source(env.conn, "Library", str(root), "Books")
    pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)
    return root


def test_folder_books_are_summarized_without_youtube(env, books, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid, langs: pytest.fail("no YouTube for books"))
    assert db.stage_counts(env.conn, "en") == (0, 3)              # книги не чекають «збору субтитрів»
    chain = FakeChain([GOOD.replace('"summary_enough"', '"watch"')])
    stats = env.make(chain).run(limit=10, stage=pipeline.SUMMARIZE)
    assert (stats.done, stats.no_text, stats.errors) == (1, 1, 1)  # книга / скан / пошкоджений файл
    assert "[p. 2]" in chain.calls[0] and "Format: PDF" in chain.calls[0]
    note = (env.root / "Books" / "Science" / "Sleep.md").read_text(encoding="utf-8")
    assert "Read it" in note and "Open the file" in note and '"format": "PDF"' not in note
    assert 'format: "PDF"' in note and "file:///" in note


def test_long_book_is_read_in_parts_and_resumes_after_quota(env, books):
    env.cfg["models"]["max_chars"] = 400                           # дві сторінки по ~300 символів — дві частини
    chain = FakeChain(["notes of part 1", llm.QuotaExhausted("all")])
    stats = env.make(chain).run(limit=10, stage=pipeline.SUMMARIZE)
    assert (stats.stopped, stats.done) == ("quota", 0)
    cache = env.make().paths.cache_dir / "chunks"
    assert len(list(cache.rglob("*.md"))) == 1                     # перша частина збережена

    chain = FakeChain(["notes of part 2", GOOD])
    stats = env.make(chain).run(limit=10, stage=pipeline.SUMMARIZE)
    assert stats.done == 1 and len(chain.calls) == 2               # першу частину не перепитували
    assert "notes of part 1" in chain.calls[1] and "notes of part 2" in chain.calls[1]
    assert "2 parts" in chain.calls[1] and not list(cache.rglob("*.md"))   # кеш частин прибрано


# ---------------------------------------------------------------- веб-статті

def test_web_source_articles_and_youtube_links(env, tmp_path, monkeypatch):
    from backlog_digest import web

    (tmp_path / "list.txt").write_text("https://blog.example/post\nhttps://www.youtube.com/watch?v=vid00000001\n"
                                       "https://blog.example/gone\n", encoding="utf-8")
    sid = pipeline.add_web_source(env.conn, "Articles", [], [str(tmp_path / "list.txt")], "Web")
    sync(env)                                                              # джерело YouTube з тим самим відео
    pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)
    kinds = [r[0] for r in env.conn.execute("SELECT kind FROM items WHERE source_id=? ORDER BY position", (sid,))]
    assert kinds == ["article", "video", "article"]

    def fetch(url):
        if url.endswith("gone"):
            raise web.Gone("the page no longer exists (404)")
        return web.Article("[text] " + samples.PARA, "A great post", "Jane Doe", "2026-01-02", "blog.example")

    monkeypatch.setattr(web, "fetch_article", fetch)
    chain = FakeChain()
    stats = env.make(chain).run(limit=10, stage=pipeline.SUMMARIZE)
    assert (stats.done, stats.no_text) == (1, 1)
    assert "web article, blog.example, Jane Doe, 2026-01-02" in chain.calls[0]
    note = (env.root / "Web" / "A great post.md").read_text(encoding="utf-8")
    assert "Open the article" in note and "Jane Doe" in note and 'site: "blog.example"' in note
    assert db.stage_counts(env.conn, "en") == (4, 0)   # 3 відео з YouTube + 1 із закладок
    env.make().run(limit=10)                                             # усе разом: відео — один конспект
    assert env.conn.execute("SELECT COUNT(*) FROM results WHERE key='youtube:vid00000001'").fetchone()[0] == 1


def test_known_missing_subtitles_apply_to_the_same_video_elsewhere(env):
    sync(env)
    env.transcripts["vid00000001"] = None
    env.make().run(limit=10, stage=pipeline.FETCH)
    sid2 = pipeline.add_youtube_source(env.conn, "Other", ["x"], "Other")
    pipeline.sync_source(env.conn, db.get_source(env.conn, sid2), env.logs.append)
    assert db.stage_counts(env.conn, "en") == (0, 5)
    stats = env.make().run(limit=10, stage=pipeline.SUMMARIZE)
    assert (stats.done, stats.reused, stats.no_text) == (2, 2, 1)


def test_big_link_list_shows_progress_and_folds_lists(env, tmp_path):
    lines = [f"https://e.example/{i}" for i in range(2500)]
    (tmp_path / "many.txt").write_text("\n".join(lines), encoding="utf-8")
    sid = pipeline.add_web_source(env.conn, "Many", [], [str(tmp_path / "many.txt")])
    links = [web_link(f"https://f.example/{i}", f"L{i}") for i in range(40)]
    source = db.get_source(env.conn, sid)
    assert pipeline.sync_source(env.conn, source, env.logs.append) == (2500, 2500)
    assert sum("saved" in line for line in env.logs) == 2          # 1000 і 2000
    env.logs.clear()
    assert pipeline._add_links(env.conn, source, links, env.logs.append) == 40
    assert len(env.logs) == pipeline.MAX_LISTS_SHOWN + 1 and "10 more lists" in env.logs[-1]


def web_link(url, list_name):
    from backlog_digest import web

    return web.Link(url, "", list_name)


def test_single_video_goes_straight_into_source_folder(env, monkeypatch):
    single = youtube.Listing("", [youtube.Entry("vid00000009", "Lone video", "Chan", 60)])
    monkeypatch.setattr(youtube, "list_url", lambda url, cookies=None: single)
    assert sync(env)[0] == 1
    env.make().run(limit=1)
    assert (env.root / "Video" / "Lone video.md").exists()     # не «Video/YouTube/Lone video.md»
