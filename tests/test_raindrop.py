from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from backlog_digest import cli, config, db, keys, menu, pipeline, raindrop

TOKEN = "test-token-123"
COLLECTIONS = [{"_id": 1, "title": "Наука", "count": 3}, {"_id": 2, "title": "Read later", "count": 1}]
CHILDREN = [{"_id": 11, "title": "Фізика", "count": 1, "parent": {"$id": 1}},
            {"_id": 2, "title": "Read later", "count": 1}]   # Raindrop буває повторює корінь тут


def drop(n, link, cid, type_="article"):
    return {"_id": n, "link": link, "title": f"Title {n}", "type": type_, "collection": {"$id": cid}}


# 51 закладка: перевіряємо, що читаються всі сторінки
DROPS = ([drop(1, "https://a.example/one", 1), drop(2, "https://a.example/two", 11),
          drop(3, "https://a.example/pic.png", 1, "image"), drop(4, "https://youtu.be/vid00000001", 2, "video"),
          drop(5, "https://a.example/loose", -1)]
         + [drop(100 + i, f"https://b.example/{i}", 2) for i in range(46)])


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def api(monkeypatch):
    """Підроблений Raindrop: перевіряє токен і віддає колекції та закладки посторінково."""
    calls = []

    def urlopen(req, timeout=0):
        url = urllib.parse.urlsplit(req.full_url)
        calls.append(url.path)
        if req.get_header("Authorization") != f"Bearer {TOKEN}":
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path.endswith("/user"):
            body = {"result": True, "user": {"fullName": "Test User"}}
        elif url.path.endswith("/collections"):
            body = {"result": True, "items": COLLECTIONS}
        elif url.path.endswith("/collections/childrens"):
            body = {"result": True, "items": CHILDREN}
        else:
            page, per = int(q["page"]), int(q["perpage"])
            body = {"result": True, "items": DROPS[page * per:(page + 1) * per]}
        return Response(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(raindrop.urllib.request, "urlopen", urlopen)
    return calls


def test_collections_get_nested_paths(api):
    cols = raindrop.collections(TOKEN)
    assert [(c.id, c.title) for c in cols] == [(2, "Read later"), (1, "Наука"), (11, "Наука/Фізика")]
    assert raindrop.descendants(cols, [1]) == {1, 11}


def test_links_read_all_pages_and_skip_images(api):
    links = raindrop.links(TOKEN)
    assert len(links) == 50   # 51 мінус зображення
    assert sum(p.endswith("/raindrops/0") for p in api) == 2
    by_url = {lk.url: lk.list_name for lk in links}
    assert by_url["https://a.example/two"] == "Наука/Фізика" and by_url["https://a.example/loose"] == ""
    only = raindrop.links(TOKEN, [1])   # колекція разом із вкладеною
    assert [lk.url for lk in only] == ["https://a.example/one", "https://a.example/two"]


def test_bad_token(api):
    with pytest.raises(raindrop.AuthError):
        raindrop.check_token("wrong")
    assert raindrop.check_token(TOKEN) == "Test User"


def test_sync_raindrop_source(api, paths, monkeypatch):
    conn = db.connect(paths.db_file)
    sid = pipeline.add_raindrop_source(conn, "Raindrop", [])
    logs: list[str] = []
    assert pipeline.sync_source(conn, db.get_source(conn, sid), logs.append) == (0, 0)
    assert "RAINDROP_TOKEN" in logs[0]           # без токена — підказка, без падіння
    monkeypatch.setenv(raindrop.TOKEN, TOKEN)
    assert pipeline.sync_source(conn, db.get_source(conn, sid), logs.append) == (50, 50)
    kinds = dict(conn.execute("SELECT url, kind FROM items WHERE source_id=?", (sid,)).fetchall())
    assert kinds["https://www.youtube.com/watch?v=vid00000001"] == "video"
    assert kinds["https://a.example/one"] == "article"
    assert pipeline.sync_source(conn, db.get_source(conn, sid), logs.append) == (0, 50)   # повтор — нічого нового
    assert not any(TOKEN in line for line in logs)
    conn.close()


def test_menu_adds_raindrop_and_saves_token(api, paths, monkeypatch, capsys):
    from test_menu import feed

    monkeypatch.setattr(menu.getpass, "getpass", lambda prompt="": TOKEN)
    cfg = config.defaults()
    conn = db.connect(paths.db_file)
    m = menu.Menu(paths, cfg, conn)
    feed(monkeypatch, ["2", "4", "3", "", "", "0", "q"])   # колекції за абеткою: 3 — «Наука/Фізика»
    m.run()
    out = capsys.readouterr().out
    assert TOKEN not in out and "Test User" in out
    assert keys.read_env_file(paths.env_file)[raindrop.TOKEN] == TOKEN
    [source] = db.list_sources(conn)
    assert (source["type"], source["name"], source["settings"]) == ("raindrop", "Фізика", {"collections": [11]})
    assert db.status_counts(conn)[db.NEW] == 1
    conn.close()


def test_cli_add_raindrop(api, paths, monkeypatch, capsys):
    assert cli.main(["add", "raindrop"]) == 2                        # токена немає
    monkeypatch.setenv(raindrop.TOKEN, TOKEN)
    assert cli.main(["add", "raindrop", "Немає такої"]) == 2
    assert cli.main(["add", "raindrop", "наука", "--folder", "RD"]) == 0
    conn = db.connect(paths.db_file)
    [source] = db.list_sources(conn)
    assert (source["name"], source["output_dir"], source["settings"]) == ("наука", "RD", {"collections": [1]})
    assert db.status_counts(conn)[db.NEW] == 2
    conn.close()
    assert TOKEN not in capsys.readouterr().out


def test_links_skip_export_collection(api):
    links = raindrop.links(TOKEN, exclude=[1])   # разом із вкладеною «Фізика»
    assert not {"https://a.example/one", "https://a.example/two"} & {lk.url for lk in links}


def test_call_sends_json(monkeypatch):
    sent = {}

    def urlopen(req, timeout=0):
        sent.update(method=req.get_method(), body=json.loads(req.data), type=req.get_header("Content-type"))
        return Response(json.dumps({"result": True, "item": {"_id": 5}}).encode("utf-8"))

    monkeypatch.setattr(raindrop.urllib.request, "urlopen", urlopen)
    assert raindrop.call(TOKEN, "POST", "/collection", body={"title": "Тест"})["item"]["_id"] == 5
    assert sent == {"method": "POST", "body": {"title": "Тест"}, "type": "application/json"}
