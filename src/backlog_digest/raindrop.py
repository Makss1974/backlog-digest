"""Raindrop.io: імпорт закладок (лише читання) і запити для експорту конспектів (див. export.py).

Доступ — «тестовий токен» власної програми: app.raindrop.io/settings/integrations → «Create new app» →
«Create test token». Токен — секрет: RAINDROP_TOKEN у змінних середовища або в .env, ніколи не друкуємо.

Колекції (зокрема вкладені) стають списками: «Наука/Фізика». «Несортовані» — без списку.
Зображення пропускаємо: тексту в них немає. Колекцію експорту (exclude) не імпортуємо —
інакше програма знову ставила б у чергу власні конспекти.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import __version__
from .i18n import t
from .web import Link

TOKEN = "RAINDROP_TOKEN"
API = "https://api.raindrop.io/rest/v1"
PER_PAGE = 50           # більше API не віддає за раз
UNSORTED = -1
TIMEOUT = 30
SKIP_TYPES = ("image",)


class RaindropError(Exception):
    """Raindrop недоступний або відповів помилкою."""


class AuthError(RaindropError):
    """Токен неправильний або відкликаний."""


@dataclass
class Collection:
    id: int
    title: str       # повний шлях: «Наука/Фізика»
    count: int
    parent: int | None = None


class NotFound(RaindropError):
    """Колекції чи закладки вже немає (видалили в Raindrop)."""


def _get(token: str, path: str, params: dict[str, Any] | None = None, *, retries: int = 2) -> dict[str, Any]:
    return call(token, "GET", path, params, retries=retries)


def call(token: str, method: str, path: str, params: dict[str, Any] | None = None,
         body: dict[str, Any] | None = None, *, retries: int = 2) -> dict[str, Any]:
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": f"backlog-digest/{__version__}"}
    data_out = None
    if body is not None:
        data_out = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data_out, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(t("raindrop.bad_token")) from e
        if e.code == 404:
            raise NotFound(t("raindrop.http_error", code=e.code)) from e
        if e.code == 429 and retries:   # 120 запитів на хвилину — чекаємо й пробуємо ще
            time.sleep(min(int(e.headers.get("Retry-After") or 60), 120))
            return call(token, method, path, params, body, retries=retries - 1)
        raise RaindropError(t("raindrop.http_error", code=e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise RaindropError(t("raindrop.network_error", detail=str(getattr(e, "reason", e))[:200])) from e
    if data.get("result") is False:
        raise RaindropError(t("raindrop.api_error", detail=str(data.get("errorMessage") or data)[:200]))
    return data


def check_token(token: str) -> str:
    """Перевіряє токен; повертає ім'я користувача Raindrop (для підтвердження, що токен той)."""
    user = _get(token, "/user").get("user") or {}
    return str(user.get("fullName") or user.get("email") or user.get("_id") or "")


def collections(token: str) -> list[Collection]:
    """Усі колекції з повними шляхами, у порядку дерева (батьківська перед вкладеними)."""
    raw = _get(token, "/collections").get("items", []) + _get(token, "/collections/childrens").get("items", [])
    by_id = {int(c["_id"]): c for c in raw}

    def parent_of(c: dict[str, Any]) -> int | None:
        p = (c.get("parent") or {}).get("$id")
        return int(p) if p is not None and int(p) in by_id else None

    def path(c: dict[str, Any], depth: int = 0) -> str:
        p = parent_of(c)
        title = str(c.get("title") or c["_id"]).replace("/", "∕")   # «/» у назві не має ламати шлях
        return f"{path(by_id[p], depth + 1)}/{title}" if p is not None and depth < 20 else title

    out = [Collection(int(c["_id"]), path(c), int(c.get("count") or 0), parent_of(c)) for c in raw]
    return sorted(out, key=lambda c: c.title.lower())


def descendants(cols: list[Collection], ids: list[int]) -> set[int]:
    """Вибрані колекції разом із вкладеними."""
    chosen = set(ids)
    changed = True
    while changed:
        changed = False
        for c in cols:
            if c.parent in chosen and c.id not in chosen:
                chosen.add(c.id)
                changed = True
    return chosen


def links(token: str, collection_ids: list[int] | None = None,
          progress: Callable[[int], None] | None = None, exclude: list[int] | None = None) -> list[Link]:
    """Закладки (крім кошика). collection_ids — лише ці колекції з вкладеними; None або [] — усі.
    exclude — ці колекції з вкладеними пропускаємо (експорт конспектів).
    progress(n) — після кожної сторінки: скільки закладок переглянуто."""
    cols = collections(token)
    names = {c.id: c.title for c in cols}
    wanted = descendants(cols, collection_ids) if collection_ids else None
    skip = descendants(cols, exclude) if exclude else set()
    out: list[Link] = []
    page = 0
    while True:
        items = _get(token, "/raindrops/0", {"page": page, "perpage": PER_PAGE, "sort": "created"}).get("items", [])
        for r in items:
            cid = int((r.get("collection") or {}).get("$id", UNSORTED))
            if ((wanted is not None and cid not in wanted) or cid in skip or r.get("type") in SKIP_TYPES
                    or not r.get("link")):
                continue
            out.append(Link(str(r["link"]), str(r.get("title") or "").strip(),
                            "" if cid == UNSORTED else names.get(cid, "")))
        if progress:
            progress(page * PER_PAGE + len(items))
        if len(items) < PER_PAGE:
            return out
        page += 1
