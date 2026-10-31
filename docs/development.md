# Development

[Home](../README.md) · Українською — нижче

## Running from source

Python 3.11 or newer. Everything goes into `.venv` inside the project; the system Python is not changed.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # Windows: .venv\Scripts\python
.venv/bin/bdig                 # menu (q to quit)
.venv/bin/bdig doctor          # health check; --lang uk|en|fr|es, --offline
```

Quality checks — the same three run in CI (Windows, macOS, Linux; Python 3.11 and 3.13), plus a package build:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy
```

Tests never touch the network, the real model, YouTube, Raindrop or the system scheduler: each test gets an
empty program folder (`BACKLOG_DIGEST_HOME` in a temp dir), fake model answers and fake HTTP.

For manual checks keep a separate folder: `bdig --home D:\test\home ...` (or `BACKLOG_DIGEST_HOME`).

## Layout

| Module | What it does |
|---|---|
| `cli.py`, `menu.py` | commands and the interactive menu with the data panel |
| `wizard.py` | first-run setup |
| `config.py`, `paths.py`, `keys.py` | `config.toml`, program folders, secrets from the environment / `.env` |
| `i18n.py`, `locales/*.toml` | interface texts in uk, en, fr, es; every key exists in every language (tested) |
| `db.py` | SQLite with numbered migrations (`PRAGMA user_version`) |
| `youtube.py`, `folders.py`, `readers.py`, `web.py`, `raindrop.py`, `tools.py` | sources and file formats |
| `llm.py`, `summarize.py` | model queue with spares and quotas, summary prompt and answer parsing |
| `pipeline.py` | sync and processing: statuses `new → done / no_text / error → failed`, resume after a stop |
| `notes.py` | Markdown notes with frontmatter, safe file names, Windows path limits |
| `library.py`, `sorting_menu.py` | sorting by topic, report, index note |
| `export.py` | Raindrop export |
| `schedule.py`, `autostart.py`, `runstate.py` | jobs, system scheduler on 3 OSes, "running now" marker |
| `doctor.py`, `shortcut.py` | health check, desktop shortcut |

Adding an interface text: add the key to all four files in `locales/`; `tests/test_i18n.py` checks that every
key used in code exists in every language.

---

## Розробка (українською)

Python 3.11+, усе — у `.venv` усередині проєкту:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\bdig                  # меню
.venv\Scripts\pytest; .venv\Scripts\ruff check .; .venv\Scripts\mypy
```

Тести не звертаються до мережі, справжньої моделі, YouTube, Raindrop і системного планувальника. Для ручних
перевірок — окрема тека: `bdig --home D:\test\home ...`. Новий текст інтерфейсу — ключ у всіх чотирьох
файлах `locales/`.
