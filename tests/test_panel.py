from __future__ import annotations

import json
import os

from backlog_digest import config, db, llm, menu, runstate, shortcut


def make(paths):
    cfg = config.defaults()
    cfg["general"].update(ui_language="en", summary_language="en")
    return menu.Menu(paths, cfg, db.connect(paths.db_file))


def test_panel_shows_queue_sources_models_and_running(paths, monkeypatch):
    m = make(paths)
    sid = db.add_source(m.conn, "raindrop", "RD")
    db.upsert_item(m.conn, sid, "url:a", kind="article")
    db.upsert_item(m.conn, sid, "youtube:v", kind="video")
    lines = m.panel()
    assert lines[0] == "📦 0 of 2 done, 2 in queue"
    assert "waiting for subtitles: 1 (0 of 30" in lines[1] and "ready to summarize: 1" in lines[1]
    assert lines[2] == "📥 Sources: 1 (Raindrop 1)" and "no folder for summaries" in lines[3].lower()
    assert "no key" in lines[4]
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    names, _ = llm.model_status(m.cfg, paths)
    (paths.data_dir / "quota.json").write_text(json.dumps({"day": llm.quota_day(), "exhausted": names[:1]}))
    line = m.panel()[4]
    assert f"{len(names) - 1} of {len(names)} available" in line and names[0] in line
    # обробка в іншому процесі
    monkeypatch.setattr(runstate, "alive", lambda pid: True)
    (paths.data_dir / runstate.FILE).write_text(json.dumps({"pid": os.getpid() + 1, "started": "2026-10-30T23:10:00",
                                                            "stage": "fetch"}))
    assert m.panel()[-1] == "🟢 Running now: collecting subtitles, since 23:10 (another window or the schedule)"
    m.conn.close()


def test_runstate_marks_and_clears(paths, monkeypatch):
    runstate.start(paths, "all", "2026-10-30T10:00:00")
    assert runstate.current(paths) is None            # це наш процес — не «інше вікно»
    runstate.finish(paths)
    assert not (paths.data_dir / runstate.FILE).exists()
    (paths.data_dir / runstate.FILE).write_text(json.dumps({"pid": 999999999, "stage": "all"}))
    assert runstate.current(paths) is None            # процесу вже немає — позначка застаріла


def test_posix_shortcut(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut, "desktop", lambda: tmp_path)
    monkeypatch.setattr(shortcut.sys, "platform", "linux")
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    link = shortcut._posix(paths, tmp_path / "bdig", "", "backlog-digest")
    assert link.name == "backlog-digest.desktop" and "Terminal=true" in link.read_text(encoding="utf-8")
    assert "bdig" in (paths.config_dir / "backlog-digest.sh").read_text(encoding="utf-8")
