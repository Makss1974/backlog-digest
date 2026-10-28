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
