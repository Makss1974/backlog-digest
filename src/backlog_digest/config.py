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
        "per_day": 30,            # скільки субтитрів збирати за добу: YouTube рахує й добову кількість запитів
    },
    "youtube": {
        "delay_min": 60,          # випадкова пауза між запитами субтитрів, с: YouTube обмежує IP за часті запити
        "delay_max": 180,
        "languages": [],          # бажані мови субтитрів ([] — англійська, потім мова конспектів)
        "newest_first": False,    # обробляти спершу нові відео списку
        "no_subtitles": "gemini", # відео без субтитрів: gemini — Gemini «дивиться» відео за посиланням;
                                  # whisper — звук розпізнається на цьому комп'ютері; skip — пропустити
        "part_minutes": 20,       # довше відео без субтитрів Gemini дивиться частинами такої довжини
    },
    "whisper": {
        "model": "small",         # модель розпізнавання: tiny / base / small / medium / large-v3-turbo
        "language": "",           # мова запису ("" — визначити самостійно)
    },
    "raindrop": {
        "export_root": "Backlog digest",   # коренева колекція для експорту конспектів
    },
    "models": {
        "order": ["gemini", "openrouter", "deepseek"],   # черга провайдерів: перший — основний
        "thinking": "fast",       # «роздуми» моделі: fast / deep / auto
        "temperature": 0.3,
        "max_chars": 200000,      # довший текст обрізається
        # Свій провайдер або свої моделі: [models.<назва>] base_url, key_env, models, json_mode
    },
}

THINKING_MODES = ("fast", "deep", "auto")
NO_SUBTITLES_MODES = ("gemini", "whisper", "skip")

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
    for section, name in (("limits", "per_run"), ("limits", "per_day"), ("models", "max_chars")):
        v = cfg[section][name]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ConfigError("config.err_positive_int", name=f"{section}.{name}", value=v)
    yt = cfg["youtube"]
    for name in ("delay_min", "delay_max"):
        if not isinstance(yt[name], int | float) or isinstance(yt[name], bool) or yt[name] < 0:
            raise ConfigError("config.err_positive_int", name=f"youtube.{name}", value=yt[name])
    if yt["delay_max"] < yt["delay_min"]:
        yt["delay_max"] = yt["delay_min"]
    if yt["no_subtitles"] not in NO_SUBTITLES_MODES:
        raise ConfigError("config.err_no_subtitles", value=yt["no_subtitles"], allowed=", ".join(NO_SUBTITLES_MODES))
    w = cfg["whisper"]
    if not isinstance(w["model"], str) or not w["model"].strip():
        raise ConfigError("config.err_whisper_model", value=w["model"])
    v = yt["part_minutes"]
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ConfigError("config.err_positive_int", name="youtube.part_minutes", value=v)
    m = cfg["models"]
    if m["thinking"] not in THINKING_MODES:
        raise ConfigError("config.err_thinking", value=m["thinking"], allowed=", ".join(THINKING_MODES))
    if not isinstance(m["order"], list) or not m["order"]:
        raise ConfigError("config.err_models_order")


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
    """Простий запис TOML: таблиці (й вкладені таблиці) зі скалярами та списками."""
    lines = [f"# {line}" for line in header.splitlines()]
    _dump_table(cfg, [], lines)
    return "\n".join(lines) + "\n"


def _dump_table(table: dict[str, Any], path: list[str], lines: list[str]) -> None:
    for k, v in table.items():
        if not isinstance(v, dict):
            lines.append(f"{_key(k)} = {_value(v)}")
    for k, v in table.items():
        if isinstance(v, dict):
            lines += ["", f"[{'.'.join(_key(p) for p in [*path, k])}]"]
            _dump_table(v, [*path, k], lines)


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
