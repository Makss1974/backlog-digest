"""Де програма тримає свої файли: конфіг і секрети — окремо від даних (база, кеш, журнали).

Усе — у теці користувача, а не в теці проєкту:
  Windows: конфіг %APPDATA%\\backlog-digest, дані %LOCALAPPDATA%\\backlog-digest
  macOS:   ~/Library/Application Support/backlog-digest (обидва)
  Linux:   $XDG_CONFIG_HOME/backlog-digest і $XDG_DATA_HOME/backlog-digest

BACKLOG_DIGEST_HOME=<тека> кладе все в одну теку (портативний режим і тести).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME

HOME_ENV = "BACKLOG_DIGEST_HOME"


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    data_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def env_file(self) -> Path:
        return self.config_dir / ".env"

    @property
    def db_file(self) -> Path:
        return self.data_dir / "backlog.sqlite"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


def get_paths() -> Paths:
    home = os.environ.get(HOME_ENV)
    if home:
        p = Path(home).expanduser()
        return Paths(p, p)
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return Paths(roaming / APP_NAME, local / APP_NAME)
    if sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_NAME
        return Paths(p, p)
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return Paths(cfg / APP_NAME, data / APP_NAME)
