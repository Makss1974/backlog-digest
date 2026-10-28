"""Інтерактивне меню в терміналі (запуск без параметрів). Вихід — q."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__, config, db, doctor, i18n
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
            ("2", t("menu.sources"), soon(2)),
            ("3", t("menu.summaries"), soon(2)),
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
        if not sources:
            print(f"\n{t('stats.no_sources')}")

    def run_doctor(self) -> None:
        doctor.run(self.paths)

    def settings(self) -> None:
        while True:
            g = self.cfg["general"]
            print(t("settings.title"))
            print(f"  1. {pair(t('settings.ui_language'), i18n.language_name(g['ui_language']))}")
            summary = i18n.SUMMARY_LANGUAGES.get(g["summary_language"], g["summary_language"])
            print(f"  2. {pair(t('settings.summary_language'), summary)}")
            print(f"  3. {t('settings.show_paths')}")
            print(f"  4. {t('settings.open_config_dir')}")
            print(f"  0. {t('menu.back')}")
            choice = ask_int(t("settings.choose"), 0, 0, 4)
            if choice == 0:
                return
            if choice == 1:
                self.choose_ui_language()
            elif choice == 2:
                self.choose_summary_language()
            elif choice == 3:
                self.show_paths()
            elif choice == 4:
                open_path(self.paths.config_dir)
            print()

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
        for i, code in enumerate(codes, 1):
            print(f"  {i}. {i18n.SUMMARY_LANGUAGES[code]} ({code})")
        print(f"  0. {t('settings.other_language')}")
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


def ask_yes(prompt: str) -> bool:
    return ask(f"{prompt} ({t('answers.yes_no_hint')}) ").lower() in i18n.words("answers.yes")


def open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined,unused-ignore]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    print(t("menu.opened", path=str(path)))
