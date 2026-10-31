from __future__ import annotations

import os
import subprocess
from datetime import datetime

import pytest

from backlog_digest import autostart, cli, menu, pipeline, runstate, schedule
from backlog_digest.paths import HOME_ENV
from test_menu import feed
from test_pipeline import FakeChain, env  # noqa: F401  (env — фікстура)


def job(**kw):
    return {"enabled": 1, "at_time": "09:00", "frequency": "daily", "last_run": None, **kw}


def at(text):
    return datetime.fromisoformat(text)


def test_daily_due_and_catch_up():
    # 2026-11-02 — понеділок
    assert not schedule.is_due(job(), at("2026-11-02T08:59"))
    assert schedule.is_due(job(), at("2026-11-02T09:00"))
    assert not schedule.is_due(job(last_run="2026-11-02T09:05"), at("2026-11-02T23:00"))
    assert not schedule.is_due(job(last_run="2026-11-02T09:05"), at("2026-11-03T08:00"))
    assert schedule.is_due(job(last_run="2026-11-02T09:05"), at("2026-11-03T09:01"))
    # комп'ютер був вимкнений три дні — наздоганяємо одним запуском
    assert schedule.is_due(job(last_run="2026-10-30T09:05"), at("2026-11-02T10:00"))
    assert not schedule.is_due(job(enabled=0), at("2026-11-02T10:00"))


def test_weekly_due():
    wed = job(frequency="weekly:2")
    assert not schedule.is_due(wed, at("2026-11-02T10:00"))                       # понеділок, нове завдання
    assert schedule.is_due(wed, at("2026-11-04T09:30"))                           # середа
    assert not schedule.is_due({**wed, "last_run": "2026-11-04T09:30"}, at("2026-11-08T12:00"))
    assert schedule.is_due({**wed, "last_run": "2026-11-04T09:30"}, at("2026-11-12T08:00"))  # пропустили середу
    anyday = job(frequency="weekly")
    assert schedule.is_due(anyday, at("2026-11-02T09:30"))
    assert not schedule.is_due({**anyday, "last_run": "2026-11-02T09:30"}, at("2026-11-07T10:00"))
    assert schedule.is_due({**anyday, "last_run": "2026-11-02T09:30"}, at("2026-11-09T10:00"))


def test_parse_time():
    assert [schedule.parse_time(x) for x in ("9", "9:5", "21.30", "24:00", "x")] == [
        "09:00", "09:05", "21:30", None, None]


def test_run_due_with_log_and_journal(env, paths, monkeypatch):
    env.cfg["youtube"].update(delay_min=0, delay_max=0)
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: FakeChain()))
    job_id = schedule.add_job(env.conn, "Videos", source_id=env.sid, lim=10, at_time="23:59")
    results = schedule.run_due(env.conn, env.cfg, paths, force=True, log=lambda _: None)
    assert [name for name, _ in results] == ["Videos"]
    assert "new summaries 3" in results[0][1] and "queued 0" in results[0][1]
    saved = schedule.get_job(env.conn, job_id)
    assert saved["last_run"] and saved["last_result"] == results[0][1]
    log = next((paths.data_dir / "logs").glob("schedule-*.log")).read_text(encoding="utf-8")
    assert "===== " in log and "Videos" in log
    journal = (env.root / "_Schedule log.md").read_text(encoding="utf-8")
    assert "| Videos | new summaries 3" in journal
    assert runstate.current(paths) is None and not (paths.data_dir / runstate.FILE).exists()


def test_run_due_waits_for_other_process(env, paths, monkeypatch):
    schedule.add_job(env.conn, "Videos", source_id=env.sid)
    monkeypatch.setattr(runstate, "current", lambda p: {"pid": 1, "started": "2026-11-02T09:00:00"})
    logs = []
    assert schedule.run_due(env.conn, env.cfg, paths, force=True, log=logs.append) == []
    assert "09:00" in logs[0]


def test_one_failing_job_does_not_stop_others(env, paths, monkeypatch):
    first = schedule.add_job(env.conn, "Broken", source_id=env.sid)
    schedule.add_job(env.conn, "Fine", source_id=env.sid, steps={"sync": True, "process": False})
    real = schedule.Runner.run_job

    def run_job(self, j):
        if j["id"] == first:
            raise RuntimeError("boom")
        return real(self, j)

    monkeypatch.setattr(schedule.Runner, "run_job", run_job)
    results = dict(schedule.run_due(env.conn, env.cfg, paths, force=True, log=lambda _: None))
    assert results["Broken"] == "error: boom" and "queued 3" in results["Fine"]


