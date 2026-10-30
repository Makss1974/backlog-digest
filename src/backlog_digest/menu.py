"""Інтерактивне меню в терміналі (запуск без параметрів). Вихід — q."""
from __future__ import annotations

import getpass
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__, config, db, doctor, i18n, keys, llm, pipeline
from .i18n import t
from .paths import Paths

LINE = "─" * 60
QUIT_KEYS = ("q", "й")   # «й» — та сама клавіша в українській розкладці


class Menu:
    def __init__(self, paths: Paths, cfg: dict[str, Any], conn: sqlite3.Connection) -> None:
        self.paths = paths
        self.cfg = cfg
        self.conn = conn

    def items(self) -> list[tuple[str, str, Callable[[], None]]]:
        """Пункти меню: (клавіша, назва, дія). Будуються щоразу — назви залежать від мови."""
        return [
            ("1", t("menu.stats"), self.show_stats),
            ("2", t("menu.sources"), self.sources_menu),
            ("3", t("menu.summaries"), self.process),
            ("4", t("menu.sorting"), soon(4)),
            ("5", t("menu.raindrop"), soon(4)),
            ("6", t("menu.schedule"), soon(5)),
            ("7", t("menu.doctor"), self.run_doctor),
            ("8", t("menu.settings"), self.settings),
        ]

    def run(self) -> None:
        while True:
            print(f"\n{LINE}\n  {t('app.title', version=__version__)} — {self.queue_line()}\n{LINE}")
            items = self.items()
            for key, title, _ in items:
                print(f"  {key:>2}. {title}")
            print(f"   q. {t('menu.quit')}")
            try:
                choice = ask(f"\n{t('menu.prompt')} ").lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not choice:
                continue
            if choice in QUIT_KEYS:
                return
            action = next((a for k, _, a in items if k == choice), None)
            if action is None:
                print(t("menu.no_such_item"))
                continue
            print()
            try:
                action()
                ask(f"\n{t('menu.press_enter')}")
            except KeyboardInterrupt:
                print(f"\n{t('menu.interrupted')}")
            except EOFError:
                return

    def queue_line(self) -> str:
        counts = db.status_counts(self.conn)
        total = sum(counts.values())
        if not total:
            return t("menu.queue_empty")
        return t("menu.queue", done=counts[db.DONE], total=total,
                 queued=sum(counts[s] for s in db.QUEUED))

    # ------------------------------------------------------------ дії

    def show_stats(self) -> None:
        counts = db.status_counts(self.conn)
        sources = db.list_sources(self.conn)
        print(t("stats.title"))
        print(f"  {pair(t('stats.sources'), len(sources))}")
        print(f"  {pair(t('stats.items'), sum(counts.values()))}")
        for status in db.STATUSES:
            print(f"    {pair(t(f'status.{status}'), counts[status])}")
        language = self.cfg["general"]["summary_language"]
        to_fetch, to_summarize = db.stage_counts(self.conn, language)
        if to_fetch or to_summarize:
            print(f"\n  {pair(t('stats.to_fetch'), to_fetch)}")
            print(f"  {pair(t('stats.to_summarize'), to_summarize)}")
        if to_fetch:
            per_day = self.cfg["limits"]["per_day"]
            days = pipeline.queue_eta_days(self.conn, per_day, language)
            print(f"\n{t('stats.eta', days=days, per_day=per_day)}")
        if not sources:
            print(f"\n{t('stats.no_sources')}")

    def run_doctor(self) -> None:
        doctor.run(self.paths)

    # ------------------------------------------------------------ джерела

    def sources_menu(self) -> None:
        while True:
            sources = db.list_sources(self.conn)
            print(t("sources.title"))
            if not sources:
                print(f"  {t('sources.none')}")
            for s in sources:
                total, queued = self.conn.execute(
                    "SELECT COUNT(*), SUM(status IN ('new', 'error')) FROM items WHERE source_id=?",
                    (s["id"],)).fetchone()
                kind = t(f"sources.type_{s['type']}")
                print(f"  • {s['name']} ({kind}): {t('sources.counts', total=total, queued=queued or 0)}")
            print(f"\n  1. {t('sources.add_youtube')}")
            print(f"  2. {t('sources.add_folder')}")
            print(f"  3. {t('sources.sync')}")
            print(f"  4. {t('sources.delete')}")
            print(f"  0. {t('menu.back')}")
            choice = ask_int(t("settings.choose"), 0, 0, 4)
            if choice == 0:
                return
            if choice == 1:
                self.add_youtube()
            elif choice == 2:
                self.add_folder()
            elif choice == 3:
                pipeline.sync_all(self.conn)
            elif choice == 4:
                self.delete_source(sources)
            print()

    def add_youtube(self) -> None:
        print(t("sources.urls_help"))
        urls = ask_lines()
        if not urls:
            return
        names = {s["name"] for s in db.list_sources(self.conn)}
        default = next(n for n in (f"YouTube {i}" if i else "YouTube" for i in range(100)) if n not in names)
        name = ask(f"{t('sources.name')} [{default}]: ") or default
        if name in names:
            print(t("sources.name_taken", name=name))
            return
        folder = ask(f"{t('sources.folder')} [{name}]: ") or name
        sid = pipeline.add_youtube_source(self.conn, name, urls, folder)
        print(t("sources.added", name=name))
        source = db.get_source(self.conn, sid)
        if source:
            new, _ = pipeline.sync_source(self.conn, source)
            print(t("sources.synced", new=new))

    def add_folder(self) -> None:
        print(t("sources.folder_help"))
        raw = ask(f"{t('settings.output_path')}: ").strip('"')
        if not raw:
            return
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_dir():
            print(t("sources.folder_not_found", path=raw))
            return
        names = {s["name"] for s in db.list_sources(self.conn)}
        default = path.name or str(path)
        name = ask(f"{t('sources.name')} [{default}]: ") or default
        if name in names:
            print(t("sources.name_taken", name=name))
            return
        folder = ask(f"{t('sources.folder')} [{name}]: ") or name
        sid = pipeline.add_folder_source(self.conn, name, str(path), folder)
        print(t("sources.added_folder", name=name))
        source = db.get_source(self.conn, sid)
        if source:
            new, _ = pipeline.sync_source(self.conn, source)
            print(t("sources.synced", new=new))

    def delete_source(self, sources: list[dict[str, Any]]) -> None:
        if not sources:
            return
        for i, s in enumerate(sources, 1):
            print(f"  {i}. {s['name']}")
        n = ask_int(t("settings.choose"), 0, 0, len(sources))
        if not n:
            return
        s = sources[n - 1]
        if ask_yes(t("sources.confirm_delete", name=s["name"])):
            removed = db.delete_source(self.conn, s["id"])
            print(t("sources.deleted", name=s["name"], items=removed))

    # ------------------------------------------------------------ конспекти

    def process(self) -> None:
        to_fetch, to_summarize = db.stage_counts(self.conn, self.cfg["general"]["summary_language"])
        queued = db.queued_by_source(self.conn)
        if not queued:
            print(t("process.empty_queue"))
            return
        print(t("process.waiting", to_fetch=to_fetch, to_summarize=to_summarize))
        print(f"  1. {t('process.mode_all')}")
        print(f"  2. {t('process.mode_fetch')}")
        print(f"  3. {t('process.mode_summarize')}")
        print(f"  0. {t('menu.back')}")
        mode = ask_int(t("settings.choose"), 1, 0, 3)
        if not mode:
            return
        stage = (pipeline.ALL, pipeline.FETCH, pipeline.SUMMARIZE)[mode - 1]
        if stage != pipeline.FETCH and not self.cfg["output"]["path"] and not self.choose_output():
            return
        source_id = None
        if len(queued) > 1:
            print(f"  0. {t('process.all_sources')}")
            for i, s in enumerate(queued, 1):
                print(f"  {i}. {s['name']} — {t('sources.counts_queued', queued=s['queued'])}")
            n = ask_int(t("settings.choose"), 0, 0, len(queued))
            source_id = queued[n - 1]["id"] if n else None
        limit = ask_int(t("process.how_many"), self.cfg["limits"]["per_run"], 1, 100000)
        print(t("process.hint_stop_short" if stage == pipeline.SUMMARIZE else "process.hint_stop") + "\n")
        pipeline.Processor(self.conn, self.cfg, self.paths).run(limit, source_id, stage)

    # ------------------------------------------------------------ налаштування

    def settings(self) -> None:
        while True:
            g = self.cfg["general"]
            print(t("settings.title"))
            print(f"  1. {pair(t('settings.ui_language'), i18n.language_name(g['ui_language']))}")
            summary = i18n.SUMMARY_LANGUAGES.get(g["summary_language"], g["summary_language"])
            print(f"  2. {pair(t('settings.summary_language'), summary)}")
            output = self.cfg["output"]["path"] or t("settings.not_set")
            print(f"  3. {pair(t('settings.output'), output)}")
            print(f"  4. {t('settings.model_keys')}")
            print(f"  5. {t('settings.show_paths')}")
            print(f"  6. {t('settings.open_config_dir')}")
            print(f"  0. {t('menu.back')}")
            choice = ask_int(t("settings.choose"), 0, 0, 6)
            if choice == 0:
                return
            if choice == 1:
                self.choose_ui_language()
            elif choice == 2:
                self.choose_summary_language()
            elif choice == 3:
                self.choose_output()
            elif choice == 4:
                self.model_keys()
            elif choice == 5:
                self.show_paths()
            elif choice == 6:
                open_path(self.paths.config_dir)
            print()

    def choose_output(self) -> bool:
        """Тека, куди складати конспекти (сховище Obsidian або будь-яка тека). True — задано."""
        print(t("settings.output_help"))
        current = self.cfg["output"]["path"]
        raw = ask(f"{t('settings.output_path')}" + (f" [{current}]" if current else "") + ": ").strip('"')
        if not raw:
            return bool(current)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            print(t("settings.output_not_absolute"))
            return bool(current)
        self.cfg["output"]["path"] = str(path)
        self.cfg["output"]["kind"] = "obsidian" if (path / ".obsidian").is_dir() else "markdown"
        self.save()
        return True

    def model_keys(self) -> None:
        providers = [llm.BUILTIN[n] for n in ("gemini", "openrouter", "deepseek")]
        for i, p in enumerate(providers, 1):
            src = keys.secret_source(p.key_env or "", self.paths)
            state = t("settings.key_set") if src else t("settings.key_not_set")
            print(f"  {i}. {p.name.capitalize()} — {state}  ({p.signup_url})")
        print(f"  0. {t('menu.back')}")
        n = ask_int(t("settings.choose"), 0, 0, len(providers))
        if not n:
            return
        p = providers[n - 1]
        value = getpass.getpass(f"{t('settings.paste_key', provider=p.name.capitalize())} ").strip()
        if not value or not p.key_env:
            return
        print(t("settings.checking_key"))
        ok, detail = llm.check_key(p, value)
        if ok:
            print(t("settings.key_ok"))
        else:
            print(t("settings.key_bad", detail=detail))
            if not ask_yes(t("settings.save_anyway")):
                return
        path = keys.set_secret(p.key_env, value, self.paths)
        print(t("settings.saved", path=str(path)))
        if p.name not in self.cfg["models"]["order"]:
            self.cfg["models"]["order"].append(p.name)
            self.save()

    def choose_ui_language(self) -> None:
        langs = list(i18n.UI_LANGUAGES)
        for i, lang in enumerate(langs, 1):
            print(f"  {i}. {i18n.language_name(lang)}")
        current = langs.index(self.cfg["general"]["ui_language"]) + 1
        lang = langs[ask_int(t("settings.choose"), current, 1, len(langs)) - 1]
        self.cfg["general"]["ui_language"] = lang
        i18n.set_language(lang)
        self.save()

    def choose_summary_language(self) -> None:
        codes = list(i18n.SUMMARY_LANGUAGES)
        cells = [f"{i:>2}. {i18n.SUMMARY_LANGUAGES[code]} ({code})" for i, code in enumerate(codes, 1)]
        print_columns(cells)
        print(f"   0. {t('settings.other_language')}")
        current = self.cfg["general"]["summary_language"]
        default = codes.index(current) + 1 if current in codes else 0
        n = ask_int(t("settings.choose"), default, 0, len(codes))
        if n:
            code = codes[n - 1]
        else:
            code = ask(f"{t('settings.language_code')} ")
            if not code:
                return
        old = current
        self.cfg["general"]["summary_language"] = code
        try:
            config.validate(self.cfg)
        except config.ConfigError as e:
            self.cfg["general"]["summary_language"] = old
            print(e.message())
            return
        self.save()

    def show_paths(self) -> None:
        print(f"  {pair(t('settings.path_config'), self.paths.config_file)}")
        print(f"  {pair(t('settings.path_secrets'), self.paths.env_file)}")
        print(f"  {pair(t('settings.path_data'), self.paths.data_dir)}")

    def save(self) -> None:
        path = config.save(self.paths, self.cfg)
        print(t("settings.saved", path=str(path)))


