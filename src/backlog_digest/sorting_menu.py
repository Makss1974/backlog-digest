"""Меню сортування списку за темами (етап 4). Окремо від menu.py, бо тут довгий діалог."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import db, library, pipeline
from .i18n import t
from .llm import LLMError
from .paths import Paths

CRITERIA = ("crit_content", "crit_value", "crit_depth", "crit_evidence")


def run(conn: sqlite3.Connection, cfg: dict[str, Any], paths: Paths) -> None:
    from .menu import ask, ask_int, ask_yes

    language = cfg["general"]["summary_language"]
    lists = db.done_lists(conn, language)
    if not lists:
        print(t("sort.no_lists"))
        return
    print(t("sort.pick_list"))
    for i, r in enumerate(lists, 1):
        extra = f", {t('sort.unsorted', n=r['unsorted'])}" if r["unsorted"] and r["unsorted"] < r["done"] else ""
        print(f"  {i:>3}. {r['source_name']} / {r['list_name'] or '—'} — {t('sort.done', n=r['done'])}{extra}")
    row = lists[ask_int(t("settings.choose"), 1, 1, len(lists)) - 1]
    source_id, name = int(row["source_id"]), str(row["list_name"])
    proc = pipeline.Processor(conn, cfg, paths)
    if not proc.check_ready():
        return
    other = proc.labels()["other_topic"]
    items = db.done_items(conn, source_id, name, language)
    scheme = db.get_scheme(conn, source_id, name)
    try:
        if not scheme or not scheme["topics"]:
            print(f"\n  1. {t('sort.sort_list')}")
            print(f"  2. {t('sort.report_only')}")
            print(f"  0. {t('menu.back')}")
            choice = ask_int(t("settings.choose"), 1, 0, 2)
            if choice == 0:
                return
            if choice == 2:
                finish(conn, proc, source_id, name, language, ask_yes, ask_report=False)
                return
        if scheme and scheme["topics"]:
            mode = t(f"sort.mode_{scheme['mode']}")
            print("\n" + t("sort.has_scheme", mode=mode))
            print(library.tree_text(scheme["topics"]))
            unsorted = [it for it in items if not it.get("topic")]
            print(f"\n  1. {t('sort.assign_unsorted', n=len(unsorted))}")
            print(f"  2. {t('sort.new_scheme')}")
            print(f"  3. {t('sort.report_only')}")
            print(f"  0. {t('menu.back')}")
            choice = ask_int(t("settings.choose"), 1, 0, 3)
            if choice == 0:
                return
            if choice == 1:
                if unsorted:
                    assign = library.classify(proc.chain, unsorted, scheme["topics"], scheme["criteria"], other)
                    print(t("sort.moved", n=library.apply(conn, unsorted, assign, proc.rewrite)))
                finish(conn, proc, source_id, name, language, ask_yes)
                return
            if choice == 3:
                finish(conn, proc, source_id, name, language, ask_yes, ask_report=False)
                return
        new_scheme(conn, proc, items, source_id, name, language, other, ask, ask_int, ask_yes)
    except (LLMError, ValueError) as e:
        print(t("sort.model_failed", detail=str(e)[:300]))


def new_scheme(conn: sqlite3.Connection, proc: pipeline.Processor, items: list[dict[str, Any]], source_id: int,
               list_name: str, language: str, other: str, ask: Any, ask_int: Any, ask_yes: Any) -> None:
    print(f"\n{t('sort.mode_title')}")
    for i, mode in enumerate(library.MODES, 1):
        print(f"  {i}. {t(f'sort.mode_{mode}')} — {t(f'sort.mode_{mode}_help')}")
    mode = library.MODES[ask_int(t("settings.choose"), 3, 1, 3) - 1]

    print(f"\n{t('sort.criteria_title')}")
    for i, key in enumerate(CRITERIA, 1):
        print(f"  {i}. {t(f'sort.{key}')}")
    picked = [t(f"sort.{CRITERIA[int(x) - 1]}") for x in ask(f"{t('sort.criteria_numbers')} ").replace(",", " ").split()
              if x.isdigit() and 1 <= int(x) <= len(CRITERIA)]
    own = ask(f"{t('sort.own_rule')}\n  > ")
    criteria = " ".join(picked + ([own] if own else []))

    if mode == "manual":
        topics = library.parse_tree(edit_text(t("sort.manual_template")), other)
    elif mode == "importance":
        names = ask(f"{t('sort.baskets')} [{t('sort.baskets_default')}]: ") or t("sort.baskets_default")
        topics = library.normalize_topics([n.strip() for n in names.split(",")], other)
        if not own:
            rule = ask(f"{t('sort.basket_rule')} ")
            criteria = " ".join(x for x in (criteria, rule) if x)
    else:
        n = ask(f"{t('sort.how_many')} [4–8]: ") or "4–8"
        print(t("sort.proposing"))
        topics = library.propose_topics(proc.chain, items, n, criteria, language, other)
        print(f"\n{t('sort.proposal')}\n{library.tree_text(topics)}")
        if ask_yes(t("sort.edit_topics")):
            topics = library.parse_tree(edit_text(t("sort.edit_template") + "\n"
                                                  + library.draft_text(topics, other)), other)
    if len(topics) < 2:
        print(t("sort.too_few"))
        return
    print(f"\n{t('sort.topics')}\n{library.tree_text(topics)}")
    print(t("sort.classifying_all", n=len(items)))
    assign = library.classify(proc.chain, items, topics, criteria, other)
    print(f"\n{t('sort.preview_head')}")
    for label, count, examples in library.preview(items, assign, topics):
        print(f"  {label[:48]:<48} {count:>4}  {examples}")
    if not ask_yes("\n" + t("sort.confirm")):
        print(t("sort.cancelled"))
        return
    db.save_scheme(conn, source_id, list_name, mode, topics, criteria)
    print(t("sort.moved", n=library.apply(conn, items, assign, proc.rewrite)))
    print(t("sort.auto_next"))
    finish(conn, proc, source_id, list_name, language, ask_yes)


def finish(conn: sqlite3.Connection, proc: pipeline.Processor, source_id: int, list_name: str, language: str,
           ask_yes: Any, ask_report: bool = True) -> None:
    """Звіт (повтори, не в темі — один запит до моделі) й індексна нотатка списку."""
    from .menu import open_file

    if not ask_report or ask_yes(t("sort.ask_report")):
        print(t("sort.reporting"))
        report = library.make_report(conn, proc.chain, source_id, list_name, language)
        print(t("sort.report_done", similar=len(report["similar"]), off_topic=len(report["off_topic"])))
    path = proc.write_index(source_id, list_name)
    print(t("sort.index_done", path=str(path)))
    if ask_yes(t("sort.open_index")):
        open_file(path)


def edit_text(initial: str) -> str:
    """Текст у звичайному редакторі (Блокнот, TextEdit, nano): відкриваємо й чекаємо, поки закриють.
    Не вийшло — вводимо в консолі. Рядки з # — пояснення, їх прибирає parse_tree."""
    from .menu import ask_lines

    fd, name = tempfile.mkstemp(suffix=".txt", prefix="backlog-digest-")
    path = Path(name)
    with os.fdopen(fd, "w", encoding="utf-8-sig" if sys.platform == "win32" else "utf-8") as f:
        f.write(initial + "\n")
    try:
        print(t("sort.editor_opened"))
        if sys.platform == "win32":
            subprocess.run(["notepad.exe", str(path)], check=True)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-W", "-t", str(path)], check=True)
        else:
            editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano") or "vi"
            subprocess.run([editor, str(path)], check=True)
        return path.read_text(encoding="utf-8-sig")
    except (OSError, subprocess.SubprocessError):
        print(t("sort.editor_failed"))
        return "\n".join(ask_lines())
    finally:
        path.unlink(missing_ok=True)