def test_menu_add_job(env, paths, monkeypatch):
    # джерело 1 → один список, тож без вибору списку → 5 за раз → без звіту → щотижня, п'ятниця → 7:30 →
    # без автозапуску
    feed(monkeypatch, ["1", "5", "no", "2", "5", "7:30", "no"])
    menu.Menu(paths, env.cfg, env.conn).add_job()
    (j,) = schedule.list_jobs(env.conn)
    assert (j["source_id"], j["lim"], j["frequency"], j["at_time"]) == (env.sid, 5, "weekly:4", "07:30")
    assert j["steps"] == {"sync": True, "process": True, "report": False, "raindrop": False}
    assert "Friday" in schedule.describe(env.conn, j)


def test_cli_home_option(paths, tmp_path, monkeypatch):
    home = tmp_path / "other-home"
    monkeypatch.setattr(cli, "run_command", lambda *a: 0)
    assert cli.main(["--home", str(home), "run-jobs"]) == 0
    assert os.environ[HOME_ENV] == str(home) and (home / "backlog.sqlite").exists()


def test_runstate_nested_keeps_marker(paths):
    runstate.start(paths, "schedule", "t")
    runstate.start(paths, "all", "t")
    runstate.finish(paths)
    assert (paths.data_dir / runstate.FILE).exists()      # розклад ще працює
    runstate.finish(paths)
    assert not (paths.data_dir / runstate.FILE).exists()


# ---------------------------------------------------------------- автозапуск

def test_command_passes_home(monkeypatch):
    monkeypatch.setenv(HOME_ENV, "D:/My Home")
    cmd = autostart.command()
    assert cmd[1:] == ["-m", "backlog_digest", "--home", "D:/My Home", "run-jobs"]


def test_windows_script_quotes():
    script = autostart.windows_script(["C:/O'Brien/pythonw.exe", "-m", "backlog_digest", "--home", "D:/a b",
                                       "run-jobs"])
    assert "-Execute 'C:/O''Brien/pythonw.exe'" in script
    assert """-Argument '-m backlog_digest --home "D:/a b" run-jobs'""" in script
    assert "-TaskName 'backlog-digest'" in script and "-MultipleInstances IgnoreNew" in script


def test_plist_and_systemd_units(tmp_path):
    text = autostart.plist(["/usr/bin/python3", "-m", "backlog_digest", "run-jobs"], tmp_path / "a&b.log")
    assert "<string>run-jobs</string>" in text and "<integer>3600</integer>" in text and "a&amp;b.log" in text
    units = autostart.systemd_units(["/home/u/my env/python", "-m", "backlog_digest", "run-jobs"])
    assert 'ExecStart="/home/u/my env/python" -m backlog_digest run-jobs' in units["backlog-digest.service"]
    assert "Persistent=true" in units["backlog-digest.timer"]


def test_cron_install_and_remove(monkeypatch):
    tab = {"text": "5 4 * * * backup\n"}
    calls = []

    def run(args, stdin=None):
        calls.append(args)
        if args == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0, tab["text"], "")
        if args == ["crontab", "-"]:
            tab["text"] = stdin
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setattr(autostart, "_run", run)
    monkeypatch.setattr(autostart, "_has_systemd", lambda: False)
    monkeypatch.setattr(autostart.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert autostart.install(autostart.Path("log")) == "crontab"
    autostart.install(autostart.Path("log"))                           # повторно — не дублюємо
    assert tab["text"].count(autostart.CRON_MARK) == 1 and tab["text"].startswith("5 4 * * * backup")
    assert autostart.installed()
    autostart.remove()
    assert tab["text"] == "5 4 * * * backup\n"


def test_windows_install_failure_is_reported(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart, "_run", lambda args, stdin=None: subprocess.CompletedProcess(
        args, 1, "", "Access is denied."))
    with pytest.raises(autostart.AutostartError, match="Access is denied"):
        autostart.install(autostart.Path("log"))
