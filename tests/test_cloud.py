from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backlog_digest import cloud, config, db, pipeline, tgchannel
from backlog_digest.paths import Paths
from test_pipeline import FakeChain, env, sync  # noqa: F401  (env — фікстура)
from test_tgchannel import FakeTelegram

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def test_utc_cron():
    kyiv = datetime(2026, 11, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert cloud.utc_cron("07:30", kyiv) == "30 5 * * *"
    assert cloud.utc_cron("01:00", kyiv) == "0 23 * * *"


def test_workflow_passes_inputs_through_env():
    text = cloud.workflow("0 5 * * *")
    assert 'cron: "0 5 * * *"' in text and "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in text
    assert '--limit "$LIMIT" --add "$ADD"' in text      # не ${{ inputs.add }} у самій команді
    assert "${{ inputs.add }}" in text.split("run: bdig")[0]


def make_cloud(env, tmp_path, copy_data=True):
    sync(env)
    proc = env.make()
    proc.run(1)                                   # один конспект у старому сховищі
    dest = cloud.init(tmp_path / "cloud", Paths(tmp_path / "home", tmp_path / "home"), env.cfg, env.conn,
                      copy_data=copy_data, at_time="07:00", log=lambda s: None)
    return dest


def test_init_copies_data_and_makes_paths_relative(env, tmp_path):
    dest = make_cloud(env, tmp_path)
    assert (dest / cloud.MARKER).exists() and (dest / cloud.WORKFLOW).exists() and (dest / "README.md").exists()
    assert ".env" in (dest / ".gitignore").read_text(encoding="utf-8")
    cfg = config.load(Paths(dest, dest))
    assert cfg["output"]["path"] == "notes" and cfg["youtube"]["no_subtitles"] == "gemini"
    assert "_cloud" not in cfg
    conn = sqlite3.connect(dest / "backlog.sqlite")
    (note,) = conn.execute("SELECT note_path FROM items WHERE status='done'").fetchone()
    conn.close()
    assert note.startswith("notes/Video/") and (dest / note).is_file()
    assert env.cfg["output"]["path"] == str(tmp_path / "vault")      # поточні налаштування не змінились
    if shutil.which("git"):
        assert (dest / ".git").is_dir()


def test_init_needs_empty_folder(env, tmp_path):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "x.txt").write_text("x")
    with pytest.raises(cloud.CloudError):
        cloud.init(tmp_path / "busy", Paths(tmp_path, tmp_path), env.cfg, env.conn)


def test_cloud_run_skips_disk_sources(env, tmp_path, monkeypatch):
    dest = make_cloud(env, tmp_path)
    books = tmp_path / "books"
    books.mkdir()
    (books / "a.txt").write_text("A book about sleep. " * 50, encoding="utf-8")
    home = Paths(dest, dest)
    monkeypatch.chdir(dest)
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, *a, **k: FakeChain()))
    monkeypatch.setattr(tgchannel, "_get", FakeTelegram(last=3, per_page=5).get)   # без мережі
    monkeypatch.setattr(tgchannel.time, "sleep", lambda s: None)
    conn = db.connect(home.db_file)
    sid = pipeline.add_folder_source(conn, "Books", str(books), "Books")
    pipeline.sync_source(conn, db.get_source(conn, sid), lambda s: None)
    shutil.rmtree(books)                               # у хмарі цієї теки немає
    cfg = config.load(home)
    logs: list[str] = []
    summary = cloud.run(conn, cfg, home, limit=10, add=["t.me/science", "not-a-link"], log=logs.append)
    rows = dict(conn.execute("SELECT kind, status FROM items WHERE kind IN ('text', 'video') "
                             "GROUP BY kind, status").fetchall())
    assert rows["text"] == "new"                        # файл з диска не чіпали
    assert conn.execute("SELECT COUNT(*) FROM items WHERE kind='video' AND status='done'").fetchone()[0] == 3
    notes = [r[0] for r in conn.execute("SELECT note_path FROM items WHERE note_path IS NOT NULL")]
    assert all(n.startswith("notes/") and (dest / n).is_file() for n in notes)
    assert db.list_sources(conn)[-1]["name"] == "Telegram (cloud)"
    assert summary == "new summaries 2, queued 1" and any("not-a-link" in line for line in logs)
    assert not any("folder" in line.lower() and "missing" in line.lower() for line in logs)
    conn.close()


