# FAQ

[Українською](FAQ.uk.md) · [Home](../README.md)

## General

**Do I need Google Cloud or a YouTube API key?**
No. Video lists and subtitles are read the same way a browser sees them. You only need a language model key —
the easiest is a free Gemini key: <https://aistudio.google.com/apikey>.

**Is it free?**
Yes, with free keys. They have daily limits: free Gemini gives from a few dozen to a few hundred requests a day
depending on the model, a free OpenRouter key gives 50. The program keeps a queue of models: when one runs out,
the next one takes over, and used-up models are remembered until the limits reset (midnight Pacific time).
More keys — more summaries per day.

**Does it change anything in my YouTube, bookmarks or Raindrop?**
No, sources are read-only. The Raindrop export goes into its own "Backlog digest" collection.

**Where are my keys, and can anyone see them?**
In environment variables or in a `.env` file in the settings folder (`bdig doctor` shows where). The program
never shows key values and never writes them to logs. There is no telemetry.

**I stopped the program halfway. Is everything lost?**
No. After Ctrl+C (or a computer that was switched off) the next run continues from the same place, even in the
middle of a long book.

## YouTube

**"YouTube limited requests" — what happened?**
YouTube limits an internet address that asks for too many subtitles — both too often and too many per day.
That is why the program waits 1–3 minutes between subtitle requests and makes at most 30 a day. If a limit hits
anyway, it stops asking YouTube (retries would only make it longer) and keeps summarizing what is already
collected. Usually waiting a day is enough; the schedule carries on by itself.

**Can I run it on a cloud server?**
Better not: from cloud provider addresses (AWS and the like) YouTube blocks subtitles almost immediately.
The program is meant for a home computer.

**A video has no subtitles.**
With a Gemini key the program does not give up: Gemini watches the video itself by its link (Google opens it,
not your address, and nothing is downloaded). Long videos are watched in parts of 20 minutes
(`[youtube] part_minutes`), and the parts are kept, so a stop does not start the video over. A video costs
about 100 model tokens per second, so a free Gemini key handles a few such videos a day; when its limit is
used up, these videos wait until the next day and everything else goes on. Without a Gemini key, or with
`[youtube] no_subtitles = "skip"`, such a video gets "no text". Videos that got "no text" earlier return to the
queue by themselves once a Gemini key is added.

**Can it recognize speech on my own computer, without Gemini limits?**
Yes, with the optional speech recognition part (Whisper, about 100 MB):

```bash
uv tool install "backlog-digest[whisper]"
```

then set `[youtube] no_subtitles = "whisper"` in `config.toml`. For a video without subtitles the program
downloads only its sound (one more request to YouTube, counted in the daily limit), recognizes it here and
makes a summary with any model — the text costs as little as subtitles. The recognition model downloads once,
on first use, into the program's data folder: `[whisper] model = "small"` (≈480 MB) by default; `base`
(≈145 MB) is faster and rougher, `large-v3-turbo` (≈1.6 GB) is the most accurate. No graphics card is needed;
on a usual processor `small` takes roughly a quarter of the video's length. The recognized text is kept, so a
stop does not repeat the work.

**Audio and video files in a folder?**
With speech recognition installed, a folder source also takes MP3, M4A, WAV, OGG, OPUS, FLAC, MP4, MKV, WEBM,
MOV and AVI: podcasts, lectures, audiobooks, voice notes. Without it these files wait with "no text" and
return to the queue once it is installed. The files themselves are never changed.

**Why is it so slow?**
Subtitles are slow on purpose (see above). Summaries of what is already collected are fast: menu → 3 →
"Only summaries", or `bdig summarize`. A daily schedule works best: a little every day, without you.

## Books and articles

**A book got "no text".**
Most often it is a scan without a text layer (just page images). Scan recognition is planned. An empty or
damaged file also ends up here.

**DJVU, MOBI, AZW3 or DOC files are not read.**
They need free programs: DjVuLibre (DJVU) and Calibre (MOBI, AZW3, DOC). After installing them, `bdig doctor`
shows whether they were found (it also looks in the Windows registry, not only on drive C:).

**An article got "no text".**
The page needs a login, is paywalled, or is gone. Temporary site errors are retried later automatically.

**I have tens of thousands of bookmarks.**
Take them in parts: after reading the bookmarks file the program shows its folders — pick the ones you want
(or use `--only "Folder"`). Free models give a few hundred summaries a day, so 50,000 bookmarks take months.

## Scores and summaries

**Why are the scores so strict?**
Without a clear scale a model gives 5/5 to almost everything. Here an average item gets 3 and pseudoscience
gets "Skip". "Summary is enough" means the essentials are in the summary, so there is no need to watch or read
the whole thing.

**Can summaries be in a language other than English, Ukrainian, French or Spanish?**
Yes, any language (menu → 8 → 2), regardless of the source language. Note labels for such a language are
translated by the model once; you can fix them in `note_labels/<code>.toml` in the settings folder.

**Can I use my own or a local model (Ollama)?**
Yes: in `config.toml`, section `[models]` — the provider order (`order`) and your own profiles
`[models.<name>]` with `base_url`, `key_env`, `models`. There is a ready `ollama` profile — add it to `order`.
Small local models (1–4B parameters) write noticeably worse and sometimes loop.

## Windows

**After installing, `bdig` is not found.**
Close and reopen the terminal: windows that were already open do not see the updated PATH.
If that does not help, run `uv tool update-shell` and reopen the terminal again.

**Note paths are oddly shortened ("A · B · C").**
Windows limits the length of a file path (about 260 characters). Very deep bookmark folders are merged into one,
and long names are cut at a word.

**"Permission denied" when writing a note.**
The note is open in a program that does not let it be overwritten (some editors and viewers do this).
Close it — the next run retries by itself.

## Schedule

**The computer was off at the job's time.**
No problem: the job runs at the first start after its time.

**How do I check that the schedule works?**
Menu → 6 shows "Autostart: on ✅" and the result of the last run. The log is the "_Schedule log" note in your
vault and the files in the `logs` folder of the program folder.

**How do I add Telegram?**
Without logging in: in Telegram Desktop, Settings → Advanced → Export Telegram data (or in a chat: ⋮ → Export
chat history), format HTML (the default) or JSON; tick files if you want documents and voice messages. Then menu → 2 → 5 and the
export folder (the one with `export_results.html` or `result.json`). Links, long posts and files become items; each chat is a list.
A newer export into the same folder adds only what is new. The program never logs in and changes nothing in
Telegram.

**How do I remove everything?**
`bdig autostart off`, then `uv tool uninstall backlog-digest`. Delete settings and the database together with
the program folder (`bdig doctor` shows where it is); your summaries stay in your folder.
