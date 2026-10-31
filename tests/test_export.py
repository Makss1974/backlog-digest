from __future__ import annotations

import pytest

from backlog_digest import cli, db, export, menu, notes, raindrop
from test_library import summarize_all
from test_menu import feed
from test_pipeline import env  # noqa: F401  (env — фікстура)

TOKEN = "test-token-123"


class FakeRaindrop:
    """Raindrop у пам'яті: колекції, закладки, кошик. Рахує запити."""

    def __init__(self):
        self.cols = {7: {"_id": 7, "title": "My bookmarks"}}     # чужа колекція — її не чіпаємо
        self.drops = {}
        self.next = 1000
        self.calls = []

    def new_id(self):
        self.next += 1
        return self.next

    def children(self, cid):
        out = {cid}
        while True:
            more = {c["_id"] for c in self.cols.values() if (c.get("parent") or {}).get("$id") in out} - out
            if not more:
                return out
            out |= more

    def __call__(self, token, method, path, params=None, body=None, *, retries=2):
        assert token == TOKEN
        self.calls.append((method, path))
        if (method, path) == ("GET", "/user"):
            return {"user": {"fullName": "Test User"}}
        if (method, path) == ("GET", "/collections"):
            return {"items": [c for c in self.cols.values() if not c.get("parent")]}
        if (method, path) == ("GET", "/collections/childrens"):
            return {"items": [c for c in self.cols.values() if c.get("parent")]}
        if (method, path) == ("POST", "/collection"):
            cid = self.new_id()
            self.cols[cid] = {"_id": cid, **body}
            return {"item": self.cols[cid]}
        if method == "GET" and path.startswith("/raindrops/"):
            inside = self.children(int(path.rsplit("/", 1)[1]))
            items = [d for d in self.drops.values() if d["collection"]["$id"] in inside]
            page, per = int(params["page"]), int(params["perpage"])
            return {"items": items[page * per:(page + 1) * per]}
        if (method, path) == ("POST", "/raindrops"):
            out = []
            for it in body["items"]:
                rid = self.new_id()
                self.drops[rid] = {"_id": rid, **it}
                out.append(self.drops[rid])
            return {"items": out}
        if method == "PUT" and path.startswith("/raindrop/"):
            rid = int(path.rsplit("/", 1)[1])
            if rid not in self.drops:
                raise raindrop.NotFound("404")
            self.drops[rid].update(body)
            return {"item": self.drops[rid]}
        raise AssertionError((method, path))

    def tree(self):
        """Шляхи колекцій -> назви закладок у них."""
        def path(c):
            p = (c.get("parent") or {}).get("$id")
            return f"{path(self.cols[p])}/{c['title']}" if p else c["title"]
        out = {}
        for d in self.drops.values():
            out.setdefault(path(self.cols[d["collection"]["$id"]]), []).append(d["title"])
        return {k: sorted(v) for k, v in out.items()}


@pytest.fixture
def fake(monkeypatch):
    api = FakeRaindrop()
    monkeypatch.setattr(raindrop, "call", api)
    return api


def run_export(env, paths, root="Backlog digest"):
    labels = notes.load_labels(paths, "en")
    return export.export(env.conn, TOKEN, [(env.sid, "Playlist")], root, "en", labels, log=lambda _: None)


def test_export_nested_collections_and_no_duplicates(env, paths, fake):
    summarize_all(env)
    ids = [r[0] for r in env.conn.execute("SELECT id FROM items ORDER BY position")]
    env.conn.execute("UPDATE items SET topic='Science/Space' WHERE id=?", (ids[0],))
    env.conn.execute("UPDATE items SET topic='Life' WHERE id IN (?, ?)", (ids[1], ids[2]))
    env.conn.commit()
    stats = run_export(env, paths)
    assert (stats.created, stats.updated, stats.collections) == (3, 0, 5)
    assert fake.tree() == {"Backlog digest/Playlist/Science/Space": ["Video 1"],
                           "Backlog digest/Playlist/Life": ["Video 2", "Video 3"]}
    drop = next(d for d in fake.drops.values() if d["title"] == "Video 1")
    assert drop["link"] == "https://www.youtube.com/watch?v=vid00000001"
    assert drop["excerpt"] == "What it is about." and drop["tags"] == ["t"]
    assert "3/5 ★★★☆☆" in drop["note"] and "- k1" in drop["note"]
    assert fake.cols[7] == {"_id": 7, "title": "My bookmarks"}

    # вдруге — нічого не надсилаємо
    fake.calls.clear()
    stats = run_export(env, paths)
    assert (stats.created, stats.updated, stats.unchanged) == (0, 0, 3)
    assert not [c for c in fake.calls if c[0] in ("POST", "PUT")]

    # тема змінилась — закладка переїжджає; видалену в Raindrop — додаємо знову
    env.conn.execute("UPDATE items SET topic='Life' WHERE id=?", (ids[0],))
    env.conn.commit()
    gone = next(rid for rid, d in fake.drops.items() if d["title"] == "Video 2")
    del fake.drops[gone]
    stats = run_export(env, paths)
    assert (stats.created, stats.updated, stats.unchanged, stats.collections) == (1, 1, 1, 0)
    assert fake.tree()["Backlog digest/Playlist/Life"] == ["Video 1", "Video 2", "Video 3"]
    assert len(fake.drops) == 3


def test_deleted_collection_is_recreated(env, paths, fake):
    summarize_all(env)
    run_export(env, paths)
    fake.cols.clear()       # користувач видалив усю колекцію (закладки пішли в кошик)
    fake.drops.clear()
    stats = run_export(env, paths)
    assert (stats.created, stats.collections) == (3, 2)
    assert fake.tree() == {"Backlog digest/Playlist": ["Video 1", "Video 2", "Video 3"]}


def test_export_root_is_not_imported_back(env, paths, fake):
    summarize_all(env)
    run_export(env, paths)
    assert db.export_roots(env.conn) == [next(c for c, v in fake.cols.items() if v["title"] == "Backlog digest")]


def test_menu_export(env, paths, fake, monkeypatch):
    summarize_all(env)
    monkeypatch.setenv(raindrop.TOKEN, TOKEN)
    monkeypatch.setattr(menu.getpass, "getpass", lambda prompt="": "")
    # список 1 → збережений токен → своя коренева колекція → так
    feed(monkeypatch, ["1", "Digest", "yes"])
    menu.Menu(paths, env.cfg, env.conn).export_raindrop()
    assert list(fake.tree()) == ["Digest/Playlist"]
    assert env.cfg["raindrop"]["export_root"] == "Digest"


def test_cli_export(env, paths, fake, monkeypatch, capsys):
    summarize_all(env)
    assert cli.run_command(cli.build_parser().parse_args(["export"]), paths, env.cfg, env.conn) == 2
    monkeypatch.setenv(raindrop.TOKEN, TOKEN)
    assert cli.run_command(cli.build_parser().parse_args(["export", "--root", "R"]), paths, env.cfg,
                           env.conn) == 0
    assert list(fake.tree()) == ["R/Playlist"]
    assert "added 3" in capsys.readouterr().out
