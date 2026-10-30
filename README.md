# backlog-digest

> 🚧 Early stage — stage 3 of 7: YouTube, folders with books, web articles → summaries in Obsidian / Markdown.
> Рання стадія — етап 3 із 7: YouTube, теки з книгами, веб-статті → конспекти в Obsidian / Markdown.

**EN:** Turn thousands of "saved for later" videos, articles, books and posts into a readable library:
a summary, a critical score and a "worth it or not" verdict for each item, sorted by topic,
delivered to your own Obsidian / Markdown folder / Raindrop. Free by default.

**UK:** Розбери свої завали: тисячі збережених відео, статей, книг і постів → бібліотека,
яку можна прочитати за вечір. Конспект, критична оцінка й вердикт «варто чи ні» для кожного елемента,
сортування за темами, результат — у вашому Obsidian / Markdown-папці / Raindrop. Безкоштовно за замовчуванням.

Specification (Ukrainian): [docs/ТЗ.md](docs/ТЗ.md)

Based on the working personal prototype `yt-digest` (tag `personal-v1`).

License / Ліцензія: [MIT](LICENSE).

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

### Конспекти YouTube (етап 2)

Без Google Cloud і без ключів YouTube. Потрібен лише ключ моделі — безкоштовний ключ Gemini:
<https://aistudio.google.com/apikey>.

1. **Ключ:** меню → 8 Налаштування → 4 Ключі моделей → вставити (програма одразу перевіряє ключ
   і зберігає його у `.env`). Або змінна середовища `GEMINI_API_KEY`.
2. **Куди складати:** меню → 8 → 3 Тека для конспектів (тека сховища Obsidian або будь-яка тека).
3. **Що розбирати:** меню → 2 Джерела → 1 Додати YouTube → посилання на плейлист, канал (`@назва`) або відео.
4. **Конспекти:** меню → 3 → режим → скільки обробити. Ctrl+C зупиняє, прогрес зберігається.

Обробка має два незалежні кроки — їх можна робити разом або окремо, у різний час:
- **зібрати субтитри** — повільно, з вашої інтернет-адреси; моделі не потрібні. Тексти лежать у базі,
  доки не готовий конспект, а потім видаляються (знадобляться знову — їх можна зібрати повторно);
- **зробити конспекти** з уже зібраного — YouTube не потрібен, швидко, скільки дозволяють ліміти моделей.

Тож коли YouTube тимчасово обмежив вашу адресу, конспекти з уже зібраного далі робляться;
а коли скінчились ліміти моделей — далі збираються субтитри.

Те саме командами (зручно для розкладу):

```powershell
.venv\Scripts\bdig add "https://www.youtube.com/playlist?list=..." --name "Наука" --folder "YouTube/Наука"
.venv\Scripts\bdig sync               # знайти нові відео в усіх джерелах
.venv\Scripts\bdig process --limit 20    # обидва кроки поспіль
.venv\Scripts\bdig fetch --limit 30      # лише зібрати субтитри
.venv\Scripts\bdig summarize             # лише конспекти із зібраного
```

Як це працює і чого чекати:
- між запитами субтитрів — випадкова пауза 1–3 хв (`[youtube] delay_min/delay_max`) і не більше 30 запитів субтитрів
  на добу (`[limits] per_day`; на конспекти цей ліміт не діє):
  YouTube обмежує IP і за часті запити, і за їх кількість за добу;
  якщо обмежив — програма більше до нього не звертається, а черга лишається на потім;
- моделі — черга з запасними: коли денний ліміт однієї вичерпано, береться наступна, а вичерпані
  запам'ятовуються до скидання лімітів; свої моделі й провайдери — у `[models]` файлу `config.toml`;
- мова конспекту — будь-яка (налаштування), незалежно від мови відео; підписи нотаток для мов поза
  uk/en/fr/es модель перекладає один раз, їх можна виправити у `note_labels/<код>.toml`;
- відео без субтитрів отримують статус «без тексту» (розпізнавання мовлення — на пізніших етапах);
- те саме відео в кількох списках конспектується один раз.

### Книги й документи з папки (етап 3, частина 1)

Меню → 2 Джерела → 2 Додати папку → повний шлях до теки (можна синхронізовану: Google Drive, OneDrive,
Dropbox). Або командою: `.venv\Scripts\bdig add "D:\Книги"`. Підтеки першого рівня стають окремими списками.

- Формати: PDF, EPUB, FB2 (і .fb2.zip), DOCX, ODT, RTF, HTML, TXT, MD — без додаткових програм;
  DJVU — потрібна безкоштовна DjVuLibre; MOBI, AZW3, DOC — безкоштовна Calibre (`bdig doctor` покаже, чи знайдено).
- Книги обробляються на кроці «конспекти» (меню → 3, режим 1 або 3; команда `bdig summarize`): YouTube
  не потрібен, добовий ліміт субтитрів на них не діє.
- Довга книга (понад `[models] max_chars` символів) читається частинами: спершу робочі нотатки кожної частини,
  потім один підсумковий конспект. Нотатки частин зберігаються, тож після зупинки (скінчились ліміти моделей)
  книга продовжиться з того місця; коли конспект готовий — вони видаляються.
- Скан без текстового шару отримує статус «без тексту» (розпізнавання сканів — на пізніших етапах).
- Та сама книга в кількох теках конспектується один раз; перейменований чи переміщений файл не стає новим.

### Веб-статті (етап 3, частина 2)

Меню → 2 Джерела → 3 Додати статті → по одному в рядку: посилання або повний шлях до файлу:
- закладки браузера — HTML-експорт Chrome, Edge, Firefox, Safari (теки закладок стають списками);
- експорт Pocket (HTML або CSV), Instapaper (CSV), будь-який CSV зі стовпцем `url`, або TXT — посилання в рядку.

Або командою: `.venv\Scripts\bdig add "D:\bookmarks.html"` чи `.venv\Scripts\bdig add https://site/article`.

- Програма завантажує сторінку й бере лише основний текст (без меню, реклами й коментарів — пакет trafilatura),
  разом із назвою, автором і датою. Посилання на PDF читається як книга.
- Статті обробляються на кроці «конспекти» (меню → 3, режим 1 або 3; команда `bdig summarize`).
- Посилання на YouTube у закладках стають відео (той самий конспект, що й у джерелі YouTube).
- Сторінки, якої більше немає (404), і сторінки без тексту статті (вхід, платний доступ) отримують
  статус «без тексту»; тимчасові помилки сайту — повтор пізніше.
- Файл закладок перечитується при кожному оновленні списків: можна перезберегти його з браузера —
  додадуться лише нові посилання.

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
