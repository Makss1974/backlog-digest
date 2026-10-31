# Changelog

All notable changes are listed here. The project follows [Semantic Versioning](https://semver.org/).

## 1.1.0 — videos without subtitles, speech recognition, audio files

### Sources
- **YouTube videos without subtitles**: Gemini watches the video itself by its link — nothing is downloaded and
  YouTube is not asked from your address. Long videos are watched in parts (`[youtube] part_minutes`, 20 by
  default) that survive a stop. Videos that got "no text" earlier return to the queue once a Gemini key is set.
  `[youtube] no_subtitles = "skip"` turns it off.
- **Speech recognition on your computer** (optional: `backlog-digest[whisper]`, faster-whisper):
  `[youtube] no_subtitles = "whisper"` downloads only the sound of a video without subtitles and recognizes it
  locally, with timestamps; no graphics card needed. The model (`[whisper] model`, `small` by default) downloads
  once on first use. `doctor` shows whether it is installed and the model is ready.
- **Audio and video files in folders** (MP3, M4A, WAV, OGG, OPUS, FLAC, MP4, MKV, WEBM, MOV, AVI): podcasts,
  lectures, audiobooks — recognized with Whisper and summarized like videos.

### Fixes
- A model that hit a per-minute limit is no longer switched off for the whole day, only for the current run.

## 1.0.0 — first public release

### Sources
- **YouTube** playlists, channels and single videos through yt-dlp — no Google Cloud, no YouTube keys.
  Two independent steps: collect subtitles (slow, gentle pauses, daily limit) and write summaries (fast).
  Stops asking YouTube when it limits your address and keeps summarizing what is already collected.
- **Folders** of books and documents: PDF, EPUB, FB2, DOCX, ODT, RTF, HTML, TXT, MD; DJVU with DjVuLibre;
  MOBI, AZW3, DOC with Calibre. Long books are read in parts and continue after a stop.
- **Web articles**: links, browser bookmarks (HTML export, with folder choice), Pocket, Instapaper, CSV.
  Main text only.
- **Raindrop.io** bookmarks (read-only), collections become lists.

### Summaries
- A note per item: what it is about, key ideas and facts, what is new, grounding and questionable claims,
  filler with timestamps, audience, verdict and a strict 1–5 score; pseudoscience goes to "Skip".
- Any summary language; interface in English, Ukrainian, French and Spanish.
- A queue of models with spares (Gemini, OpenRouter, DeepSeek, your own or local), daily quotas remembered.
- Output to an Obsidian vault or a plain Markdown folder.

### Library
- Sorting by topic: your own topics, "important / the rest" baskets or a topic tree proposed by the model,
  with a preview before anything moves.
- A report and an index note per list: the best items, everything by topic, groups of repeats, off-topic,
  unscientific, failed pages.
- Export to Raindrop.io: nested collections by list and topic, only new and changed items are sent.

### Running
- First-run wizard: from zero to the first summary in a few minutes.
- Schedule with catch-up after the computer was off; autostart through Task Scheduler (Windows),
  launchd (macOS), a systemd timer or cron (Linux), without administrator rights.
- A data panel above the menu, a desktop shortcut, `bdig doctor` health check.
- Install with `uv tool install backlog-digest`.
