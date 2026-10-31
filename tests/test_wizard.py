from __future__ import annotations

from backlog_digest import autostart, config, keys, llm, menu, pipeline, schedule, wizard, youtube
from test_menu import feed, make_menu  # noqa: F401  (make_menu — фікстура)
from test_pipeline import FakeChain


def fake_world(monkeypatch):
    """Ключ «перевіряється», YouTube віддає плейлист і субтитри, модель — підроблена, планувальник — ні."""
    monkeypatch.setattr(menu.getpass, "getpass", lambda prompt="": "AIza-test-key")
    monkeypatch.setattr(llm, "check_key", lambda provider, key: (True, ""))
    listing = youtube.Listing("Playlist", [youtube.Entry(f"vid{i:08d}", f"Video {i}", "Chan", 60) for i in (1, 2)])
    monkeypatch.setattr(youtube, "list_url", lambda url, cookies=None: listing)
    monkeypatch.setattr(youtube, "fetch_transcript", lambda vid, langs: youtube.Transcript("[00:00] text", "en", True))
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: FakeChain()))
    installed = []
    monkeypatch.setattr(autostart, "install", lambda log: installed.append(log) or "Task Scheduler: backlog-digest")
    return installed


def test_first_run_from_zero_to_summary(make_menu, paths, tmp_path, monkeypatch, capsys):
    installed = fake_world(monkeypatch)
    m = make_menu()
    m.cfg["youtube"].update(delay_min=0, delay_max=0)
    notes_dir = tmp_path / "My notes"
    opened = []
    monkeypatch.setattr(menu, "open_file", lambda path: opened.append(path))
    feed(monkeypatch, [
        "yes",                                                     # пройти майстер
        "", "yes",                                                 # мова — як є (English), конспекти нею ж
        str(notes_dir),                                            # тека
        "",                                                        # ключ: 1 (Gemini) за замовчуванням
        "", "https://www.youtube.com/playlist?list=PL1", "", "", "",   # джерело YouTube, назва й тека — як є
        "5",                                                       # на день
        "yes", "8",                                                # розклад щодня з 08:00
        "yes", "yes",                                              # перший конспект зараз, відкрити його
        "q",
    ])
    m.run(offer_wizard=True)
    out = capsys.readouterr().out
    assert "Step 6 of 6" in out and "aistudio.google.com" in out
    cfg = config.load(paths)
    assert cfg["output"]["path"] == str(notes_dir) and cfg["output"]["kind"] == "markdown"
    assert cfg["limits"]["per_run"] == cfg["limits"]["per_day"] == 5
    assert keys.get_secret("GEMINI_API_KEY", paths) == "AIza-test-key"
    (job,) = schedule.list_jobs(m.conn)
    assert (job["source_id"], job["lim"], job["at_time"], job["frequency"]) == (None, 5, "08:00", "daily")
    assert installed
    assert len(opened) == 1 and opened[0].exists() and opened[0].is_relative_to(notes_dir)


def test_wizard_offered_only_on_first_run(make_menu, monkeypatch, tmp_path):
    m = make_menu()
    assert wizard.is_first_run(m)
    m.cfg["output"]["path"] = str(tmp_path)
    assert not wizard.is_first_run(m)
    feed(monkeypatch, ["q"])     # пропозиції немає — одразу меню
    m.run(offer_wizard=True)


def test_wizard_everything_skipped(make_menu, paths, monkeypatch):
    m = make_menu()
    monkeypatch.setattr(wizard, "default_output", lambda: paths.data_dir / "notes")
    # мова як є, конспекти тією ж; тека — запропонована; ключ — назад; джерело — пропустити; на день — як є
    feed(monkeypatch, ["", "yes", "", "0", "0", ""])
    wizard.run(m)
    assert m.cfg["output"]["path"] == str(paths.data_dir / "notes") and (paths.data_dir / "notes").is_dir()
    assert schedule.list_jobs(m.conn) == []      # без джерел розклад не пропонуємо
