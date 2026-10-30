"""Ярлик на робочому столі: програма відкривається у власному вікні, одразу з меню й панеллю даних.

Ярлик запускає невеликий файл запуску в теці налаштувань (backlog-digest.cmd / .sh): він задає назву вікна,
переносить BACKLOG_DIGEST_HOME (якщо задано) і, коли програма завершилась з помилкою, не закриває вікно одразу —
щоб було видно, що сталося.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import HOME_ENV, Paths

NAME = "backlog-digest"
UTF8 = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "   # інакше PowerShell віддає кирилицю в кодуванні OEM


class ShortcutError(Exception):
    pass


def program() -> Path:
    """Шлях до bdig: поруч із поточним Python (віртуальне середовище, uv tool) або в PATH."""
    scripts = Path(sys.executable).parent
    for name in ("bdig.exe", "bdig"):
        if (scripts / name).exists():
            return scripts / name
    found = shutil.which("bdig")
    if not found:
        raise ShortcutError("bdig")
    return Path(found)


def desktop() -> Path:
    if sys.platform == "win32":   # робочий стіл може бути в OneDrive і мати назву мовою системи
        out = subprocess.run(["powershell", "-NoProfile", "-Command", UTF8 + "[Environment]::GetFolderPath('Desktop')"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        path = out.stdout.strip()
        if out.returncode == 0 and path:
            return Path(path)
    for name in ("Desktop", "Робочий стіл"):
        if (Path.home() / name).is_dir():
            return Path.home() / name
    return Path.home()


def create(paths: Paths, title: str) -> Path:
    """Створює (або оновлює) ярлик. Повертає його шлях."""
    exe = program()
    home = os.environ.get(HOME_ENV, "")
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        return _windows(paths, exe, home, title)
    return _posix(paths, exe, home, title)


def _windows(paths: Paths, exe: Path, home: str, title: str) -> Path:
    launcher = paths.config_dir / f"{NAME}.cmd"
    lines = ["@echo off", "chcp 65001 >nul", f"title {title}"]
    if home:
        lines.append(f'set "{HOME_ENV}={home}"')
    lines += [f'"{exe}" %*', "if errorlevel 1 pause"]
    launcher.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    link = desktop() / f"{NAME}.lnk"
    # шляхи — через змінні середовища, а не в тексті команди: так не ламаються лапки й літери
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BD_LINK); "
              "$s.TargetPath = $env:BD_TARGET; $s.WorkingDirectory = $env:BD_DIR; "
              "$s.IconLocation = $env:BD_ICON; $s.Description = $env:BD_TITLE; $s.Save()")
    env = {**os.environ, "BD_LINK": str(link), "BD_TARGET": str(launcher), "BD_DIR": str(Path.home()),
           "BD_ICON": f"{exe},0", "BD_TITLE": title}
    out = subprocess.run(["powershell", "-NoProfile", "-Command", UTF8 + script], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env, timeout=60)
    if out.returncode != 0 or not link.exists():
        raise ShortcutError((out.stderr or out.stdout).strip()[:300])
    return link


def _posix(paths: Paths, exe: Path, home: str, title: str) -> Path:
    launcher = paths.config_dir / f"{NAME}.sh"
    lines = ["#!/bin/sh", f"printf '\\033]0;{title}\\007'"]
    if home:
        lines.append(f"export {HOME_ENV}='{home}'")
    lines += [f"'{exe}' \"$@\" || {{ echo; read -r _; }}"]
    launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")
    launcher.chmod(0o755)
    if sys.platform == "darwin":   # .command відкривається в Терміналі подвійним клацанням
        link = desktop() / f"{NAME}.command"
        link.write_text(f"#!/bin/sh\nexec '{launcher}'\n", encoding="utf-8")
    else:
        link = desktop() / f"{NAME}.desktop"
        link.write_text("[Desktop Entry]\nType=Application\n"
                        f"Name={title}\nExec='{launcher}'\nTerminal=true\nCategories=Utility;\n", encoding="utf-8")
    link.chmod(0o755)
    return link