def pair(label: str, value: object) -> str:
    """«Назва: значення» за правилами мови (у французькій — пробіл перед двокрапкою)."""
    return t("format.label_value", label=label, value=value)


def print_columns(cells: list[str], columns: int = 3) -> None:
    """Довгий список — у кілька колонок, щоб уміщався на екрані (нумерація йде вниз по колонках)."""
    rows = -(-len(cells) // columns)
    width = max(len(c) for c in cells) + 3
    for r in range(rows):
        line = "".join(cells[i].ljust(width) for i in range(r, len(cells), rows))
        print(f"  {line.rstrip()}")


def soon(stage: int) -> Callable[[], None]:
    return lambda: print(t("menu.coming_soon", stage=stage))


# ---------------------------------------------------------------- введення

def ask(prompt: str) -> str:
    # BOM трапляється, коли відповіді подають конвеєром з PowerShell
    return input(prompt).strip().lstrip("\ufeff")


def ask_int(prompt: str, default: int, lo: int, hi: int) -> int:
    while True:
        s = ask(f"{prompt} [{default}]: ")
        if not s:
            return default
        if s.isdigit() and lo <= int(s) <= hi:
            return int(s)
        print(t("menu.enter_number", lo=lo, hi=hi))


def ask_lines() -> list[str]:
    """Кілька рядків, по одному; порожній рядок — кінець."""
    lines: list[str] = []
    while True:
        s = ask("  > ")
        if not s:
            return lines
        lines.append(s)


def ask_yes(prompt: str) -> bool:
    return ask(f"{prompt} ({t('answers.yes_no_hint')}) ").lower() in i18n.words("answers.yes")


def open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined,unused-ignore]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    print(t("menu.opened", path=str(path)))
