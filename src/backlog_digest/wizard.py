"""Майстер першого запуску (етап 5): мова → куди складати → ключ моделі → перше джерело → скільки на день →
розклад → перший конспект. Кожен крок можна пропустити (Enter) і зробити потім у меню.

Кроки — ті самі дії, що й у меню (menu.Menu), тут лише порядок і пояснення для новачка.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import autostart, db, i18n, keys, llm, pipeline, raindrop, schedule
from .i18n import t

if TYPE_CHECKING:
    from .menu import Menu

STEPS = 6
FIRST_RUN_LIMIT = 1     # перший конспект — одразу, щоб побачити результат; решта — за розкладом


def is_first_run(menu: Menu) -> bool:
    """Ще нічого не налаштовано: немає ні теки для конспектів, ні джерел."""
    return not menu.cfg["output"]["path"] and not db.list_sources(menu.conn)


def default_output() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "backlog-digest"


def step(n: int, title: str) -> None:
    print(f"\n{'─' * 60}\n  {t('wizard.step', n=n, total=STEPS)} · {title}\n{'─' * 60}")


def run(menu: Menu) -> None:
    from .menu import ask, ask_int, ask_yes

    print(t("wizard.welcome"))

    step(1, t("wizard.title_language"))
    menu.choose_ui_language()
    ui = menu.cfg["general"]["ui_language"]
    if ask_yes(t("wizard.same_language", language=i18n.language_name(ui))):
        menu.cfg["general"]["summary_language"] = ui
        menu.save()
    else:
        menu.choose_summary_language()

    step(2, t("wizard.title_output"))
    print(t("wizard.output_help"))
    if not menu.cfg["output"]["path"]:
        menu.cfg["output"]["path"] = str(default_output())    # підказка: Enter — ця тека
    menu.choose_output(explain=False)
    out = Path(menu.cfg["output"]["path"]).expanduser()
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(t("wizard.output_failed", detail=str(e)))

    step(3, t("wizard.title_key"))
    if llm.model_status(menu.cfg, menu.paths)[0]:
        print(t("wizard.key_exists"))
        if ask_yes(t("wizard.add_another_key")):
            menu.model_keys()
    else:
        print(t("wizard.key_help", url=llm.BUILTIN["gemini"].signup_url))
        menu.model_keys(default=1)
        if not llm.model_status(menu.cfg, menu.paths)[0]:
            print(t("wizard.no_key_later"))

    step(4, t("wizard.title_source"))
    print(t("wizard.source_help"))
    print(f"  1. {t('sources.add_youtube')}\n  2. {t('sources.add_folder')}\n  3. {t('sources.add_web')}\n"
          f"  4. {t('sources.add_raindrop')}\n  5. {t('sources.add_telegram')}\n  0. {t('wizard.skip')}")
    choice = ask_int(t("settings.choose"), 1, 0, 5)
    action = {1: menu.add_youtube, 2: menu.add_folder, 3: menu.add_web, 4: menu.add_raindrop,
              5: menu.add_telegram}.get(choice)
    if action:
        action()

    step(5, t("wizard.title_per_day"))
    print(t("wizard.per_day_help"))
    limits = menu.cfg["limits"]
    n = ask_int(t("wizard.per_day"), int(limits["per_run"]), 1, 1000)
    limits["per_run"] = limits["per_day"] = n
    menu.save()

    step(6, t("wizard.title_schedule"))
    print(t("wizard.schedule_help"))
    if db.list_sources(menu.conn) and ask_yes(t("wizard.ask_schedule")):
        while True:
            at = schedule.parse_time(ask(f"{t('schedule.time')} [09:00]: ") or "09:00")
            if at:
                break
            print(t("schedule.bad_time"))
        steps = {"raindrop": bool(keys.get_secret(raindrop.TOKEN, menu.paths)) and ask_yes(t("schedule.ask_raindrop"))}
        schedule.add_job(menu.conn, t("wizard.job_name"), lim=n, steps=steps, at_time=at)
        try:
            print(t("schedule.autostart_on", where=autostart.install(menu.paths.data_dir / "logs" / "autostart.log")))
        except autostart.AutostartError as e:
            print(t("schedule.autostart_failed", detail=str(e)))
        menu._autostart = None

    print("\n" + t("wizard.done"))
    if llm.model_status(menu.cfg, menu.paths)[0] and db.status_counts(menu.conn)[db.NEW] \
            and ask_yes(t("wizard.ask_first")):
        stats = pipeline.Processor(menu.conn, menu.cfg, menu.paths).run(FIRST_RUN_LIMIT)
        if stats.done:
            note = menu.conn.execute("SELECT note_path FROM items WHERE status='done' AND note_path IS NOT NULL "
                                     "ORDER BY processed_at DESC LIMIT 1").fetchone()
            if note and ask_yes(t("wizard.open_first")):
                from .menu import open_file

                open_file(Path(note[0]))
