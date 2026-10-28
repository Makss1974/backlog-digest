from __future__ import annotations

from backlog_digest import keys


def test_env_file_parsing(paths):
    paths.config_dir.mkdir(parents=True)
    paths.env_file.write_text(
        "\ufeff# comment\nGEMINI_API_KEY = 'abc'\nexport RAINDROP_TOKEN=\"t=1\"\nEMPTY=\nbroken line\n",
        encoding="utf-8")
    assert keys.read_env_file(paths.env_file) == {"GEMINI_API_KEY": "abc", "RAINDROP_TOKEN": "t=1", "EMPTY": ""}


def test_sources_and_priority(paths, monkeypatch):
    assert keys.get_secret("GEMINI_API_KEY", paths) is None
    assert keys.secret_source("GEMINI_API_KEY", paths) is None
    paths.config_dir.mkdir(parents=True)
    paths.env_file.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    assert keys.get_secret("GEMINI_API_KEY", paths) == "from-file"
    assert keys.secret_source("GEMINI_API_KEY", paths) == keys.FILE
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert keys.get_secret("GEMINI_API_KEY", paths) == "from-env"
    assert keys.secret_source("GEMINI_API_KEY", paths) == keys.ENV
