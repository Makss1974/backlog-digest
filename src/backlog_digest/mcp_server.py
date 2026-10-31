"""MCP-сервер (етап 7): бібліотека конспектів доступна Claude, Cursor та іншим помічникам.

Запуск — `bdig mcp`: протокол Model Context Protocol поверх stdin/stdout (JSON-RPC 2.0, одне повідомлення
на рядок). Без сторонніх пакетів: потрібно лише initialize, tools/list, tools/call і ping.
У stdout — тільки протокол; повідомлення програми й моделей ідуть у stderr.

Інструменти: пошук конспектів (без моделі й без лімітів), повний конспект за номером, джерела й списки,
і «питання до бібліотеки» — воно використовує моделі користувача, тож витрачає його ліміти.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from . import __version__, questions

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")   # перша — найновіша, яку знаємо
MAX_LIMIT = 50

SUMMARY_FIELDS = ("about", "key_points", "useful", "evidence", "evidence_notes", "questionable_claims",
                  "water_percent", "audience", "verdict", "verdict_reason", "score", "tags")


def _tools(language: str) -> list[dict[str, Any]]:
    lang = f"Summaries are written in language '{language}' — search with words in that language (and English)."
    return [
        {"name": "search_library",
         "description": "Search the user's personal library of summaries (YouTube videos, articles, books, "
                        "Telegram posts, audio). Matches words in titles, descriptions, tags and key points. "
                        f"{lang} Without a query returns the best-rated summaries. Free: no model is used.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "keywords, e.g. 'sleep memory'"},
             "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 15},
             "min_score": {"type": "integer", "minimum": 1, "maximum": 5, "description": "only scores >= this"},
             "source_id": {"type": "integer", "description": "only this source (see list_sources)"}}}},
        {"name": "get_summary",
         "description": "The full summary of one item by its id (from search_library): key points, what is useful, "
                        "evidence level, questionable claims, verdict, score, link and the note file.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
        {"name": "list_sources",
         "description": "Sources of the library (YouTube, folders, articles, Raindrop, Telegram) with their lists "
                        "and how many summaries are done and queued.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "ask_library",
         "description": "Answer a question from ALL summaries of the library: an answer with numbered sources, "
                        "repeats, disagreements, how well it is grounded and what the library lacks. Uses the "
                        "user's language models and their daily limits (several requests) — prefer search_library "
                        "+ get_summary when you can answer yourself. save=true also writes the answer as a note.",
         "inputSchema": {"type": "object", "properties": {
             "question": {"type": "string"}, "source_id": {"type": "integer"},
             "save": {"type": "boolean", "default": False}}, "required": ["question"]}},
    ]


class ToolError(Exception):
    """Помилка виклику інструмента — повертається помічнику як isError, а не як збій протоколу."""


class Server:
    def __init__(self, conn: sqlite3.Connection, cfg: dict[str, Any], paths: Any,
                 log: Callable[[str], None] | None = None) -> None:
        self.conn = conn
        self.cfg = cfg
        self.paths = paths
        self.language = cfg["general"]["summary_language"]
        self.log = log or (lambda s: print(s, file=sys.stderr, flush=True))

    # -- протокол

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Одне повідомлення JSON-RPC → відповідь (None для сповіщень)."""
        method = message.get("method")
        mid = message.get("id")
        if mid is None:   # сповіщення (notifications/initialized, cancelled...) — без відповіді
            return None
        try:
            if method == "initialize":
                asked = str((message.get("params") or {}).get("protocolVersion") or "")
                result: dict[str, Any] = {
                    "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "backlog-digest", "version": __version__},
                    "instructions": "The user's personal library of summaries of videos, articles, books and posts. "
                                    "Use search_library and get_summary to find what they have saved and read; "
                                    "cite titles and links.",
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tools(self.language)}
            elif method == "tools/call":
                params = message.get("params") or {}
                result = self.call(str(params.get("name") or ""), params.get("arguments") or {})
            else:
                return _error(mid, -32601, f"method not found: {method}")
        except Exception as e:  # збій одного запиту не повинен зупиняти сервер
            return _error(mid, -32603, str(e)[:500])
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tools: dict[str, Callable[[dict[str, Any]], Any]] = {
            "search_library": self.search, "get_summary": self.get_summary,
            "list_sources": self.list_sources, "ask_library": self.ask}
        if name not in tools:
            return _content(f"unknown tool: {name}", error=True)
        try:
            return _content(json.dumps(tools[name](args), ensure_ascii=False, indent=1))
        except ToolError as e:
            return _content(str(e), error=True)

    # -- інструменти

    def _brief(self, it: dict[str, Any]) -> dict[str, Any]:
        s = it["s"]
        return {"id": it["id"], "title": it.get("title"), "kind": it.get("kind"), "author": it.get("author"),
                "list": it.get("list_name"), "about": s.get("about"), "score": s.get("score"),
                "verdict": s.get("verdict"), "evidence": s.get("evidence"), "tags": s.get("tags"),
                "url": it.get("url")}

    def search(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(args.get("limit") or 15), MAX_LIMIT))
        source_id = int(args["source_id"]) if args.get("source_id") else None
        items = questions.library_items(self.conn, self.language, source_id)
        if args.get("min_score"):
            items = [it for it in items if int(it["s"].get("score") or 0) >= int(args["min_score"])]
        query = str(args.get("query") or "").strip()
        if query:
            found = questions.rank(items, [query], limit)
        else:
            found = sorted(items, key=lambda it: (-int(it["s"].get("score") or 0), -it["id"]))[:limit]
        return {"library_size": len(items), "found": len(found), "items": [self._brief(it) for it in found]}

    def get_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            item_id = int(args.get("id"))   # type: ignore[arg-type]
        except (TypeError, ValueError) as e:
            raise ToolError("id must be a number from search_library") from e
        row = self.conn.execute(
            "SELECT i.*, r.summary_json FROM items i JOIN results r ON r.key=i.key AND r.language=? "
            "WHERE i.id=?", (self.language, item_id)).fetchone()
        if not row:
            raise ToolError(f"no summary with id {item_id}")
        s = json.loads(row["summary_json"])
        out = {k: row[k] for k in ("id", "title", "kind", "author", "url", "list_name", "duration", "note_path")}
        out["path"] = row["path"] if row["kind"] != "post" else None
        out.update({k: s[k] for k in SUMMARY_FIELDS if k in s})
        return out

    def list_sources(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT s.id, s.type, s.name, i.list_name, SUM(i.status='done') done, "
            "SUM(i.status IN ('new','error')) queued, COUNT(i.id) total FROM sources s "
            "LEFT JOIN items i ON i.source_id=s.id GROUP BY s.id, i.list_name ORDER BY s.id, i.list_name").fetchall()
        sources: dict[int, dict[str, Any]] = {}
        for r in rows:
            src = sources.setdefault(r["id"], {"id": r["id"], "type": r["type"], "name": r["name"], "lists": []})
            if r["total"]:
                src["lists"].append({"list": r["list_name"], "done": r["done"] or 0, "queued": r["queued"] or 0,
                                     "total": r["total"]})
        return {"summary_language": self.language, "sources": list(sources.values())}

    def ask(self, args: dict[str, Any]) -> dict[str, Any]:
        from . import pipeline
        from .llm import LLMError

        question = " ".join(str(args.get("question") or "").split())
        if not question:
            raise ToolError("question is empty")
        proc = pipeline.Processor(self.conn, self.cfg, self.paths, log=self.log)
        if not proc.chain.active():
            raise ToolError("no language model is available now (no keys, or today's limits are used up)")
        try:
            result = questions.ask(self.conn, proc.chain, question, self.language,
                                   source_id=int(args["source_id"]) if args.get("source_id") else None, log=self.log)
        except (LLMError, ValueError) as e:
            raise ToolError(f"could not answer: {str(e)[:300]}") from e
        out: dict[str, Any] = {
            "answer": result.answer, "grounding": result.grounding, "grounding_note": result.grounding_note,
            "points": result.points, "disputed": result.disputed, "repeats": result.repeats, "gaps": result.gaps,
            "sources": [{"n": i, **self._brief(it)} for i, it in enumerate(result.sources, 1)],
            "library_size": result.searched}
        if args.get("save") and result.searched and self.cfg["output"]["path"]:
            path = questions.write_answer(result, proc.labels(), kind=self.cfg["output"]["kind"], root=proc.root)
            out["note"] = str(path)
        return out


def _content(text: str, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(server: Server, stdin: IO[bytes] | None = None, stdout: IO[bytes] | None = None) -> None:
    """Читає повідомлення по рядку, доки клієнт не закриє stdin."""
    inp = stdin or sys.stdin.buffer
    out = stdout or sys.stdout.buffer
    for raw in inp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply: dict[str, Any] | None = _error(None, -32700, "parse error")
        else:
            reply = server.handle(message) if isinstance(message, dict) else _error(None, -32600, "invalid request")
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            out.flush()


def client_config(home: Path | None = None) -> dict[str, Any]:
    """Що вписати в налаштування Claude Desktop / Cursor (mcpServers)."""
    args = ["mcp"] if home is None else ["--home", str(home), "mcp"]
    return {"mcpServers": {"backlog-digest": {"command": shutil.which("bdig") or "bdig", "args": args}}}
