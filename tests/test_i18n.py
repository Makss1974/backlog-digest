from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

from backlog_digest import i18n

SRC = Path(__file__).resolve().parent.parent / "src" / "backlog_digest"
LANGS = i18n.UI_LANGUAGES


def placeholders(value: object) -> set[str]:
    texts = value.values() if isinstance(value, dict) else value if isinstance(value, list) else [value]
    return {f for text in texts for _, f, _, _ in string.Formatter().parse(str(text)) if f}


def test_catalog_files_match_ui_languages():
    files = {p.stem for p in (SRC / "locales").glob("*.toml")}
    assert files == set(LANGS)
    assert "ru" not in files


@pytest.mark.parametrize("lang", LANGS)
def test_all_keys_present_in_every_language(lang):
    reference = set(i18n.catalog("en"))
    keys = set(i18n.catalog(lang))
    assert reference - keys == set(), f"{lang}: missing keys"
    assert keys - reference == set(), f"{lang}: extra keys"


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_match_english(lang):
    en, other = i18n.catalog("en"), i18n.catalog(lang)
    for key, value in en.items():
        assert placeholders(other[key]) == placeholders(value), f"{lang}:{key}"


@pytest.mark.parametrize("lang", LANGS)
def test_values_have_expected_types(lang):
    for key, value in i18n.catalog(lang).items():
        if key.startswith("plural."):
            assert set(value) == set(i18n.PLURAL_FORMS[lang]), f"{lang}:{key}"
            assert all("{n}" in form for form in value.values()), f"{lang}:{key}"
        elif key.startswith("answers.yes") and not key.endswith("_hint"):
            assert isinstance(value, list) and value, f"{lang}:{key}"
        else:
            assert isinstance(value, str) and value.strip(), f"{lang}:{key}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_key_used_in_code_exists(lang):
    """Ключі, згадані в коді як t("...") / tn("..."), мають бути в каталогах."""
    used = set()
    for py in SRC.glob("*.py"):
        used |= set(re.findall(r"""\bt[n]?\(\s*["']([a-z_]+\.[a-z_.]+)["']""", py.read_text(encoding="utf-8")))
    assert used, "no keys found — regex is broken"
    missing = used - set(i18n.catalog(lang))
    assert not missing, f"{lang}: {sorted(missing)}"


@pytest.mark.parametrize(("n", "form"), [(0, "many"), (1, "one"), (2, "few"), (4, "few"), (5, "many"),
                                         (11, "many"), (12, "many"), (21, "one"), (22, "few"), (111, "many")])
def test_ukrainian_plural(n, form):
    assert i18n.plural_form("uk", n) == form


def test_other_plurals():
    assert i18n.plural_form("en", 1) == "one" and i18n.plural_form("en", 0) == "other"
    assert i18n.plural_form("fr", 0) == "one" and i18n.plural_form("fr", 2) == "other"
    assert i18n.plural_form("es", 1) == "one" and i18n.plural_form("es", 21) == "other"


def test_translation_and_fallback():
    i18n.set_language("uk")
    assert i18n.t("menu.quit") == "Вихід"
    assert i18n.tn("plural.items", 3) == "3 елементи"
    assert i18n.t("no.such.key") == "no.such.key"
    i18n.set_language("xx")
    assert i18n.get_language() == "en"


@pytest.mark.parametrize(("raw", "code"), [("uk_UA.UTF-8", "uk"), ("Ukrainian_Ukraine", "uk"), ("fr-CA", "fr"),
                                           ("es_ES", "es"), ("en_US", "en"), ("ru_RU", None), ("C", None),
                                           ("", None)])
def test_normalize_language(raw, code):
    assert i18n.normalize_language(raw) == code


def test_language_names_are_endonyms():
    assert [i18n.language_name(lang) for lang in LANGS] == ["Українська", "English", "Français", "Español"]


# ---------------------------------------------------------------- без хардкоду

OUTPUT_CALLS = {"print", "input", "ask", "ask_int", "ask_yes", "log"}
WORD = re.compile(r"[^\W\d_]{2,}")


def _literal_parts(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            yield from _literal_parts(v)


def test_no_hardcoded_ui_text():
    """Рядки, які бачить користувач (print/input/ask), мають іти через t(), а не бути в коді."""
    offenders = []
    for py in SRC.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in OUTPUT_CALLS:
                continue
            for arg in node.args:
                for text in _literal_parts(arg):
                    if WORD.search(text):
                        offenders.append(f"{py.name}:{node.lineno}: {text!r}")
    assert not offenders, "\n".join(offenders)


def test_summary_languages_cover_europe():
    from backlog_digest.config import _LANG_CODE

    codes = set(i18n.SUMMARY_LANGUAGES)
    assert set(LANGS) <= codes
    assert {"sv", "nb", "da", "fi", "cs", "sk", "hu", "ro", "bg", "el", "nl", "et", "lv", "lt"} <= codes
    assert "ru" not in codes
    assert all(_LANG_CODE.match(c) for c in codes)
