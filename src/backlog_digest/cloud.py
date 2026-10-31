"""Хмарний режим (за бажанням): програма щодня працює на GitHub Actions, навіть коли комп'ютер вимкнений.

Хмарна тека — звичайна тека даних програми (config.toml, backlog.sqlite) плюс конспекти в «notes/»,
файл-позначка й завдання GitHub Actions. Користувач кладе її у свій **приватний** репозиторій GitHub,
а ключі — у його Secrets. Щодня (і за кнопкою «Run workflow», з телефону теж) GitHub запускає
`bdig --home . cloud run`: оновити списки, зробити конспекти, записати зміни назад у репозиторій.

На комп'ютері та сама тека — клон репозиторію: перед роботою програма забирає зміни з GitHub
(`git pull`), після — надсилає свої (`git push`). Теки й експорти з диска в хмарі не оновлюються
й не обробляються — їх робить комп'ютер, коли увімкнений. Усі шляхи всередині теки — відносні.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, config, db, pipeline, tgchannel, web
from .i18n import t
from .paths import Paths

MARKER = "backlog-digest-cloud.json"
NOTES = "notes"
WORKFLOW = Path(".github") / "workflows" / "backlog-digest.yml"
SECRETS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "RAINDROP_TOKEN")
GIT_TIMEOUT = 120
# підпис для комітів і злиттів, якщо в git не задано свого (на новому комп'ютері й у хмарі)
IDENTITY = ("-c", "user.name=backlog-digest", "-c", "user.email=backlog-digest@users.noreply.github.com")
# у хмарі IP щоразу інший і YouTube частіше просить «підтвердити, що ви не робот»:
# менші паузи (хвилини GitHub не безкінечні), а відео без субтитрів «дивиться» Gemini
CLOUD_SETTINGS = {"youtube": {"delay_min": 15, "delay_max": 30, "no_subtitles": "gemini"},
                  "limits": {"per_run": 20}}
ADDED_NAMES = {"youtube": "YouTube (cloud)", "web": "Web (cloud)", "telegram": "Telegram (cloud)"}

Log = Callable[[str], None]
Git = Callable[..., subprocess.CompletedProcess[str]]


class CloudError(Exception):
    pass


def is_cloud(paths: Paths) -> bool:
    return (paths.config_dir / MARKER).exists()


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


# ---------------------------------------------------------------- git

def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if not shutil.which("git"):
        raise CloudError(t("cloud.no_git"))
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise CloudError(str(e)) from e


def _out(r: subprocess.CompletedProcess[str]) -> str:
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"exit {r.returncode}"


def has_remote(home: Path, run: Git = git) -> bool:
    return bool(run(home, "remote").stdout.strip())


def pull(home: Path, log: Log = print, run: Git = git) -> bool:
    """Забрати зміни з GitHub. Базу змінили і тут, і в хмарі — беремо хмарну, а свою кладемо поруч копією."""
    if not has_remote(home, run):
        return False
    r = run(home, "pull", "--ff-only", "--quiet")
    if r.returncode == 0:
        return True
    commit(home, "local changes", run)
    r = run(home, *IDENTITY, "pull", "--no-rebase", "--no-edit", "--quiet", "-X", "theirs")
    if r.returncode != 0:
        log(t("cloud.pull_failed", detail=_out(r)))
        return False
    log(t("cloud.pull_merged"))
    return True


def commit(home: Path, message: str, run: Git = git) -> bool:
    run(home, "add", "-A")
    if not run(home, "status", "--porcelain").stdout.strip():
        return False
    r = run(home, *IDENTITY, "commit", "--quiet", "-m", message)
    return r.returncode == 0


def push(home: Path, message: str, log: Log = print, run: Git = git) -> bool:
    """Записати зміни й надіслати на GitHub. Хмара встигла щось змінити — спершу забираємо її зміни."""
    commit(home, message, run)
    if not has_remote(home, run):
        return False
    r = run(home, "push", "--quiet")
    if r.returncode != 0 and pull(home, log, run):
        r = run(home, "push", "--quiet")
    if r.returncode != 0:
        log(t("cloud.push_failed", detail=_out(r)))
        return False
    return True


# ---------------------------------------------------------------- створення хмарної теки

def utc_cron(local_time: str, now: datetime | None = None) -> str:
    """«09:00» за місцевим часом -> cron GitHub (той працює за UTC)."""
    now = now or datetime.now().astimezone()
    h, m = (int(x) for x in local_time.split(":"))
    local = now.replace(hour=h, minute=m, second=0, microsecond=0)
    utc = local.astimezone(UTC)
    return f"{utc.minute} {utc.hour} * * *"


def workflow(cron: str) -> str:
    env = "\n".join(f"          {k}: ${{{{ secrets.{k} }}}}" for k in SECRETS)
    return f"""# backlog-digest: щодня й за кнопкою «Run workflow» — оновити списки й зробити конспекти.
