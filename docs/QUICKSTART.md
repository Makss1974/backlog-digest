# Getting started: from zero to your first summary

[Українською](QUICKSTART.uk.md) · [Home](../README.md) · [FAQ](FAQ.md)

About 10 minutes. You need a computer with Windows, macOS or Linux, internet, and a Google account (for a free
model key). No programming — everything happens in a menu.

## 1. Install

**Windows.** Press Start, type `PowerShell`, open it and paste (Enter after each line):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close the PowerShell window, open a new one and paste:

```powershell
uv tool install backlog-digest
```

```powershell
uv tool update-shell
```

Close and reopen PowerShell once more.

**macOS and Linux** — the same in Terminal, with a different first line:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> `uv` is a small program that installs the right Python and backlog-digest separately from the rest of the
> system. No administrator rights are needed.

## 2. First run

```bash
bdig
```

A setup wizard opens. It asks for:

1. The **language** of the interface and of the summaries.
2. The **folder for summaries.** If you use Obsidian, pick your vault (the folder that contains `.obsidian`).
   Otherwise any folder, e.g. `D:\Summaries`: plain Markdown files go there.
3. A **model key.** The easiest is a free Gemini key:
   1. open <https://aistudio.google.com/apikey> and sign in with your Google account;
   2. click **Create API key** and copy it;
   3. paste it into the wizard (the characters are hidden — that is expected). The program checks the key and
      stores it only on your computer.
4. The **first source** — see the next section.
5. **How many per day** and whether to turn on the **daily schedule**.

At the end the wizard makes your first summary right away. Run it again: menu → 8 → 8.

**Desktop shortcut:** `bdig shortcut` — then the program opens with a double click.

## 3. Giving the program access to your lists

The program never changes your YouTube, bookmarks or Telegram — it only reads them.

### YouTube

Menu → **2 Sources** → **1 YouTube** → paste links (one per line, an empty line when done).

| What | Which link | Sign-in needed |
|---|---|---|
| Public playlist | the playlist page address (`...playlist?list=...`) | no |
| Unlisted playlist | the same | no |
| Channel | `https://www.youtube.com/@name` or just `@name` | no |
| Single video | the video address | no |
| **Private playlist** | the playlist address | yes — see below |
| **Watch Later** | `WL` | yes — see below |

**How to copy a playlist link:** on youtube.com → "You" on the left → "Playlists" → open the playlist → copy the
address from the browser bar.

**Private lists.** The program notices a private list (🔒) by itself and offers two ways:

1. **The simplest and safest — make the playlist Unlisted.** On YouTube: open the playlist → ⋮ (or the pencil)
   → **Visibility** → **Unlisted**. It does not show up in search or on your channel, but the program can read
   it without signing in.
   Watch Later can't be made unlisted, but it can be copied: open it → ⋮ → **Add all to…** → create a new
   unlisted playlist and give that one to the program.
2. **A cookies.txt file** — if the list must stay private:
   1. in the browser where you are signed in to YouTube, install the **Get cookies.txt LOCALLY** extension
      (Chrome, Edge, Firefox);
   2. open youtube.com, click the extension icon → **Export** → save the file, e.g. `D:\Summaries\cookies.txt`;
   3. when the program asks, give the path to this file. It checks the access and shows how many videos it sees.

   ⚠️ This file is a key to your Google account. Never send it to anyone and don't put it in shared folders.
   The program only reads it on your computer. When access stops working (after a few weeks), export again into
   the same file.

As a command: `bdig add "https://www.youtube.com/playlist?list=..." --cookies "D:\Summaries\cookies.txt"`.

### Books and documents

Menu → 2 → **2 Folder** → the full path to the folder (a synced one is fine: Google Drive, OneDrive, Dropbox).
Subfolders become separate lists. PDF, EPUB, FB2, DOCX, ODT, RTF, TXT, MD, HTML are read right away.

### Browser bookmarks and articles

First save your bookmarks to a file:
- **Chrome / Edge:** `Ctrl+Shift+O` → ⋮ at the top right → **Export bookmarks**;
- **Firefox:** `Ctrl+Shift+O` → **Import and Backup** → **Export Bookmarks to HTML**.

Then menu → 2 → **3 Articles** → the path to the saved file. The program shows your bookmark folders — choose
the ones you want (thousands of bookmarks are best taken in parts). You can also just paste article links.

### Raindrop.io

1. <https://app.raindrop.io/settings/integrations> → **Create new app** (any name) → open it →
   **Create test token** → copy it.
2. Menu → 2 → **4 Raindrop** → paste the token → choose collections.

### Telegram (Saved Messages, channels, groups)

Menu → 2 → **5 Telegram**, then choose one of two ways:

**A public channel — just a link.** Choose **2** and paste `t.me/name` (or `@name`), one per line. Anyone can
read a public channel without an account, so no export and no sign-in are needed. New posts are picked up by
themselves on every update (the schedule); a big channel's older posts are read in parts over several updates.
As a command: `bdig add t.me/name`.

**Saved Messages, your private channels, groups — an export.**
1. In **Telegram Desktop**: Settings → Advanced → **Export Telegram data** (or in a chat ⋮ → Export chat
   history). HTML format is fine. Tick what you need: **Private channels** for your own note channels,
   **Public channels**, and files if you want documents and voice messages.
2. Choose **1** → the export folder → the numbers of the chats you want (Enter — all). A newer export into the
   same folder adds only what is new.

## 4. Summaries

Menu → **3 Make summaries** → Enter. The program shows what it is doing and writes notes into your folder.
Ctrl+C stops at any moment — next time it continues from the same place.

**Why not everything at once.** Free keys have daily limits and YouTube dislikes frequent requests, so the
program works in portions: up to about 30 videos and a few hundred articles a day. The most convenient way is
the schedule: menu → **6** → add a job and turn on autostart. From then on it all happens by itself, daily.

## 5. What next

- **Read the notes** in Obsidian or any Markdown editor: score, verdict "watch / read / summary is enough /
  skip", key points, questionable claims.
- **Sort by topic + report** (menu → 4): topics, repeats, the best — in the list's index note.
- **Ask the library** (menu → 9): an answer from all your summaries, with links to the notes.
- **Claude, Cursor and other assistants** can read your library: `bdig mcp --config`.
- **New items in your lists** are found by the schedule or by menu → 2 → 6.
- **Without the computer:** cloud mode on GitHub (optional) — menu → 6 → 6, step by step in the folder's README.md.

## If something is wrong

- **`bdig` is not found** — close and reopen the terminal; if that doesn't help — `uv tool update-shell`.
- **Check everything:** menu → 7 or `bdig doctor` — shows what is missing and what to do about it.
- Other questions — in the [FAQ](FAQ.md).
