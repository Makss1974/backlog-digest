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
    feed(monkeypatch, ["1", "", "2", "0", "", "4", "", "6", "0", "", "7", "", "42", "q"])
    make_menu(lang).run()
    out = capsys.readouterr().out
    for key in ("menu.stats", "menu.settings", "menu.quit", "menu.queue_empty", "stats.no_sources",
                "status.no_text", "menu.no_such_item", "sources.none", "sources.add_youtube"):
        assert i18n.t(key) in out, key
    assert i18n.t("schedule.no_jobs") in out and i18n.t("sort.no_lists") in out
    assert "DOCTOR-RAN" in out
    assert not re.search(r"\b(menu|stats|status|settings|sources|process)\.[a-z_]+", out), "untranslated key"


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
    feed(monkeypatch, ["8", "5", "0", "", "q"])
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


@pytest.fixture
def fake_youtube(monkeypatch):
    from backlog_digest import youtube

    listing = youtube.Listing("My list", [youtube.Entry("aaaaaaaaaaa", "Video A", "Chan", 60)])
    monkeypatch.setattr(youtube, "list_url", lambda url, cookies=None: listing)
    monkeypatch.setattr(youtube, "fetch_transcript",
                        lambda vid, langs: youtube.Transcript("[00:00] text", "en", False))


def test_add_youtube_source_from_menu(make_menu, monkeypatch, capsys, fake_youtube):
    # 2 — джерела, 1 — додати, посилання, порожній рядок, назва за замовчуванням, тека «Відео»
    feed(monkeypatch, ["2", "1", "https://www.youtube.com/playlist?list=PL1", "", "", "Відео", "0", "", "q"])
    m = make_menu()
    m.run()
    [s] = db.list_sources(m.conn)
    assert (s["name"], s["output_dir"], s["settings"]["urls"]) == (
        "YouTube", "Відео", ["https://www.youtube.com/playlist?list=PL1"])
    assert db.status_counts(m.conn)[db.NEW] == 1
    assert i18n.t("sources.synced", new=1) in capsys.readouterr().out


def test_add_folder_from_menu(make_menu, monkeypatch, tmp_path):
    (tmp_path / "Lib" / "Sub").mkdir(parents=True)
    (tmp_path / "Lib" / "Sub" / "b.md").write_text("Notes. " * 50, encoding="utf-8")
    feed(monkeypatch, ["2", "2", str(tmp_path / "Nope"), "2", str(tmp_path / "Lib"), "", "", "0", "", "q"])
    m = make_menu()
    m.run()
    [s] = db.list_sources(m.conn)
    assert (s["type"], s["name"], s["output_dir"]) == ("folder", "Lib", "Lib")
    assert m.conn.execute("SELECT list_name FROM items").fetchone()[0] == "Sub"


def test_delete_source_from_menu(make_menu, monkeypatch):
    m = make_menu()
    sid = db.add_source(m.conn, "youtube", "Old")
    db.upsert_item(m.conn, sid, "k")
    feed(monkeypatch, ["2", "6", "1", "yes", "0", "", "q"])
    m.run()
    assert db.list_sources(m.conn) == []


def test_process_from_menu_asks_for_output_folder(make_menu, monkeypatch, capsys, paths, tmp_path, fake_youtube):
    from backlog_digest import llm, pipeline

    class Chain:
        slots = [1]

        def active(self):
            return ["fake/m"]

        def complete(self, system, user, **kw):
            return llm.LLMResult('{"about": "A", "key_points": ["k"], "score": 4, "verdict": "watch"}', "fake/m")

        def has_watcher(self):
            return False

    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: Chain()))
    m = make_menu()
    pipeline.add_youtube_source(m.conn, "YouTube", ["x"], "Video")
    pipeline.sync_all(m.conn)
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    feed(monkeypatch, ["3", "", str(vault), "5", "", "q"])   # "" — режим за замовчуванням: усе разом
    m.run()
    assert config.load(paths)["output"] == {"kind": "obsidian", "path": str(vault)}
    assert (vault / "Video" / "My list" / "Video A.md").exists()


def test_model_key_is_checked_and_saved(make_menu, monkeypatch, capsys, paths):
    from backlog_digest import keys, llm

    monkeypatch.setattr(menu.getpass, "getpass", lambda prompt="": " sk-test ")
    monkeypatch.setattr(llm, "check_key", lambda provider, key: (key == "sk-test", ""))
    m = make_menu()
    m.cfg["models"]["order"] = ["gemini"]
    feed(monkeypatch, ["8", "4", "3", "0", "", "q"])     # 3 — DeepSeek
    m.run()
    assert keys.get_secret("DEEPSEEK_API_KEY", paths) == "sk-test"
    assert config.load(paths)["models"]["order"] == ["gemini", "deepseek"]
    assert i18n.t("settings.key_ok") in capsys.readouterr().out
    assert "sk-test" not in paths.config_file.read_text(encoding="utf-8")


def test_bad_model_key_is_not_saved_unless_confirmed(make_menu, monkeypatch, paths):
    from backlog_digest import keys, llm

    monkeypatch.setattr(menu.getpass, "getpass", lambda prompt="": "wrong")
    monkeypatch.setattr(llm, "check_key", lambda provider, key: (False, "API key not valid"))
    feed(monkeypatch, ["8", "4", "1", "n", "0", "", "q"])
    make_menu().run()
    assert keys.get_secret("GEMINI_API_KEY", paths) is None


def test_relative_output_path_is_rejected(make_menu, monkeypatch, paths):
    feed(monkeypatch, ["8", "3", "relative/folder", "0", "", "q"])
    make_menu().run()
    assert not paths.config_file.exists()


def test_add_bookmarks_with_folder_choice(make_menu, monkeypatch, tmp_path):
    (tmp_path / "b.html").write_text("""<DL><p>
<DT><H3>Base</H3><DL><p>
  <DT><H3>Science</H3><DL><p>
    <DT><H3>Physics</H3><DL><p><DT><A HREF="https://e.com/p">P</A></DL><p>
    <DT><A HREF="https://e.com/s">S</A>
  </DL><p>
  <DT><H3>Home</H3><DL><p><DT><A HREF="https://e.com/h">H</A></DL><p>
</DL><p>
<DT><H3>Other</H3><DL><p><DT><A HREF="https://e.com/o">O</A></DL><p>
</DL><p>""", encoding="utf-8")
    m = make_menu()
    # відкрити Base, вибрати Science (з Physics), спроба відкрити теку без ▸, назад, готово
    feed(monkeypatch, ["2", "3", str(tmp_path / "b.html"), "", ">1", "2", ">1", "<", "", "", "", "0", "q"])
    m.run()
    [source] = db.list_sources(m.conn)
    assert source["settings"].get("folders") == ["Base/Science"], source
    assert source["name"] == "Science"
    items = {tuple(r) for r in m.conn.execute("SELECT url, list_name FROM items")}
    assert items == {("https://e.com/p", "Science/Physics"), ("https://e.com/s", "Science")}
