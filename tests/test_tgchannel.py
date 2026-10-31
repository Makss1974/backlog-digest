from __future__ import annotations

import pytest

from backlog_digest import db, pipeline, telegram, tgchannel
from test_pipeline import env  # noqa: F401  (env — фікстура)
from test_telegram import LONG


def post(n, body, name="science"):
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message"
 data-post="{name}/{n}" data-view="x">
 <div class="tgme_widget_message_bubble">
  <a class="tgme_widget_message_reply" href="https://t.me/{name}/1">
   <div class="tgme_widget_message_text js-message_text">
   quoted https://example.com/quoted</div></a>
  <div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div>
  <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/{name}/{n}">
   <time datetime="2026-10-{n % 28 + 1:02d}T10:00:00+00:00" class="time">10:00</time></a></div>
 </div></div></div>"""


def page(ids, older=None, title="Science channel"):
    prev = f'<link rel="prev" href="/s/science?before={older}">' if older else ""
    bodies = {
        5: 'watch <a href="https://www.youtube.com/watch?v=vid00000005">this</a>',
        6: LONG.replace(". ", ".<br/>", 1),
        7: 'see <a href="https://t.me/other/3">a post</a> and <a href="https://example.com/a?x=1">an article</a>',
    }
    posts = "".join(post(n, bodies.get(n, f"short note {n}")) for n in ids)
    return (f'<html><head><meta property="og:title" content="{title}">{prev}</head><body>'
            f'<div class="tgme_channel_info"></div>{posts}'
            f'<a class="tgme_widget_message_link_preview" href="https://example.com/outside"></a></body></html>')


class FakeTelegram:
    """Канал з публікаціями 1..N, по `per_page` на сторінці — як t.me/s/."""

    def __init__(self, last=8, per_page=2):
        self.last, self.per_page, self.urls = last, per_page, []

    def get(self, url):
        self.urls.append(url)
        before = int(url.split("before=")[1]) if "before=" in url else self.last + 1
        ids = list(range(max(1, before - self.per_page), before))
        return page(ids, ids[0] if ids and ids[0] > 1 else None), url


def test_channel_name():
    assert [tgchannel.channel_name(x) for x in ["@science", "t.me/science", "https://t.me/s/science",
                                                "https://telegram.me/science/12", "t.me/+AbCdEf", "t.me/joinchat/x",
                                                "https://www.youtube.com/@science", "t.me/ab"]] == [
        "science", "science", "science", "science", None, None, None, None]
    assert tgchannel.is_channel_link("t.me/science") and not tgchannel.is_channel_link("@science")


def test_parse_page_skips_reply_quotes():
    p = tgchannel.parse_page(page([6, 7], older=6))
    assert (p.title, p.older, [m["id"] for m in p.messages]) == ("Science channel", 6, [6, 7])
    text, links = telegram.message_text(p.messages[1])
    assert links == ["https://example.com/a?x=1"]                 # t.me й цитата відповіді — ні
    assert "quoted" not in text and p.messages[1]["date"] == "2026-10-08T10:00:00"
    assert telegram.message_text(p.messages[0])[0].startswith("Why sleep matters.\nSleep")


def test_fetch_reads_in_parts_then_new_then_older():
    tg = FakeTelegram(last=8, per_page=2)
    sleeps = []
    title, msgs, state = tgchannel.fetch("science", {}, pages=2, get=tg.get, sleep=sleeps.append)
    assert title == "Science channel" and [m["id"] for m in msgs] == [5, 6, 7, 8]
    assert state == {"newest": 8, "oldest": 5, "done": False} and sleeps == [tgchannel.PAUSE]
    tg.last = 10                                                    # дві нові публікації
    _, msgs, state = tgchannel.fetch("science", state, pages=3, get=tg.get, sleep=sleeps.append)
    assert [m["id"] for m in msgs] == [3, 4, 7, 8, 9, 10]           # нові згори (до 8), далі старіші за 5
    assert state == {"newest": 10, "oldest": 3, "done": False}
    _, msgs, state = tgchannel.fetch("science", state, pages=3, get=tg.get, sleep=sleeps.append)
    assert [m["id"] for m in msgs] == [1, 2, 9, 10]                  # верхня сторінка й решта старого
    assert state == {"newest": 10, "oldest": 1, "done": True}


def test_private_channel_is_reported():
    with pytest.raises(tgchannel.ChannelError):
        tgchannel.fetch_page("secret", get=lambda url: ("<html></html>", "https://t.me/secret"))
    with pytest.raises(tgchannel.ChannelError):
        tgchannel.fetch_page("secret", get=lambda url: ("<html><body>nothing</body></html>", url))


def test_channel_source_sync_keeps_state(env, monkeypatch):
    tg = FakeTelegram(last=8, per_page=4)
    monkeypatch.setattr(tgchannel, "_get", tg.get)
    monkeypatch.setattr(tgchannel.time, "sleep", lambda s: None)
    sid = pipeline.add_channel_source(env.conn, "Science", ["science"], "TG")
    new, _ = pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)
    rows = env.conn.execute("SELECT kind, list_name, coalesce(url, key) FROM items WHERE source_id=? ORDER BY kind",
                            (sid,)).fetchall()
    assert [tuple(r) for r in rows] == [("article", "Science channel", "https://example.com/a?x=1"),
                                        ("post", "Science channel", "telegram:science:6"),
                                        ("video", "Science channel", "https://www.youtube.com/watch?v=vid00000005")]
    assert new == 3
    state = db.get_source(env.conn, sid)["settings"]["channel_state"]["science"]
    assert state == {"newest": 8, "oldest": 1, "done": True}
    assert any("posts read: 8" in line for line in env.logs)
    tg.last = 9
    assert pipeline.sync_source(env.conn, db.get_source(env.conn, sid), env.logs.append)[0] == 0


def test_cli_add_channel(paths, monkeypatch, capsys):
    from backlog_digest import cli

    assert cli.main(["add", "https://t.me/science", "t.me/s/science", "--no-sync"]) == 0
    source = db.list_sources(db.connect(paths.db_file))[0]
    assert (source["name"], source["type"], source["settings"]) == ("science", "telegram", {"channels": ["science"]})


def test_menu_add_channels(paths, monkeypatch):
    from backlog_digest import config, menu

    tg = FakeTelegram(last=3, per_page=5)
    monkeypatch.setattr(tgchannel, "_get", tg.get)
    monkeypatch.setattr(tgchannel, "fetch_page", lambda name, before=None, get=None: (
        tgchannel.Page("Science channel") if name == "science" else (_ for _ in ()).throw(tgchannel.ChannelError("x"))))
    answers = iter(["2", "t.me/science", "not a link", "t.me/secret", "", "", ""])
    monkeypatch.setattr(menu, "ask", lambda prompt="": next(answers))
    monkeypatch.setattr(pipeline, "sync_source", lambda conn, source, log=print: (0, 0))
    m = menu.Menu(paths, config.defaults(), db.connect(paths.db_file))
    m.add_telegram()
    source = db.list_sources(m.conn)[0]
    assert (source["name"], source["settings"]["channels"]) == ("Science channel", ["science"])
