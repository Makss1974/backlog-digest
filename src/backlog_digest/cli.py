"""Точка входу: backlog-digest / bdig [--lang uk|en|fr|es] [menu | doctor]."""
from __future__ import annotations

import argparse
import sys
from typing import Any

from . import __version__, config, db, doctor, i18n
from .i18n import t
from .paths import get_paths


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    argv = sys.argv[1:] if argv is None else argv
    paths = get_paths()

    # Мову треба знати ще до розбору аргументів: від неї залежать тексти довідки.
    cfg: dict[str, Any] | None
    try:
        cfg = config.load(paths)
        cfg_error = None
    except config.ConfigError as e:
        cfg, cfg_error = None, e
    i18n.set_language(_lang_from_argv(argv) or (cfg["general"]["ui_language"] if cfg else i18n.detect_language()))

    args = build_parser().parse_args(argv)
    if args.lang and cfg:
        cfg["general"]["ui_language"] = args.lang   # лише на цей запуск, у файл не записуємо

    if args.command == "doctor":
        return doctor.run(paths, network=not args.offline)

    if cfg_error or cfg is None:
        print(t("cli.config_error", error=cfg_error.message() if cfg_error else ""), file=sys.stderr)
        print(t("cli.run_doctor"), file=sys.stderr)
        return 2
    if config.ensure(paths):
        print(t("cli.config_created", path=str(paths.config_file)))
        cfg = config.load(paths)
        if args.lang:
            cfg["general"]["ui_language"] = args.lang

    from .menu import Menu

    try:
        conn = db.connect(paths.db_file)
    except db.DbError as e:
        print(t("cli.db_error", error=str(e)), file=sys.stderr)
        return 2
    try:
        Menu(paths, cfg, conn).run()
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backlog-digest", description=t("cli.description"))
    parser.add_argument("--version", action="version", version=f"backlog-digest {__version__}")
    parser.add_argument("--lang", choices=i18n.UI_LANGUAGES, help=t("cli.help_lang"))
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("menu", help=t("cli.help_menu"))
    p = sub.add_parser("doctor", help=t("cli.help_doctor"))
    p.add_argument("--offline", action="store_true", help=t("cli.help_offline"))
    return parser


def _lang_from_argv(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        value = a.split("=", 1)[1] if a.startswith("--lang=") else (
            argv[i + 1] if a == "--lang" and i + 1 < len(argv) else None)
        if value in i18n.UI_LANGUAGES:
            return value
    return None


def _setup_console() -> None:
    # Коли вивід іде в файл чи конвеєр, Windows може взяти кодування без потрібних літер
    # (напр. cp1251 без «é»). Краще знак питання замість літери, ніж падіння програми.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
