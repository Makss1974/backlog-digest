from __future__ import annotations

import subprocess

import pytest

from backlog_digest import autostart, i18n, keys
from backlog_digest.paths import HOME_ENV, get_paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Кожен тест — з власною порожньою текою програми, без справжніх ключів і з англійською мовою."""
    monkeypatch.setenv(HOME_ENV, str(tmp_path / "home"))
    for name in (*keys.MODEL_KEYS, *keys.OTHER_KEYS):
        monkeypatch.delenv(name, raising=False)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(i18n, "detect_language", lambda: "en")
    # системний планувальник у тестах не чіпаємо: кожна команда «не вдалася»
    monkeypatch.setattr(autostart, "_run", lambda args, stdin=None: subprocess.CompletedProcess(args, 1, "", ""))
    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture
def paths():
    return get_paths()
