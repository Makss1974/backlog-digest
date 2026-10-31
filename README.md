# backlog-digest

**Work through your "saved for later" pile.** Thousands of saved videos, articles and books →
a library you can read in an evening.

[Українською](https://github.com/Makss1974/backlog-digest/blob/main/README.uk.md) · [FAQ](https://github.com/Makss1974/backlog-digest/blob/main/docs/FAQ.md) · [Example summary](https://github.com/Makss1974/backlog-digest/blob/main/docs/examples/video-summary.en.md) ·
License: [MIT](https://github.com/Makss1974/backlog-digest/blob/main/LICENSE)

For every item backlog-digest writes a summary with a **critical score** and an honest **"worth it or not"**
verdict, flags pseudoscience, sorts everything by topic, finds repeats, and puts the result into **your own**
Obsidian vault, a plain Markdown folder or Raindrop.io. It runs on your computer with free model keys, works
through thousands of items a little every day, and never loses progress.

```text
────────────────────────────────────────────────────────────
  backlog-digest — 16 of 84 done, 66 in queue
  waiting for subtitles: 44 (0 of 30 collected today) · ready to summarize: 22 · no text: 2
  Sources: 2 (YouTube 1, Raindrop 1) · summaries → D:\Obsidian\Library
  Models: 6 of 7 available · today's limit used up: gemini/gemini-3-flash-preview
────────────────────────────────────────────────────────────
   1. Statistics
   2. Sources: YouTube, folders, articles, Raindrop
   3. Make summaries
   4. Sort by topic + report
   5. Export to Raindrop.io
   6. Schedule
   7. Health check (doctor)
   8. Settings
   q. Quit
```

## What you get

A note per item ([full example](https://github.com/Makss1974/backlog-digest/blob/main/docs/examples/video-summary.en.md)):

> **Measles Explained — Vaccinate or Not?** · Kurzgesagt
>
> **Verdict: Watch** · Score: 5/5 ★★★★★
>
> **About:** how the measles virus bypasses the immune system, and why vaccination matters for herd immunity.
> **Key ideas and facts:** 10 points with numbers · **What is new** · **Grounding:** evidence level, what the
> author relies on, questionable claims · **How much filler:** ~10%, with timestamps · **Who it is for** ·
> **Why this verdict**

Verdicts: *Watch / Read*, *Watch part* (which fragments), *Summary is enough*, *Skip*. The score scale is
strict on purpose — an average item gets 3, and unscientific content goes to *Skip*.

Then, per list:

- **Sorting by topic** — your own topics, "important / the rest" baskets, or a topic tree the model proposes;
  notes move into topic folders after you see a preview.
- **Index note** — the best items, everything by topic with scores, **groups of repeats** (watch only the
  best one), off-topic and unscientific items, pages that failed.
- **Raindrop.io export** — nested collections by list and topic, with the summary in the bookmark note.

## Sources

| Source | How |
|---|---|
| **YouTube** — playlists, channels, single videos | Subtitles via yt-dlp — no Google Cloud, no YouTube keys. No subtitles? Gemini watches the video itself, or speech is recognized on your computer (optional Whisper) |
| **Folders** of books and documents (also Google Drive / OneDrive / Dropbox folders) | PDF, EPUB, FB2, DOCX, ODT, RTF, HTML, TXT, MD; DJVU with DjVuLibre; MOBI, AZW3, DOC with Calibre; audio and video files with optional Whisper |
| **Web articles** — links, browser bookmarks (HTML export), Pocket, Instapaper, CSV | Main text only (no menus, ads, comments) |
| **Raindrop.io** bookmarks | Collections become lists; read-only |

Long books are read in parts and continue after a stop. The same video or book in several lists is summarized
once. YouTube links inside bookmarks become videos, PDF links become books.

## Install

You only need [uv](https://docs.astral.sh/uv/) — a small tool that installs the right Python and
backlog-digest apart from the rest of your system. No administrator rights.

**1. Install uv** (once). Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS and Linux, in a terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install backlog-digest**, then close and reopen the terminal:

```bash
uv tool install backlog-digest
uv tool update-shell
```

**3. Run it:**

```bash
bdig
```

On the first run a wizard sets everything up in a few minutes — language, notes folder, a model key, the first
source, how many per day, a daily schedule — and makes your first summary right away.
`bdig shortcut` puts a desktop shortcut that opens the program in its own window; `bdig doctor` checks that
everything is in place.

**Model key.** The easiest is a free Google Gemini key: open <https://aistudio.google.com/apikey>, sign in,
"Create API key", paste it into the wizard. OpenRouter and DeepSeek keys work too and serve as spares when one
model's daily limit runs out.

Upgrade: `uv tool upgrade backlog-digest`. Uninstall: `bdig autostart off`, then `uv tool uninstall backlog-digest`
(your notes stay where they are).

## Everyday use

Everything is in the menu (`bdig`); the same works as commands, which is handy for scripts:

```bash
bdig add "https://www.youtube.com/playlist?list=..." --name Science   # a YouTube source
bdig add "D:\Books"                                                   # a folder of books
bdig add bookmarks.html --only "Reading/Science"                      # browser bookmarks, one folder
bdig add raindrop "Read later"                                        # Raindrop collections
bdig sync                     # find new items in all sources
bdig process --limit 20       # collect texts and write summaries
bdig export                   # send summaries to Raindrop
bdig run-jobs --force         # run the scheduled jobs now
```

**Schedule.** Menu → 6: what to do (update lists → summaries → report → Raindrop), how many, daily or weekly,
"not before HH:MM". Autostart uses the system scheduler without administrator rights — Task Scheduler on
Windows, launchd on macOS, a systemd timer or cron on Linux. If the computer was off, the job catches up the
next time. A log goes to a file and to a "_Schedule log" note.

**Gentle with limits.** Free models and YouTube have daily limits, so the program works in portions:
1–3 minute pauses between subtitle requests, at most 30 subtitle requests a day (configurable), a queue of spare
models, and if YouTube limits your address it stops asking and keeps summarizing what is already collected.
Ctrl+C stops at any moment; the next run continues where it stopped.

**Languages.** The interface is in English, Ukrainian, French and Spanish. Summaries can be in any language,
regardless of the language of the video or book.

## Your data stays yours

- Everything runs on your computer. Notes are plain Markdown files in your folder.
- Sources are read-only: nothing is changed in YouTube, your bookmarks or Raindrop (the export goes into its own
  collection).
- Keys live in environment variables or in a `.env` file in the settings folder; the program never shows them.
- No telemetry.

| What | Windows | macOS | Linux |
|---|---|---|---|
| Settings `config.toml` and keys `.env` | `%APPDATA%\backlog-digest` | `~/Library/Application Support/backlog-digest` | `~/.config/backlog-digest` |
| Database, cache, logs | `%LOCALAPPDATA%\backlog-digest` | same | `~/.local/share/backlog-digest` |

`BACKLOG_DIGEST_HOME=<folder>` (or `bdig --home <folder>`) keeps everything in one folder.

## More

- [FAQ](https://github.com/Makss1974/backlog-digest/blob/main/docs/FAQ.md): limits, blocked YouTube, scanned books, long paths on Windows and other pitfalls.
- [Development](https://github.com/Makss1974/backlog-digest/blob/main/docs/development.md): running from source, tests, project layout.
- [Specification](https://github.com/Makss1974/backlog-digest/blob/main/docs/ТЗ.md) (Ukrainian).

Grew out of the author's personal tool `yt-digest`, used daily for video summaries.
