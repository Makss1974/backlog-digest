from __future__ import annotations

import builtins

import pytest

from backlog_digest import __version__, cli, config, doctor


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(doctor, "_can_connect", lambda host, timeout=4.0: True)


def quit_menu(monkeypatch):
    answers = iter(["q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))


def test_first_run_creates_config_and_shows_menu(paths, monkeypatch, capsys):
    quit_menu(monkeypatch)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert str(paths.config_file) in out and "Quit" in out
    assert paths.config_file.exists() and paths.db_file.exists()


@pytest.mark.parametrize(("lang", "word"), [("uk", "Вихід"), ("en", "Quit"), ("fr", "Quitter"), ("es", "Salir")])
def test_lang_flag(monkeypatch, capsys, lang, word):
    quit_menu(monkeypatch)
    assert cli.main(["--lang", lang]) == 0
    assert word in capsys.readouterr().out


def test_lang_flag_does_not_change_saved_config(paths, monkeypatch):
    config.ensure(paths)
    quit_menu(monkeypatch)
    cli.main(["--lang=es", "menu"])
    assert config.load(paths)["general"]["ui_language"] == "en"


def test_language_from_config(paths, monkeypatch, capsys):
    cfg = config.defaults()
    cfg["general"]["ui_language"] = "fr"
    config.save(paths, cfg)
    assert cli.main(["doctor", "--offline"]) == 0
    assert "diagnostic" in capsys.readouterr().out


def test_doctor_does_not_create_config(paths, capsys):
    assert cli.main(["doctor"]) == 0
    assert not paths.config_file.exists()


def test_broken_config(paths, capsys):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text("[general]\nui_language = 'ru'\n", encoding="utf-8")
    assert cli.main([]) == 2
    assert "doctor" in capsys.readouterr().err
    assert cli.main(["doctor", "--offline"]) == 1


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize("lang", ["uk", "fr"])
def test_help_is_translated(capsys, lang):
    with pytest.raises(SystemExit):
        cli.main(["--lang", lang, "--help"])
    out = capsys.readouterr().out
    assert {"uk": "перевірити", "fr": "vérifier"}[lang] in out


def test_add_folder_command(paths, capsys, tmp_path):
    from backlog_digest import db
    from samples import PARA

    (tmp_path / "Books").mkdir()
    (tmp_path / "Books" / "a.txt").write_text(PARA, encoding="utf-8")
    assert cli.main(["add", str(tmp_path / "Books")]) == 0
    conn = db.connect(paths.db_file)
    [source] = db.list_sources(conn)
    assert (source["type"], source["name"], source["settings"]["path"]) == ("folder", "Books", str(tmp_path / "Books"))
    assert db.status_counts(conn)[db.NEW] == 1
    conn.close()


def test_add_sync_process_commands(paths, monkeypatch, capsys, tmp_path):
    from backlog_digest import pipeline, youtube

    listing = youtube.Listing("L", [youtube.Entry("aaaaaaaaaaa", "A")])
    monkeypatch.setattr(youtube, "list_url", lambda url, cookies=None: listing)
    assert cli.main(["add", "https://www.youtube.com/@x", "--name", "X", "--no-sync"]) == 0
    assert cli.main(["add", "https://www.youtube.com/@y", "--name", "X"]) == 2      # назва зайнята
    assert cli.main(["sync"]) == 0
    assert "1" in capsys.readouterr().out
    runs = []

    class FakeProcessor:
        def __init__(self, conn, cfg, paths):
            pass

        def run(self, limit, source_id, stage):
            runs.append((limit, source_id, stage))
            return pipeline.RunStats(stopped="quota" if source_id else "")

    monkeypatch.setattr(pipeline, "Processor", FakeProcessor)
    assert cli.main(["process"]) == 0
    assert cli.main(["process", "--limit", "5", "--source", "1"]) == 3
    assert cli.main(["fetch", "--limit", "7"]) == 0
    assert cli.main(["summarize"]) == 0
    assert runs == [(30, None, "all"), (5, 1, "all"), (7, None, "fetch"), (30, None, "summarize")]
