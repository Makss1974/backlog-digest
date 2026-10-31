"""Автозапуск розкладу системним планувальником: щогодини й після входу в систему — `run-jobs`.

Windows — Планувальник завдань (без вікна: pythonw); macOS — launchd (LaunchAgent користувача);
Linux — таймер systemd користувача, а якщо systemd немає — cron. Права адміністратора не потрібні.
Що й коли робити, вирішує вже сама програма (schedule.py), тож системне завдання однакове для всіх.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import HOME_ENV

NAME = "backlog-digest"
LABEL = "io.github.backlog-digest"
CRON_MARK = "# backlog-digest run-jobs"
TIMEOUT = 60


class AutostartError(Exception):
    pass


def command() -> list[str]:
    """Чим запускати: той самий Python, що й зараз (venv чи uv tool), без вікна на Windows."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and (exe.parent / "pythonw.exe").exists():
        exe = exe.parent / "pythonw.exe"
    cmd = [str(exe), "-m", "backlog_digest"]
    home = os.environ.get(HOME_ENV)
    if home:
        cmd += ["--home", home]
    return [*cmd, "run-jobs"]


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise AutostartError(str(e)) from e


def _check(r: subprocess.CompletedProcess[str]) -> None:
    if r.returncode != 0:
        raise AutostartError((r.stderr or r.stdout).strip()[:500] or f"exit {r.returncode}")


# ---------------------------------------------------------------- Windows

def _ps(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def windows_script(cmd: list[str]) -> str:
    args = subprocess.list2cmdline(cmd[1:])
    return f"""
$action = New-ScheduledTaskAction -Execute {_ps(cmd[0])} -Argument {_ps(args)}
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Delay = 'PT5M'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName {_ps(NAME)} -Action $action -Trigger $hourly,$logon -Settings $settings `
  -Description 'backlog-digest: run-jobs' -Force | Out-Null
"""


# ---------------------------------------------------------------- macOS

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist(cmd: list[str], log: Path) -> str:
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    args = "\n".join(f"        <string>{esc(a)}</string>" for a in cmd)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{esc(str(log))}</string>
    <key>StandardErrorPath</key>
    <string>{esc(str(log))}</string>
</dict>
</plist>
"""


# ---------------------------------------------------------------- Linux

def systemd_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def systemd_units(cmd: list[str]) -> dict[str, str]:
    exec_line = " ".join(f'"{a}"' if " " in a else a for a in cmd)
    return {
        f"{NAME}.service": f"[Unit]\nDescription=backlog-digest: run-jobs\n\n[Service]\nType=oneshot\n"
                           f"ExecStart={exec_line}\n",
        f"{NAME}.timer": "[Unit]\nDescription=backlog-digest hourly\n\n[Timer]\nOnBootSec=5min\n"
                         "OnUnitActiveSec=1h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n",
    }


def cron_line(cmd: list[str]) -> str:
    return "0 * * * * " + " ".join(f"'{a}'" if " " in a else a for a in cmd) + f" >/dev/null 2>&1 {CRON_MARK}"


def _crontab() -> str:
    r = _run(["crontab", "-l"])
    return r.stdout if r.returncode == 0 else ""   # «no crontab for user» — порожній


def _has_systemd() -> bool:
    return bool(shutil.which("systemctl")) and _run(["systemctl", "--user", "show-environment"]).returncode == 0


# ---------------------------------------------------------------- спільне

def installed() -> bool:
    try:
        if sys.platform == "win32":
            return _run(["schtasks", "/Query", "/TN", NAME]).returncode == 0
        if sys.platform == "darwin":
            return plist_path().exists()
        return (systemd_dir() / f"{NAME}.timer").exists() or (bool(shutil.which("crontab"))
                                                               and CRON_MARK in _crontab())
    except AutostartError:
        return False


def install(log: Path) -> str:
    """Ставить (або оновлює) системне завдання. Повертає, куди саме: для повідомлення користувачу."""
    cmd = command()
    if sys.platform == "win32":
        _check(_run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", windows_script(cmd)]))
        return f"Task Scheduler: {NAME}"
    if sys.platform == "darwin":
        path = plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        _run(["launchctl", "unload", str(path)])      # старе — прибрати; помилку «не було» ігноруємо
        path.write_text(plist(cmd, log), encoding="utf-8")
        _check(_run(["launchctl", "load", "-w", str(path)]))
        return str(path)
    if _has_systemd():
        folder = systemd_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in systemd_units(cmd).items():
            (folder / name).write_text(text, encoding="utf-8")
        _check(_run(["systemctl", "--user", "daemon-reload"]))
        _check(_run(["systemctl", "--user", "enable", "--now", f"{NAME}.timer"]))
        return str(folder / f"{NAME}.timer")
    if not shutil.which("crontab"):
        raise AutostartError("systemd / cron")
    lines = [ln for ln in _crontab().splitlines() if CRON_MARK not in ln]
    _check(_run(["crontab", "-"], "\n".join([*lines, cron_line(cmd)]) + "\n"))
    return "crontab"


def remove() -> None:
    if sys.platform == "win32":
        r = _run(["schtasks", "/Delete", "/TN", NAME, "/F"])
        if r.returncode != 0 and installed():
            _check(r)
        return
    if sys.platform == "darwin":
        path = plist_path()
        if path.exists():
            _run(["launchctl", "unload", "-w", str(path)])
            path.unlink()
        return
    folder = systemd_dir()
    if (folder / f"{NAME}.timer").exists():
        _run(["systemctl", "--user", "disable", "--now", f"{NAME}.timer"])
        for name in systemd_units([]):
            (folder / name).unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
    if shutil.which("crontab"):
        text = _crontab()
        if CRON_MARK in text:
            lines = [ln for ln in text.splitlines() if CRON_MARK not in ln]
            _check(_run(["crontab", "-"], "\n".join(lines) + "\n" if lines else ""))
