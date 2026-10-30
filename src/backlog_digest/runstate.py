"""Позначка «зараз іде обробка»: меню бачить, що в іншому вікні чи за розкладом уже працює програма.

Файл running.json у теці даних: {pid, started, stage}. Якщо процесу з таким pid уже немає
(вимкнули комп'ютер, закрили вікно), позначка вважається застарілою.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .paths import Paths

FILE = "running.json"


def _file(paths: Paths) -> Path:
    return paths.data_dir / FILE


def start(paths: Paths, stage: str, started: str) -> None:
    try:
        _file(paths).parent.mkdir(parents=True, exist_ok=True)
        _file(paths).write_text(json.dumps({"pid": os.getpid(), "started": started, "stage": stage}),
                                encoding="utf-8")
    except OSError:
        pass    # позначка — лише підказка для меню; без неї обробка йде так само


def finish(paths: Paths) -> None:
    state = _read(paths)
    if state and state.get("pid") == os.getpid():
        with contextlib.suppress(OSError):
            _file(paths).unlink()


def current(paths: Paths) -> dict[str, Any] | None:
    """Обробка, що йде в ІНШОМУ процесі, або None."""
    state = _read(paths)
    pid = int(state.get("pid") or 0) if state else 0
    if not state or pid == os.getpid() or not alive(pid):
        return None
    return state


def _read(paths: Paths) -> dict[str, Any] | None:
    try:
        data = json.loads(_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
