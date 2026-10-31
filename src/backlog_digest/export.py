"""Експорт конспектів у Raindrop.io (етап 4): вкладені колекції за списками й темами, нотатка, теги.

Пишемо лише у власну кореневу колекцію (за замовчуванням «Backlog digest»): закладки, з яких
імпортували, не чіпаємо — це копії з конспектами. Що й куди вже надіслано, пам'ятає база
(таблиці export_collections, exported), тож повторний експорт оновлює змінене, а не дублює.
Закладку чи колекцію видалили в Raindrop — створюємо заново.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import db, notes, raindrop
from .i18n import t

DEFAULT_ROOT = "Backlog digest"
BATCH = 100              # стільки закладок Raindrop приймає одним запитом
MAX_TITLE = 1000
MAX_NOTE = 10000         # більше Raindrop не зберігає
MAX_EXCERPT = 10000

Log = Callable[[str], None]


@dataclass
class Entry:
    item_id: int
    path: list[str]                  # колекції від кореня: [корінь, список..., тема...]
    body: dict[str, Any]             # закладка без колекції
    hash: str
    old: sqlite3.Row | None = None   # що надсилали минулого разу


@dataclass
class Plan:
    new: list[Entry] = field(default_factory=list)
    changed: list[Entry] = field(default_factory=list)
    same: list[Entry] = field(default_factory=list)
    no_link: int = 0                 # книги й файли з диска: посилання немає — у Raindrop не додати


@dataclass
class Stats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    collections: int = 0


def note_text(item: dict[str, Any], s: dict[str, Any], labels: dict[str, str]) -> str:
    """Нотатка закладки (Raindrop показує Markdown): вердикт, оцінка, головне, сумнівне."""
    L = labels
    stars = "★" * s["score"] + "☆" * (5 - s["score"])
    verdict = notes.verdict_label(L, item.get("kind") or "video", s["verdict"])
    lines = [f"**{L['verdict']}: {verdict}** · {L['score']}: {s['score']}/5 {stars}", ""]
    if s.get("key_points"):
        lines += [f"**{L['key_points']}**", *[f"- {x}" for x in s["key_points"]], ""]
    if s.get("useful"):
        lines += [f"**{L['useful']}**", *[f"- {x}" for x in s["useful"]], ""]
    if s.get("questionable_claims"):
        lines += [f"**{L['questionable']}**", *[f"- ⚠️ {x}" for x in s["questionable_claims"]], ""]
    if s.get("verdict_reason"):
        lines += [f"**{L['verdict']}:** {s['verdict_reason']}"]
    text = "\n".join(lines).strip()
    return text if len(text) <= MAX_NOTE else text[:MAX_NOTE - 1].rstrip() + "…"


def bookmark(item: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    s = item["s"]
    return {
        "link": item["url"],
        "title": (item.get("title") or item["url"])[:MAX_TITLE],
        "excerpt": (s.get("about") or "")[:MAX_EXCERPT],
        "note": note_text(item, s, labels),
        "tags": list(s.get("tags") or []),
    }


def collection_path(root: str, list_title: str, topic: str | None) -> list[str]:
    parts = [root, *list_title.split("/"), *(topic or "").split("/")]
    return [p.strip().replace("/", "∕") for p in parts if p.strip()]


def plan(conn: sqlite3.Connection, lists: list[tuple[int, str]], root: str, language: str,
         labels: dict[str, str]) -> Plan:
    """Що надіслати (без мережі): нові, змінені (текст чи тема) і без змін."""
    sent = db.exported(conn)
    out = Plan()
    for source_id, list_name in lists:
        source = db.get_source(conn, source_id) or {}
        title = list_name or source.get("name") or ""
        for item in db.done_items(conn, source_id, list_name, language):
            if not item.get("url"):
                out.no_link += 1
                continue
            body = bookmark(item, labels)
            path = collection_path(root, title, item.get("topic"))
            digest = hashlib.sha1(json.dumps([path, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            entry = Entry(int(item["id"]), path, body, digest, sent.get(int(item["id"])))
            if entry.old is None:
                out.new.append(entry)
            elif entry.old["hash"] != digest:
                out.changed.append(entry)
            else:
                out.same.append(entry)
    return out


class Exporter:
    def __init__(self, conn: sqlite3.Connection, token: str, log: Log = print) -> None:
        self.conn = conn
        self.token = token
        self.log = log
        self.ids = db.export_collections(conn)
        self.stats = Stats()

    def check_collections(self) -> None:
        """Колекції, яких у Raindrop уже немає, забуваємо — їх буде створено знову."""
        alive = {c.id for c in raindrop.collections(self.token)}
        gone = [p for p, cid in self.ids.items() if cid not in alive]
        if gone:
            db.forget_export_collections(self.conn, gone)
            for p in gone:
                del self.ids[p]

    def alive_bookmarks(self) -> set[int]:
        """Закладки, які зараз лежать у колекціях експорту (видалені й у кошику — не тут)."""
        roots = [cid for p, cid in self.ids.items() if "/" not in p]
        out: set[int] = set()
        for root in roots:
            page = 0
            while True:
                items = raindrop.call(self.token, "GET", f"/raindrops/{root}",
                                      {"page": page, "perpage": raindrop.PER_PAGE, "nested": "true"}).get("items", [])
                out.update(int(r["_id"]) for r in items)
                if len(items) < raindrop.PER_PAGE:
                    break
                page += 1
        return out

    def collection(self, path: list[str]) -> int:
        key = "/".join(path)
        if key in self.ids:
            return self.ids[key]
        parent = self.collection(path[:-1]) if len(path) > 1 else None
        body: dict[str, Any] = {"title": path[-1]}
        if parent is not None:
            body["parent"] = {"$id": parent}
        cid = int(raindrop.call(self.token, "POST", "/collection", body=body)["item"]["_id"])
        db.save_export_collection(self.conn, key, cid, parent is None)
        self.ids[key] = cid
        self.stats.collections += 1
        return cid

    def run(self, p: Plan) -> Stats:
        self.check_collections()
        alive = self.alive_bookmarks() if self.ids else set()
        create: list[Entry] = list(p.new)
        update: list[Entry] = []
        changed = {e.item_id for e in p.changed}
        for e in p.changed + p.same:
            assert e.old is not None
            if int(e.old["raindrop_id"]) not in alive:
                create.append(e)                    # видалили в Raindrop — додаємо знову
            elif e.item_id in changed or self.ids.get("/".join(e.path)) != int(e.old["collection_id"]):
                update.append(e)
            else:
                self.stats.unchanged += 1
        total = len(create) + len(update)
        done = 0
        for start in range(0, len(create), BATCH):
            chunk = create[start:start + BATCH]
            payload = [{**e.body, "collection": {"$id": self.collection(e.path)}} for e in chunk]
            res = raindrop.call(self.token, "POST", "/raindrops", body={"items": payload}).get("items", [])
            if len(res) != len(chunk):
                raise raindrop.RaindropError(t("export.bad_answer"))
            db.save_exported(self.conn, [(e.item_id, int(r["_id"]), body["collection"]["$id"], e.hash)
                                         for e, r, body in zip(chunk, res, payload, strict=True)])
            self.stats.created += len(chunk)
            done += len(chunk)
            self.log(t("export.progress", done=done, total=total))
        for e in update:
            assert e.old is not None
            cid = self.collection(e.path)
            rid = int(e.old["raindrop_id"])
            try:
                raindrop.call(self.token, "PUT", f"/raindrop/{rid}", body={**e.body, "collection": {"$id": cid}})
            except raindrop.NotFound:
                res = raindrop.call(self.token, "POST", "/raindrop", body={**e.body, "collection": {"$id": cid}})
                rid = int(res["item"]["_id"])
            db.save_exported(self.conn, [(e.item_id, rid, cid, e.hash)])
            self.stats.updated += 1
            done += 1
            if done % BATCH == 0 or done == total:
                self.log(t("export.progress", done=done, total=total))
        return self.stats


def export(conn: sqlite3.Connection, token: str, lists: list[tuple[int, str]], root: str, language: str,
           labels: dict[str, str], log: Log = print) -> Stats:
    return Exporter(conn, token, log).run(plan(conn, lists, root, language, labels))
