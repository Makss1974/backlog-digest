"""Секрети (ключі моделей, токени): змінні середовища або файл .env у теці налаштувань.

Чому .env, а не системне сховище ключів (keyring):
- keyring — стороння залежність, а на Linux без робочого столу (сервер, WSL, cron) часто не має
  «бекенда» і падає саме у фоновому запуску за розкладом;
- .env працює однаково на Windows/macOS/Linux, у Планувальнику й у CI, без додаткових пакетів;
- файл лежить у теці користувача (не в теці проєкту й не в git), на macOS/Linux — з правами 600.
keyring можна буде додати пізніше як необов'язкове сховище за цим самим get_secret().

Значення ключів програма ніколи не друкує й не пише в журнали.
"""
from __future__ import annotations

import os
from pathlib import Path

from .paths import Paths

# Ключі, які знає програма: назва змінної -> сервіс
MODEL_KEYS = {
    "GEMINI_API_KEY": "Gemini",
    "OPENROUTER_API_KEY": "OpenRouter",
    "DEEPSEEK_API_KEY": "DeepSeek",
}
OTHER_KEYS = {
    "RAINDROP_TOKEN": "Raindrop.io",
}

ENV, FILE = "env", "file"


def read_env_file(path: Path) -> dict[str, str]:
    """Розбирає .env: рядки KEY=VALUE, коментарі #, лапки навколо значення, необов'язкове 'export '."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            out[name] = value
    return out


def get_secret(name: str, paths: Paths) -> str | None:
    """Змінна середовища має перевагу над .env (зручно для CI й разових запусків)."""
    value = os.environ.get(name) or read_env_file(paths.env_file).get(name)
    return value or None


def secret_source(name: str, paths: Paths) -> str | None:
    """Звідки береться ключ: 'env', 'file' або None (не задано). Самого значення не повертає."""
    if os.environ.get(name):
        return ENV
    if read_env_file(paths.env_file).get(name):
        return FILE
    return None
