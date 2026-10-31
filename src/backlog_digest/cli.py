"""Точка входу: backlog-digest / bdig [--lang uk|en|fr|es]
[menu | doctor | add | sync | process | fetch | summarize | shortcut]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__, config, db, doctor, i18n
from .i18n import t
from .paths import get_paths

# команда → крок обробки: process — обидва кроки, fetch — лише тексти, summarize — лише конспекти
STAGES = {"process": db.ALL, "fetch": db.FETCH, "summarize": db.SUMMARIZE}


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
    if args.command == "shortcut":
        from .menu import make_shortcut

        return 0 if make_shortcut(paths) else 1

    if cfg_error or cfg is None:
        print(t("cli.config_error", error=cfg_error.message() if cfg_error else ""), file=sys.stderr)
        print(t("cli.run_doctor"), file=sys.stderr)
        return 2
    if config.ensure(paths):
        print(t("cli.config_created", path=str(paths.config_file)))
        cfg = config.load(paths)
        if args.lang:
            cfg["general"]["ui_language"] = args.lang

    try:
        conn = db.connect(paths.db_file)
    except db.DbError as e:
        print(t("cli.db_error", error=str(e)), file=sys.stderr)
        return 2
    try:
        return run_command(args, paths, cfg, conn)
    except KeyboardInterrupt:
        print(f"\n{t('menu.interrupted')}")
        return 130
    finally:
        conn.close()


def run_command(args: argparse.Namespace, paths: Any, cfg: dict[str, Any], conn: Any) -> int:
    from . import pipeline, web
    from .menu import Menu

    if args.command == "add" and args.urls[0].lower() == "raindrop":
        return _add_raindrop(conn, paths, args)
    if args.command == "add":
        # тека на диску — джерело «папка»; посилання не на YouTube або файл закладок — «статті»;
        # інакше — посилання YouTube
        local = Path(args.urls[0]).expanduser()
        is_folder = len(args.urls) == 1 and local.is_absolute() and local.is_dir()
        is_web = not is_folder and any((web.is_url(a) and not web.is_youtube(a)) or Path(a).expanduser().is_file()
                                       for a in args.urls)
        name = args.name or (local.name if is_folder else t("sources.web_default_name") if is_web else "YouTube")
        if any(s["name"] == name for s in db.list_sources(conn)):
            print(t("sources.name_taken", name=name), file=sys.stderr)
            return 2
        if is_folder:
            sid = pipeline.add_folder_source(conn, name, str(local), args.folder or name)
            print(t("sources.added_folder", name=name))
        elif is_web:
            urls = [a for a in args.urls if web.is_url(a)]
            files = [str(Path(a).expanduser().resolve()) for a in args.urls if not web.is_url(a)]
            sid = pipeline.add_web_source(conn, name, urls, files, args.folder or name, args.only)
            print(t("sources.added_web", name=name))
        else:
            sid = pipeline.add_youtube_source(conn, name, args.urls, args.folder or name)
            print(t("sources.added", name=name))
        source = db.get_source(conn, sid)
        if source and not args.no_sync:
            print(t("sources.synced", new=pipeline.sync_source(conn, source)[0]))
        return 0
    if args.command == "sync":
        print(t("sources.synced", new=pipeline.sync_all(conn)))
        return 0
    if args.command == "export":
        return _export(conn, paths, cfg, args)
    if args.command in STAGES:
        limit = args.limit or cfg["limits"]["per_run"]
        stats = pipeline.Processor(conn, cfg, paths).run(limit, args.source, STAGES[args.command])
        return 3 if stats.stopped else 0
    Menu(paths, cfg, conn).run()
    return 0


def _add_raindrop(conn: Any, paths: Any, args: argparse.Namespace) -> int:
    """bdig add raindrop [колекція ...] — усі закладки або лише названі колекції (з вкладеними)."""
    from . import keys, pipeline, raindrop

    token = keys.get_secret(raindrop.TOKEN, paths)
    if not token:
        print(t("raindrop.no_token"), file=sys.stderr)
        return 2
    wanted = args.urls[1:]
    ids: list[int] = []
    if wanted:
        try:
            cols = raindrop.collections(token)
        except raindrop.RaindropError as e:
            print(t("raindrop.failed", detail=str(e)), file=sys.stderr)
            return 2
        for title in wanted:
            found = [c for c in cols if title.lower() in (c.title.lower(), c.title.rsplit("/", 1)[-1].lower())]
            if not found:
                print(t("raindrop.no_collection", name=title), file=sys.stderr)
                return 2
            ids += [c.id for c in found]
    name = args.name or (wanted[0] if len(wanted) == 1 else "Raindrop")
    if any(s["name"] == name for s in db.list_sources(conn)):
        print(t("sources.name_taken", name=name), file=sys.stderr)
        return 2
    sid = pipeline.add_raindrop_source(conn, name, ids, args.folder or name)
    print(t("sources.added_raindrop", name=name))
    source = db.get_source(conn, sid)
    if source and not args.no_sync:
        print(t("sources.synced", new=pipeline.sync_source(conn, source)[0]))
    return 0


def _export(conn: Any, paths: Any, cfg: dict[str, Any], args: argparse.Namespace) -> int:
    """bdig export [--root НАЗВА] — усі списки з конспектами в Raindrop (лише нове й змінене)."""
    from . import export, keys, notes, raindrop

    token = keys.get_secret(raindrop.TOKEN, paths)
    if not token:
        print(t("raindrop.no_token"), file=sys.stderr)
        return 2
    language = cfg["general"]["summary_language"]
    lists = [(int(r["source_id"]), str(r["list_name"])) for r in db.done_lists(conn, language)]
    root = args.root or cfg["raindrop"]["export_root"] or export.DEFAULT_ROOT
    try:
        stats = export.export(conn, token, lists, root, language, notes.load_labels(paths, language))
    except raindrop.RaindropError as e:
        print(t("raindrop.failed", detail=str(e)), file=sys.stderr)
        return 1
    print(t("export.done", created=stats.created, updated=stats.updated, unchanged=stats.unchanged,
            collections=stats.collections))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backlog-digest", description=t("cli.description"))
    parser.add_argument("--version", action="version", version=f"backlog-digest {__version__}")
    parser.add_argument("--lang", choices=i18n.UI_LANGUAGES, help=t("cli.help_lang"))
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("menu", help=t("cli.help_menu"))
    p = sub.add_parser("doctor", help=t("cli.help_doctor"))
    p.add_argument("--offline", action="store_true", help=t("cli.help_offline"))
    p = sub.add_parser("add", help=t("cli.help_add"))
    p.add_argument("urls", nargs="+", metavar="URL", help=t("cli.help_urls"))
    p.add_argument("--name", help=t("cli.help_name"))
    p.add_argument("--folder", help=t("cli.help_folder"))
    p.add_argument("--no-sync", action="store_true", help=t("cli.help_no_sync"))
    p.add_argument("--only", action="append", default=[], metavar="FOLDER", help=t("cli.help_only"))
    sub.add_parser("sync", help=t("cli.help_sync"))
    p = sub.add_parser("export", help=t("cli.help_export"))
    p.add_argument("--root", metavar="NAME", help=t("cli.help_root"))
    sub.add_parser("shortcut", help=t("cli.help_shortcut"))
    for command in STAGES:
        p = sub.add_parser(command, help=t(f"cli.help_{command}"))
        p.add_argument("--limit", type=int, help=t("cli.help_limit"))
        p.add_argument("--source", type=int, metavar="ID", help=t("cli.help_source"))
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
