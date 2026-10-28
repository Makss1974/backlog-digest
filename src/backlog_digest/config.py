"""Налаштування: config.toml у теці користувача (див. paths.py), поверх значень за замовчуванням.

Формат TOML, а не YAML (як у прототипі): менше пасток з лапками й двокрапками,
а читання вбудоване в Python (tomllib). Секрети тут не зберігаються — див. keys.py.
"""
from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import i18n
from .paths import Paths

OUTPUT_KINDS = ("obsidian", "markdown")

DEFAULTS: dict[str, Any] = {
    "general": {
        "ui_language": "",        # мова інтерфейсу: uk / en / fr / es ("" — як у системі)
        "summary_language": "",   # мова конспектів, незалежно від мови джерела ("" — як інтерфейс)
    },
    "output": {
        "kind": "obsidian",       # obsidian / markdown
        "path": "",               # тека сховища Obsidian або просто тека для Markdown
    },
    "limits": {
        "per_run": 30,            # скільки елементів за один запуск
        "per_day": 100,           # скільки елементів за добу (бережемо квоти моделей і YouTube)
    },
}

_LANG_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class ConfigError(Exception):
    """Помилка в налаштуваннях. Текст — ключ перекладу з параметрами, перекладається під час показу."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key)
        self.key = key
        self.params = params

    def message(self) -> str:
        return i18n.t(self.key, **self.params)


def defaults() -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    lang = i18n.detect_language()
    cfg["general"]["ui_language"] = lang
    cfg["general"]["summary_language"] = lang
    return cfg


def load(paths: Paths) -> dict[str, Any]:
    """Читає config.toml і доповнює значеннями за замовчуванням. Файлу немає — лише значення за замовчуванням."""
    cfg = defaults()
    path = paths.config_file
    if not path.exists():
        return cfg
    try:
        user = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("config.err_syntax", file=path.name, detail=str(e)) from e
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError("config.err_read", file=str(path), detail=str(e)) from e
    _merge(cfg, user)
    validate(cfg)
    return cfg


def _merge(base: dict[str, Any], user: dict[str, Any]) -> None:
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v   # невідомі ключі зберігаємо — їх могла записати новіша версія програми


def validate(cfg: dict[str, Any]) -> None:
    g = cfg["general"]
    if not g["ui_language"]:
        g["ui_language"] = i18n.detect_language()
    if g["ui_language"] not in i18n.UI_LANGUAGES:
        raise ConfigError("config.err_ui_language", value=g["ui_language"],
                          allowed=", ".join(i18n.UI_LANGUAGES))
    if not g["summary_language"]:
        g["summary_language"] = g["ui_language"]
    if not isinstance(g["summary_language"], str) or not _LANG_CODE.match(g["summary_language"]):
        raise ConfigError("config.err_summary_language", value=g["summary_language"])
    if cfg["output"]["kind"] not in OUTPUT_KINDS:
        raise ConfigError("config.err_output_kind", value=cfg["output"]["kind"],
                          allowed=", ".join(OUTPUT_KINDS))
    for name in ("per_run", "per_day"):
        v = cfg["limits"][name]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ConfigError("config.err_positive_int", name=f"limits.{name}", value=v)


def save(paths: Paths, cfg: dict[str, Any]) -> Path:
    path = paths.config_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(cfg, header=i18n.t("config.file_header")), encoding="utf-8")
    tmp.replace(path)   # атомарно: обірваний запис не зіпсує наявний файл
    return path


def ensure(paths: Paths) -> bool:
    """Створює config.toml зі значеннями за замовчуванням, якщо його немає. True — щойно створено."""
    if paths.config_file.exists():
        return False
    save(paths, defaults())
    return True


def dumps(cfg: dict[str, Any], header: str = "") -> str:
    """Простий запис TOML: розділи зі скалярами й списками (цього достатньо для нашого конфігу)."""
    lines = [f"# {line}" for line in header.splitlines()]
    scalars = {k: v for k, v in cfg.items() if not isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{_key(k)} = {_value(v)}")
    for section, values in cfg.items():
        if not isinstance(values, dict):
            continue
        lines += ["", f"[{_key(section)}]"]
        for k, v in values.items():
            if isinstance(v, dict):
                raise TypeError(f"nested table {section}.{k} is not supported")
            lines.append(f"{_key(k)} = {_value(v)}")
    return "\n".join(lines) + "\n"


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)   # екранування JSON сумісне з рядками TOML
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"unsupported value type: {type(v).__name__}")
