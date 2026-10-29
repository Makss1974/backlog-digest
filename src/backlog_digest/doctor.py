"""Команда doctor: перевіряє, чи все готово до роботи, і пояснює, що виправити."""
from __future__ import annotations

import platform
import shutil
import socket
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, config, db, i18n, keys, llm, tools
from .i18n import t
from .paths import Paths

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
MARKS = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}

# Куди перевіряємо зв'язок (лише TCP-з'єднання на порт 443, жодних даних не надсилаємо)
NETWORK_HOSTS = ("www.youtube.com", "generativelanguage.googleapis.com", "openrouter.ai")
DISK_FAIL_GB = 0.5
DISK_WARN_GB = 2.0


@dataclass
class Check:
    status: str
    title: str
    detail: str


def run_checks(paths: Paths, *, network: bool = True) -> list[Check]:
    checks = [check_python()]
    cfg, cfg_check = check_config(paths)
    checks.append(cfg_check)
    if cfg:
        checks.append(Check(INFO, t("doctor.languages"),
                            t("doctor.languages_detail", ui=i18n.language_name(cfg["general"]["ui_language"]),
                              summary=i18n.SUMMARY_LANGUAGES.get(cfg["general"]["summary_language"],
                                                                 cfg["general"]["summary_language"]))))
    if cfg:
        checks.append(check_output(cfg))
        checks += check_model_order(cfg)
    checks.append(check_data_dir(paths))
    checks.append(check_database(paths))
    checks += check_keys(paths)
    if network:
        checks.append(check_network())
    checks += check_tools()
    checks.append(check_disk(paths))
    return checks


def check_python() -> Check:
    v = sys.version_info
    detail = f"{platform.python_version()} ({sys.executable})"
    if v < (3, 11):
        return Check(FAIL, t("doctor.python"), t("doctor.python_old", version=detail))
    return Check(OK, t("doctor.python"), detail)


def check_config(paths: Paths) -> tuple[dict[str, Any] | None, Check]:
    title = t("doctor.config")
    try:
        cfg = config.load(paths)
    except config.ConfigError as e:
        return None, Check(FAIL, title, f"{paths.config_file}: {e.message()}")
    if not paths.config_file.exists():
        return cfg, Check(WARN, title, t("doctor.config_missing", path=str(paths.config_file)))
    return cfg, Check(OK, title, str(paths.config_file))


def check_output(cfg: dict[str, Any]) -> Check:
    title = t("doctor.output")
    raw = cfg["output"]["path"]
    if not raw:
        return Check(WARN, title, t("doctor.output_not_set"))
    path = Path(raw).expanduser()
    if path.is_dir():
        kind = t("doctor.output_obsidian") if (path / ".obsidian").is_dir() else t("doctor.output_markdown")
        return Check(OK, title, f"{path} ({kind})")
    parent = next((p for p in path.parents if p.exists()), None)
    if parent is None:
        return Check(FAIL, title, t("doctor.output_bad", path=str(path)))
    return Check(WARN, title, t("doctor.output_missing", path=str(path)))


def check_model_order(cfg: dict[str, Any]) -> list[Check]:
    known = {p.name for p in llm.providers_from_config(cfg["models"])}
    unknown = [n for n in cfg["models"]["order"] if n not in known]
    if unknown:
        return [Check(WARN, t("doctor.models"), t("doctor.models_unknown", names=", ".join(unknown)))]
    return []


def check_data_dir(paths: Paths) -> Check:
    title = t("doctor.data_dir")
    try:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=paths.data_dir, prefix=".write-test-"):
            pass
    except OSError as e:
        return Check(FAIL, title, t("doctor.not_writable", path=str(paths.data_dir), detail=str(e)))
    return Check(OK, title, str(paths.data_dir))


def check_database(paths: Paths) -> Check:
    title = t("doctor.database")
    try:
        conn = db.connect(paths.db_file)
        try:
            total = sum(db.status_counts(conn).values())
            version = db.schema_version(conn)
        finally:
            conn.close()
    except Exception as e:  # будь-яка помилка бази — це результат перевірки, а не падіння doctor
        return Check(FAIL, title, f"{paths.db_file}: {e}")
    return Check(OK, title, t("doctor.database_detail", version=version, items=i18n.tn("plural.items", total)))


def check_keys(paths: Paths) -> list[Check]:
    out = []
    found_model_key = False
    for name, service in {**keys.MODEL_KEYS, **keys.OTHER_KEYS}.items():
        src = keys.secret_source(name, paths)
        title = t("doctor.key", service=service)
        if src:
            found_model_key |= name in keys.MODEL_KEYS
            where = t("doctor.key_from_env") if src == keys.ENV else t("doctor.key_from_file")
            out.append(Check(OK, title, t("doctor.key_set", name=name, where=where)))
        else:
            out.append(Check(INFO, title, t("doctor.key_not_set", name=name)))
    if not found_model_key:
        out.append(Check(WARN, t("doctor.model_keys"),
                         t("doctor.no_model_key", file=str(paths.env_file))))
    return out


def _can_connect(host: str, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            return True
    except OSError:
        return False


def check_network() -> Check:
    with ThreadPoolExecutor(len(NETWORK_HOSTS)) as pool:
        results = dict(zip(NETWORK_HOSTS, pool.map(_can_connect, NETWORK_HOSTS), strict=True))
    bad = [h for h, ok in results.items() if not ok]
    title = t("doctor.network")
    if not bad:
        return Check(OK, title, t("doctor.network_ok", hosts=", ".join(NETWORK_HOSTS)))
    if len(bad) == len(results):
        return Check(FAIL, title, t("doctor.network_none"))
    return Check(WARN, title, t("doctor.network_some", hosts=", ".join(bad)))


def check_tools() -> list[Check]:
    out = []
    for tool in tools.TOOLS:
        path = tools.find(tool)
        title = t(f"doctor.tool_{tool.id}")
        if path:
            out.append(Check(OK, title, path))
        else:
            out.append(Check(INFO, title, t(f"doctor.tool_{tool.id}_missing")))
    return out


def check_disk(paths: Paths) -> Check:
    title = t("doctor.disk")
    target = paths.data_dir
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        free_gb = shutil.disk_usage(target).free / 1024**3
    except OSError as e:
        return Check(WARN, title, str(e))
    detail = t("doctor.disk_free", gb=f"{free_gb:.1f}", path=str(target))
    if free_gb < DISK_FAIL_GB:
        return Check(FAIL, title, detail)
    if free_gb < DISK_WARN_GB:
        return Check(WARN, title, detail)
    return Check(OK, title, detail)


def report(checks: list[Check]) -> str:
    width = max(len(c.title) for c in checks)
    lines = [t("doctor.header", version=__version__), ""]
    lines += [f"  {MARKS[c.status]} {c.title.ljust(width)}  {c.detail}" for c in checks]
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    lines.append("")
    if fails:
        lines.append(i18n.tn("plural.doctor_fails", fails))
    elif warns:
        lines.append(i18n.tn("plural.doctor_warns", warns))
    else:
        lines.append(t("doctor.all_ok"))
    lines.append(t("doctor.legend"))
    return "\n".join(lines)


def run(paths: Paths, *, network: bool = True) -> int:
    checks = run_checks(paths, network=network)
    print(report(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0
