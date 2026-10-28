from __future__ import annotations

import builtins
import re

import pytest

from backlog_digest import config, db, doctor, i18n, menu


def feed(monkeypatch, answers):
    """Підставляє відповіді користувача замість input(); після останньої — EOF (як Ctrl+Z)."""
    it = iter(answers)

    def fake_input(prompt=""):
        print(prompt, end="")
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", fake_input)


@pytest.fixture
def make_menu(paths):
    conns = []

    def factory(lang="en"):
        cfg = config.defaults()
        cfg["general"].update(ui_language=lang, summary_language=lang)
        i18n.set_language(lang)
        conn = db.connect(paths.db_file)
        conns.append(conn)
        return menu.Menu(paths, cfg, conn)

    yield factory
    for c in conns:
        c.close()


@pytest.mark.parametrize("lang", i18n.UI_LANGUAGES)
def test_menu_in_every_language(make_menu, monkeypatch, capsys, lang):
    monkeypatch.setattr(doctor, "run", lambda paths, network=True: print("DOCTOR-RAN"))
    feed(monkeypatch, ["1", "", "2", "", "7", "", "42", "q"])
    make_menu(lang).run()
    out = capsys.readouterr().out
    for key in ("menu.stats", "menu.settings", "menu.quit", "menu.queue_empty", "stats.no_sources",
                "status.no_text", "menu.no_such_item"):
        assert i18n.t(key) in out, key
    assert i18n.t("menu.coming_soon", stage=2) in out
    assert "DOCTOR-RAN" in out
    assert not re.search(r"(menu|stats|status|settings)\.[a-z_]+", out), "untranslated key"


@pytest.mark.parametrize("quit_key", ["q", "Q", "й"])
def test_quit_keys(make_menu, monkeypatch, quit_key):
    feed(monkeypatch, [quit_key, "1"])   # "1" не має бути прочитано
    make_menu().run()


def test_eof_exits(make_menu, monkeypatch):
    feed(monkeypatch, [])
    make_menu().run()


def test_queue_line_counts(make_menu):
    m = make_menu()
    sid = db.add_source(m.conn, "folder", "Books")
    for k in "abc":
        db.upsert_item(m.conn, sid, k)
    db.mark_done(m.conn, 1)
    assert m.queue_line() == i18n.t("menu.queue", done=1, total=3, queued=2)


def test_change_ui_language_applies_immediately_and_is_saved(make_menu, monkeypatch, capsys, paths):
    # 8 — налаштування, 1 — мова інтерфейсу, 3 — Français, 0 — назад, q — вихід
    feed(monkeypatch, ["8", "1", "3", "0", "", "q"])
    make_menu("uk").run()
    out = capsys.readouterr().out
    assert "Quitter" in out
    assert config.load(paths)["general"]["ui_language"] == "fr"
    assert config.load(paths)["general"]["summary_language"] == "uk"   # мова конспектів окремо


def test_change_summary_language_custom_code(make_menu, monkeypatch, paths):
    feed(monkeypatch, ["8", "2", "0", "pt-BR", "0", "", "q"])
    make_menu().run()
    assert config.load(paths)["general"]["summary_language"] == "pt-BR"


def test_invalid_summary_language_is_rejected(make_menu, monkeypatch, capsys, paths):
    feed(monkeypatch, ["8", "2", "0", "not a code", "0", "", "q"])
    make_menu().run()
    assert not paths.config_file.exists()
    assert "not a code" in capsys.readouterr().out


def test_show_paths(make_menu, monkeypatch, capsys, paths):
    feed(monkeypatch, ["8", "3", "0", "", "q"])
    make_menu().run()
    assert str(paths.env_file) in capsys.readouterr().out


def test_ask_int_reprompts(monkeypatch, capsys):
    feed(monkeypatch, ["abc", "9", "\ufeff2"])
    assert menu.ask_int("N", 1, 1, 3) == 2
    assert capsys.readouterr().out.count(i18n.t("menu.enter_number", lo=1, hi=3)) == 2


@pytest.mark.parametrize(("lang", "answer"), [("uk", "так"), ("en", "yes"), ("fr", "oui"), ("es", "sí")])
def test_ask_yes_per_language(monkeypatch, lang, answer):
    i18n.set_language(lang)
    feed(monkeypatch, [answer, "nope"])
    assert menu.ask_yes("?") is True
    assert menu.ask_yes("?") is False


def test_pick_summary_language_from_list(make_menu, monkeypatch, capsys, paths):
    n = list(i18n.SUMMARY_LANGUAGES).index("sv") + 1
    feed(monkeypatch, ["8", "2", str(n), "0", "", "q"])
    make_menu().run()
    assert config.load(paths)["general"]["summary_language"] == "sv"
    out = capsys.readouterr().out
    assert "Svenska (sv)" in out and "Čeština (cs)" in out
