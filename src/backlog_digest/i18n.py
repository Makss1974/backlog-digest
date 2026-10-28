"""Переклади інтерфейсу. Тексти — у каталогах locales/<мова>.toml, у коді лише ключі.

    t("menu.quit")                   -> рядок поточною мовою
    t("doctor.disk_free", gb=12.5)   -> з підстановкою {gb}
    tn("plural.items", 5)            -> з правильною формою множини ({n} підставляється сам)
    words("answers.yes")             -> список слів (напр. варіанти «так»)

Якщо ключа немає — береться англійська, а якщо й там немає — сам ключ (програма не падає;
тест test_i18n перевіряє, що всі ключі є в усіх мовах).
"""
from __future__ import annotations

import contextlib
import locale
import os
import sys
import tomllib
from functools import cache
from importlib import resources
from typing import Any

UI_LANGUAGES = ("uk", "en", "fr", "es")
FALLBACK = "en"

# Мови конспектів: код -> самоназва мови (це дані, а не текст інтерфейсу).
# Список — лише підказка в меню; можна вказати будь-який код мови.
SUMMARY_LANGUAGES = {
    # мови інтерфейсу
    "uk": "Українська", "en": "English", "fr": "Français", "es": "Español",
    # Західна й Південна Європа
    "de": "Deutsch", "it": "Italiano", "pt": "Português", "nl": "Nederlands", "ca": "Català",
    "el": "Ελληνικά", "mt": "Malti",
    # Північна Європа
    "sv": "Svenska", "nb": "Norsk bokmål", "da": "Dansk", "fi": "Suomi", "is": "Íslenska",
    # Балтія
    "et": "Eesti", "lv": "Latviešu", "lt": "Lietuvių",
    # Центральна Європа
    "pl": "Polski", "cs": "Čeština", "sk": "Slovenčina", "hu": "Magyar",
    # Південно-Східна Європа
    "ro": "Română", "bg": "Български", "hr": "Hrvatski", "sr": "Srpski", "sl": "Slovenščina",
    "mk": "Македонски", "sq": "Shqip",
    # Східна Європа та сусіди
    "be": "Беларуская", "tr": "Türkçe",
}

# Назви мов, як їх повертає Windows (locale.getlocale() -> "Ukrainian_Ukraine")
_WINDOWS_NAMES = {"ukrainian": "uk", "english": "en", "french": "fr", "spanish": "es"}

_current = FALLBACK


@cache
def catalog(lang: str) -> dict[str, Any]:
    """Каталог мови як плоский словник: {"menu.title": "...", "plural.items": {"one": ..., ...}}."""
    text = resources.files(__package__).joinpath("locales", f"{lang}.toml").read_text(encoding="utf-8")
    return _flatten(tomllib.loads(text))


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        # таблиця форм множини (one/few/many/other) — це одне значення, а не розділ
        if isinstance(v, dict) and not key.startswith("plural."):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in UI_LANGUAGES else FALLBACK


def get_language() -> str:
    return _current


def _lookup(key: str) -> Any:
    for lang in (_current, FALLBACK):
        value = catalog(lang).get(key)
        if value is not None:
            return value
    return None


def t(key: str, **params: Any) -> str:
    value = _lookup(key)
    if not isinstance(value, str):
        return key
    return value.format(**params) if params else value


def tn(key: str, n: int, **params: Any) -> str:
    forms = _lookup(key)
    if not isinstance(forms, dict):
        return key
    form = forms.get(plural_form(_current, n)) or forms.get("other") or next(iter(forms.values()))
    return str(form).format(n=n, **params)


def words(key: str) -> list[str]:
    value = _lookup(key)
    return [str(w) for w in value] if isinstance(value, list) else []


def language_name(lang: str) -> str:
    """Назва мови її ж мовою (для вибору мови інтерфейсу)."""
    return str(catalog(lang).get("meta.name", lang))


def plural_form(lang: str, n: int) -> str:
    """Форма множини за правилами CLDR (для цілих чисел)."""
    n = abs(n)
    if lang == "uk":
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if lang == "fr":
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


PLURAL_FORMS = {"uk": ("one", "few", "many"), "en": ("one", "other"), "fr": ("one", "other"),
                "es": ("one", "other")}


def detect_language() -> str:
    """Мова інтерфейсу з налаштувань системи; якщо вона не підтримується — англійська."""
    candidates = [os.environ.get(v, "") for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")]
    if sys.platform == "win32":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined,unused-ignore]
            candidates.append(locale.windows_locale.get(lcid, ""))
        except (AttributeError, OSError):
            pass
    with contextlib.suppress(ValueError):
        candidates.append(locale.getlocale()[0] or "")
    for c in candidates:
        code = normalize_language(c)
        if code:
            return code
    return FALLBACK


def normalize_language(value: str) -> str | None:
    """'uk_UA.UTF-8' / 'Ukrainian_Ukraine' / 'fr-CA' -> 'uk' / 'uk' / 'fr'; непідтримувана -> None."""
    v = value.strip().lower()
    if not v or v in ("c", "posix"):
        return None
    for name, code in _WINDOWS_NAMES.items():
        if v.startswith(name):
            return code
    code = v.replace("-", "_").split("_")[0].split(".")[0].split(":")[0]
    return code if code in UI_LANGUAGES else None
