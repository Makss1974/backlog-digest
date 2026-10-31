"""Пошук зовнішніх програм (DjVuLibre, Calibre, ffmpeg). Лише перевірка наявності — нічого не встановлюємо.

На Windows ці програми часто не додають себе в PATH або стоять не на диску C:,
тому шукаємо ще в типових теках, у теках менеджерів пакетів і в реєстрі (список встановлених програм).
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Tool:
    id: str
    exe: str                       # програма, яку шукаємо
    registry_name: str             # частина DisplayName у реєстрі Windows
    windows_dirs: tuple[str, ...]  # типові теки встановлення на Windows


TOOLS = (
    Tool("djvulibre", "djvutxt", "djvulibre",
         (r"C:\Program Files (x86)\DjVuLibre", r"C:\Program Files\DjVuLibre")),
    Tool("calibre", "ebook-convert", "calibre",
         (r"C:\Program Files\Calibre2", r"C:\Program Files (x86)\Calibre2", r"C:\Program Files\Calibre")),
    Tool("ffmpeg", "ffmpeg", "ffmpeg",
         (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin")),
)


def find(tool: Tool) -> str | None:
    exe = shutil.which(tool.exe)
    if exe:
        return exe
    if sys.platform != "win32":
        return None
    for d in [*tool.windows_dirs, *_package_manager_dirs(), *registry_dirs(tool.registry_name)]:
        for cand in (Path(d) / f"{tool.exe}.exe", Path(d) / "bin" / f"{tool.exe}.exe"):
            if cand.is_file():
                return str(cand)
    return None


def _package_manager_dirs() -> list[str]:
    """Теки, куди кладуть програми winget, Scoop і Chocolatey (не завжди потрапляють у PATH
    уже відкритого терміналу)."""
    local = os.environ.get("LOCALAPPDATA", "")
    dirs = [str(Path.home() / "scoop" / "shims"), r"C:\ProgramData\chocolatey\bin"]
    if local:
        dirs.insert(0, str(Path(local) / "Microsoft" / "WinGet" / "Links"))
    return dirs


def registry_dirs(name: str) -> list[str]:
    """Теки встановлення програм, у назві яких є name, з реєстру Windows."""
    if sys.platform != "win32":   # так і mypy на Linux/macOS знає, що winreg тут не потрібен
        return []
    import winreg

    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    keys = [(winreg.HKEY_LOCAL_MACHINE, uninstall),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, uninstall)]
    dirs: list[str] = []
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
                display = str(winreg.QueryValueEx(sub, "DisplayName")[0]).lower()
            except OSError:
                continue
            if name not in display:
                continue
            for value in ("InstallLocation", "DisplayIcon", "UninstallString"):
                try:
                    v = str(winreg.QueryValueEx(sub, value)[0]).strip().strip('"').split('"')[0]
                except OSError:
                    continue
                if v:
                    p = Path(v.split(",")[0])
                    dirs.append(str(p if p.is_dir() else p.parent))
    return dirs