# Daily and on «Run workflow»: update the lists and make summaries. Created by bdig {__version__}.
name: backlog-digest
on:
  schedule:
    - cron: "{cron}"
  workflow_dispatch:
    inputs:
      limit:
        description: "How many items / Скільки елементів"
        default: ""
      add:
        description: "Links to add, space-separated / Посилання для додавання через пробіл"
        default: ""
concurrency:
  group: backlog-digest
  cancel-in-progress: false
permissions:
  contents: write
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 300
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install --upgrade backlog-digest
      - name: backlog-digest
        env:
{env}
          LIMIT: ${{{{ inputs.limit }}}}
          ADD: ${{{{ inputs.add }}}}
        run: bdig --home . cloud run --limit "$LIMIT" --add "$ADD"
      - name: save
        if: always()
        run: |
          git config user.name "backlog-digest"
          git config user.email "backlog-digest@users.noreply.github.com"
          git add -A
          git diff --cached --quiet || git commit -m "backlog-digest: $(date -u +%F)"
          git pull --no-rebase --no-edit -X ours
          git push
"""


GITIGNORE = """.env
*.tmp
cache/
logs/
whisper/
running.json
quota.json
"""


def init(dest: Path, paths: Paths, cfg: dict[str, Any], conn: sqlite3.Connection, *, copy_data: bool = True,
         at_time: str = "07:00", log: Log = print) -> Path:
    """Нова хмарна тека з поточних даних (або порожня). Конспекти джерел копіюються в notes/."""
    dest = dest.expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise CloudError(t("cloud.not_empty", path=str(dest)))
    dest.mkdir(parents=True, exist_ok=True)
    new_cfg = json.loads(json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}))
    for section, values in CLOUD_SETTINGS.items():
        new_cfg[section].update(values)
    new_cfg["output"]["path"] = NOTES
    config.save(Paths(dest, dest), new_cfg)
    if copy_data:
        target = sqlite3.connect(dest / "backlog.sqlite")
        conn.backup(target)
        target.close()
        copied, failed = _move_notes(dest, Path(cfg["output"]["path"]).expanduser() if cfg["output"]["path"] else None)
        log(t("cloud.copied", notes=copied))
        if failed:
            log(t("cloud.copy_failed", n=len(failed), example=failed[0]))
    db.connect(dest / "backlog.sqlite").close()
    labels = paths.config_dir / "note_labels"
    if labels.is_dir():
        shutil.copytree(labels, dest / "note_labels")
    (dest / NOTES).mkdir(exist_ok=True)
    (dest / WORKFLOW).parent.mkdir(parents=True, exist_ok=True)
    (dest / WORKFLOW).write_text(workflow(utc_cron(at_time)), encoding="utf-8")
    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dest / "README.md").write_text(t("cloud.readme", secrets=", ".join(SECRETS[:3]), time=at_time),
                                    encoding="utf-8")
    (dest / MARKER).write_text(json.dumps({"version": 1, "created": db.now(), "by": __version__}, indent=2) + "\n",
                               encoding="utf-8")
    if shutil.which("git"):
        git(dest, "init", "--quiet", "-b", "main")
        commit(dest, "backlog-digest: cloud folder")
    return dest


def _move_notes(dest: Path, old_root: Path | None) -> tuple[int, list[str]]:
    """Нотатки зі старого сховища -> dest/notes/<той самий відносний шлях>; у базі — відносні шляхи.
    Не скопійовані (задовгий шлях Windows тощо) лишаються у старому сховищі."""
    conn = sqlite3.connect(dest / "backlog.sqlite")
    copied, failed = 0, []
    try:
        rows = conn.execute("SELECT id, note_path FROM items WHERE note_path IS NOT NULL").fetchall()
        for item_id, note in rows:
            path = Path(note)
            try:
                rel = path.relative_to(old_root) if old_root and path.is_absolute() else None
            except ValueError:
                rel = None
            if rel is None:
                conn.execute("UPDATE items SET note_path=NULL WHERE id=?", (item_id,))   # перепишеться при потребі
                continue
            target = dest / NOTES / rel
            if path.is_file():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    copied += 1
                except OSError as e:
                    failed.append(f"{rel}: {e.strerror or e}")
                    conn.execute("UPDATE items SET note_path=NULL WHERE id=?", (item_id,))
                    continue
            conn.execute("UPDATE items SET note_path=? WHERE id=?", ((Path(NOTES) / rel).as_posix(), item_id))
        conn.commit()
    finally:
        conn.close()
    return copied, failed


# ---------------------------------------------------------------- запуск у хмарі

def add_links(conn: sqlite3.Connection, links: list[str], log: Log = print) -> int:
    """Посилання з кнопки «Run workflow»: YouTube, t.me-канали, статті — у джерела «(cloud)»."""
    groups: dict[str, list[str]] = {"youtube": [], "web": [], "telegram": []}
    for link in links:
        name = tgchannel.channel_name(link) if tgchannel.is_channel_link(link) else None
        if name:
            groups["telegram"].append(name)
        elif web.is_youtube(link):
            groups["youtube"].append(link)
        elif web.is_url(link):
            groups["web"].append(link)
        else:
            log(t("cloud.bad_link", link=link))
    added = 0
    for kind, values in groups.items():
        if not values:
            continue
        name = ADDED_NAMES[kind]
        field = {"youtube": "urls", "web": "urls", "telegram": "channels"}[kind]
        source = next((s for s in db.list_sources(conn) if s["name"] == name), None)
        if source is None and kind == "youtube":
            pipeline.add_youtube_source(conn, name, values, name)
        elif source is None and kind == "web":
            pipeline.add_web_source(conn, name, values, [], name)
        elif source is None:
            pipeline.add_channel_source(conn, name, values, name)
        else:
            settings = source["settings"]
            settings[field] = list(dict.fromkeys([*settings.get(field, []), *values]))
            db.update_source(conn, source["id"], settings=settings)
        added += len(values)
        log(t("cloud.added", n=len(values), name=name))
    return added


def run(conn: sqlite3.Connection, cfg: dict[str, Any], paths: Paths, *, limit: int | None = None,
        add: list[str] | None = None, log: Log = print) -> str:
    """`bdig cloud run`: є завдання розкладу — виконати всі ввімкнені; немає — оновити все й зробити конспекти."""
    from . import schedule

    cfg["_cloud"] = True
    if add:
        add_links(conn, add, log)
    jobs = [j for j in schedule.list_jobs(conn) if j["enabled"]]
    if jobs and not limit:
        results = schedule.run_due(conn, cfg, paths, force=True, log=log)
        summary = "; ".join(f"{name}: {result}" for name, result in results)
    else:
        lim = limit or int(cfg["limits"]["per_run"])
        job = {"id": 0, "name": "cloud", "source_id": None, "list_name": None, "lim": lim,
               "steps": {**schedule.DEFAULT_STEPS}, "enabled": 1}
        started = datetime.now()
        summary = schedule.Runner(conn, cfg, paths, log).run_job(job)
        schedule.journal(cfg, paths, "cloud", started, summary)
    log(f"\n{summary}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:   # видно на сторінці запуску в GitHub (і в застосунку на телефоні)
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"### backlog-digest\n\n{summary}\n")
    return summary