def test_add_links_appends_to_existing_source(env):
    cloud.add_links(env.conn, ["https://www.youtube.com/watch?v=abcdefghijk"], log=lambda s: None)
    cloud.add_links(env.conn, ["https://www.youtube.com/watch?v=abcdefghijk",
                               "https://www.youtube.com/watch?v=zyxwvutsrqp", "https://example.com/a"],
                    log=lambda s: None)
    sources = {s["name"]: s for s in db.list_sources(env.conn)}
    assert sources["YouTube (cloud)"]["settings"]["urls"] == ["https://www.youtube.com/watch?v=abcdefghijk",
                                                              "https://www.youtube.com/watch?v=zyxwvutsrqp"]
    assert sources["Web (cloud)"]["settings"]["urls"] == ["https://example.com/a"]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@needs_git
def test_pull_and_push_between_two_computers(env, tmp_path):
    a = make_cloud(env, tmp_path)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    _git(a, "remote", "add", "origin", str(remote))
    _git(a, "push", "--quiet", "-u", "origin", "main")
    b = tmp_path / "b"
    _git(tmp_path, "clone", "--quiet", str(remote), str(b))
    logs: list[str] = []

    (b / "notes" / "new.md").write_text("from the cloud", encoding="utf-8")
    assert cloud.push(b, "cloud run", logs.append)
    assert cloud.pull(a, logs.append) and (a / "notes" / "new.md").read_text(encoding="utf-8") == "from the cloud"

    # базу змінили в обох місцях: беремо хмарну, локальна лишається в історії git
    for where, value in ((b, "cloud"), (a, "laptop")):
        c = sqlite3.connect(where / "backlog.sqlite")
        c.execute("INSERT INTO fetch_log (day, requests) VALUES (?, 1)", (value,))
        c.commit()
        c.close()
    assert cloud.push(b, "cloud run", logs.append)
    (a / "notes" / "local.md").write_text("laptop note", encoding="utf-8")
    assert cloud.push(a, "laptop", logs.append)
    days = [r[0] for r in sqlite3.connect(a / "backlog.sqlite").execute("SELECT day FROM fetch_log")]
    assert "cloud" in days and "laptop" not in days and (a / "notes" / "local.md").exists()
    assert any("cloud one is kept" in line for line in logs)
    assert cloud.pull(b, logs.append) and (b / "notes" / "local.md").exists()


def test_cli_cloud_commands(tmp_path, monkeypatch, capsys):
    from backlog_digest import cli

    assert cli.main(["cloud", "status"]) == 2
    assert "not a cloud folder" in capsys.readouterr().err
    assert cli.main(["cloud", "init", str(tmp_path / "c"), "--empty", "--time", "6:15"]) == 0
    assert "New repository" in capsys.readouterr().out
    dest = tmp_path / "c"
    assert cloud.is_cloud(Paths(dest, dest)) and (dest / "notes").is_dir()
    assert cli.main(["--home", str(dest), "cloud", "status"]) == 0
    assert "Linked to GitHub: no" in capsys.readouterr().out
    assert Path.cwd() == dest                            # відносні шляхи — від хмарної теки


def test_relative_home_works_after_chdir(tmp_path, monkeypatch, capsys):
    from backlog_digest import cli

    monkeypatch.chdir(tmp_path)
    assert cli.main(["cloud", "init", "c", "--empty"]) == 0
    capsys.readouterr()
    assert cli.main(["--home", "c", "cloud", "status"]) == 0
    assert "Linked to GitHub: no" in capsys.readouterr().out
    assert not (tmp_path / "c" / "c").exists()
