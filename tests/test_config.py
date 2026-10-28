from __future__ import annotations

import tomllib

import pytest

from backlog_digest import config, i18n


def test_missing_file_gives_defaults(paths):
    cfg = config.load(paths)
    assert cfg["general"] == {"ui_language": "en", "summary_language": "en"}
    assert cfg["limits"]["per_run"] == 30
    assert not paths.config_file.exists()


def test_ensure_creates_file_once(paths):
    assert config.ensure(paths) is True
    assert config.ensure(paths) is False
    assert paths.config_file.parent == paths.config_dir


def test_user_values_override_defaults_and_unknown_keys_survive(paths):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        '[general]\nui_language = "fr"\nsummary_language = ""\n\n[future]\nflag = true\n', encoding="utf-8")
    cfg = config.load(paths)
    assert cfg["general"]["ui_language"] == "fr"
    assert cfg["general"]["summary_language"] == "fr"   # порожня — як мова інтерфейсу
    assert cfg["output"]["kind"] == "obsidian"
    config.save(paths, cfg)
    assert config.load(paths)["future"] == {"flag": True}


def test_ui_and_summary_language_are_independent(paths):
    cfg = config.defaults()
    cfg["general"].update(ui_language="es", summary_language="pt-BR")
    config.save(paths, cfg)
    loaded = config.load(paths)
    assert loaded["general"] == {"ui_language": "es", "summary_language": "pt-BR"}


def test_roundtrip_windows_path_and_unicode(paths):
    cfg = config.defaults()
    cfg["output"]["path"] = r'D:\Сховище\Obsidian "мій"'
    config.save(paths, cfg)
    assert config.load(paths)["output"]["path"] == r'D:\Сховище\Obsidian "мій"'


def test_bom_is_tolerated(paths):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text('\ufeff[general]\nui_language = "uk"\n', encoding="utf-8")
    assert config.load(paths)["general"]["ui_language"] == "uk"


@pytest.mark.parametrize(("text", "key"), [
    ('[output]\npath = D:\\vault\n', "config.err_syntax"),
    ('[general]\nui_language = "ru"\n', "config.err_ui_language"),
    ('[general]\nsummary_language = "Ukrainian!"\n', "config.err_summary_language"),
    ('[output]\nkind = "notion"\n', "config.err_output_kind"),
    ('[limits]\nper_run = 0\n', "config.err_positive_int"),
    ('[limits]\nper_day = true\n', "config.err_positive_int"),
])
def test_invalid_config(paths, text, key):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(text, encoding="utf-8")
    with pytest.raises(config.ConfigError) as e:
        config.load(paths)
    assert e.value.key == key
    for lang in i18n.UI_LANGUAGES:
        i18n.set_language(lang)
        assert not e.value.message().startswith("config.")


def test_dumps_is_valid_toml():
    data = {"a": 1, "s": {"b": True, "c": [1, "x"], "weird key": 1.5}}
    assert tomllib.loads(config.dumps(data, header="line 1\nline 2")) == data
