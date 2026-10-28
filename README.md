# backlog-digest

> 🚧 Early stage — stage 1 of 7 (skeleton: settings, languages, database, `doctor`, menu).
> Рання стадія — етап 1 із 7 (каркас: налаштування, мови, база, `doctor`, меню).

**EN:** Turn thousands of "saved for later" videos, articles, books and posts into a readable library:
a summary, a critical score and a "worth it or not" verdict for each item, sorted by topic,
delivered to your own Obsidian / Markdown folder / Raindrop. Free by default.

**UK:** Розбери свої завали: тисячі збережених відео, статей, книг і постів → бібліотека,
яку можна прочитати за вечір. Конспект, критична оцінка й вердикт «варто чи ні» для кожного елемента,
сортування за темами, результат — у вашому Obsidian / Markdown-папці / Raindrop. Безкоштовно за замовчуванням.

Specification (Ukrainian): [docs/ТЗ.md](docs/ТЗ.md)

Based on the working personal prototype `yt-digest` (tag `personal-v1`).

---

## Запуск каркаса локально (для розробки)

Потрібен Python 3.11 або новіший. Усі пакети ставляться в теку `.venv` усередині проєкту —
системний Python не змінюється. Команди для PowerShell (Windows):

```powershell
cd D:\PROJECTS\IT-PROJECTS\backlog-digest
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Запуск (без активації середовища — просто повний шлях до програми):

```powershell
.venv\Scripts\bdig                 # меню (вихід — q)
.venv\Scripts\bdig doctor          # перевірка: Python, налаштування, ключі, мережа, програми, місце
.venv\Scripts\bdig --lang fr doctor   # те саме французькою (uk, en, fr, es)
.venv\Scripts\backlog-digest --help   # повна назва команди теж працює
```

Перевірки якості:

```powershell
.venv\Scripts\pytest          # тести
.venv\Scripts\ruff check .    # стиль
.venv\Scripts\mypy            # типи
```

На macOS/Linux те саме, але `.venv/bin/...` замість `.venv\Scripts\...`.

### Де програма тримає файли

| Що | Windows | macOS | Linux |
|---|---|---|---|
| Налаштування `config.toml` і ключі `.env` | `%APPDATA%\backlog-digest` | `~/Library/Application Support/backlog-digest` | `~/.config/backlog-digest` |
| База, кеш, журнали | `%LOCALAPPDATA%\backlog-digest` | те саме | `~/.local/share/backlog-digest` |

Змінна середовища `BACKLOG_DIGEST_HOME=<тека>` кладе все в одну теку (портативний режим, тести).

Мова інтерфейсу й мова конспектів — окремі налаштування (меню → 8). Ключі моделей (`GEMINI_API_KEY`,
`OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`) і `RAINDROP_TOKEN` читаються зі змінних середовища або з
файлу `.env` у теці налаштувань; програма ніколи не показує їхні значення.

## Running the skeleton locally (development)

Python 3.11+. Create a virtual environment and install in editable mode:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"   # Windows: .venv\Scripts\python
.venv/bin/bdig            # interactive menu (q to quit)
.venv/bin/bdig doctor     # health check; --lang uk|en|fr|es, --offline
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/mypy
```
