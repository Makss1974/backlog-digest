from __future__ import annotations

import io
import json
import os
import subprocess
import sys

from backlog_digest import config, db, mcp_server, pipeline
from test_pipeline import FakeChain
from test_questions import DIET, NAPS, SLEEP, library


def make_server(paths, tmp_path):
    cfg = config.defaults()
    cfg["output"]["path"] = str(tmp_path / "vault")
    conn = db.connect(paths.db_file)
    library(conn, tmp_path / "vault", [("k1", "Sleep and memory", SLEEP, "video"), ("k2", "Naps", NAPS, "article"),
                                       ("k3", "Keto", DIET, "article")])
    logs = []
    return mcp_server.Server(conn, cfg, paths, log=logs.append), logs


def call(server, name, **args):
    reply = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                           "params": {"name": name, "arguments": args}})
    result = reply["result"]
    text = result["content"][0]["text"]
    return (text if result["isError"] else json.loads(text)), result["isError"]


def test_handshake_and_tools_list(paths, tmp_path):
    server, _ = make_server(paths, tmp_path)
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}})
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["capabilities"] == {"tools": {}}
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                          "params": {"protocolVersion": "1999-01-01"}})["result"]["protocolVersion"] == "2025-06-18"
    names = [t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]]
    assert names == ["search_library", "get_summary", "list_sources", "ask_library"]
    assert server.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "id": 5, "method": "ping"})["result"] == {}


def test_search_get_and_sources(paths, tmp_path):
    server, _ = make_server(paths, tmp_path)
    found, err = call(server, "search_library", query="sleep")
    assert not err and found["library_size"] == 3
    assert [it["title"] for it in found["items"]] == ["Sleep and memory", "Naps"]
    best, _ = call(server, "search_library", min_score=4)
    assert [it["title"] for it in best["items"]] == ["Sleep and memory", "Naps"]   # без запиту — за оцінкою
    full, _ = call(server, "get_summary", id=found["items"][0]["id"])
    assert full["key_points"] == ["Deep sleep moves memories"] and full["note_path"].endswith("Sleep and memory.md")
    assert call(server, "get_summary", id=999) == ("no summary with id 999", True)
    assert call(server, "get_summary", id="x")[1] is True
    sources, _ = call(server, "list_sources")
    assert sources["sources"][0]["lists"] == [{"list": "A", "done": 3, "queued": 0, "total": 3}]
    assert call(server, "nope")[1] is True


def test_ask_library_tool(paths, tmp_path, monkeypatch):
    server, logs = make_server(paths, tmp_path)
    chain = FakeChain([json.dumps({"relevant": [1]}), json.dumps({"answer": "Sleep helps [1].", "grounding": "high"})])
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: chain))
    answer, err = call(server, "ask_library", question="why sleep?", save=True)
    assert not err and answer["answer"] == "Sleep helps [1]." and answer["sources"][0]["n"] == 1
    assert answer["note"].endswith("why sleep.md")
    monkeypatch.setattr(pipeline.Chain, "from_config",
                        classmethod(lambda cls, cfg, paths, log=print: FakeChain(exhausted=True)))
    text, err = call(server, "ask_library", question="why?")
    assert err and "no language model" in text


def test_serve_reads_lines_and_survives_garbage(paths, tmp_path):
    server, _ = make_server(paths, tmp_path)
    inp = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n'
                     b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                     + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                         "name": "search_library", "arguments": {"query": "сон"}}}).encode() + b"\n")
    out = io.BytesIO()
    mcp_server.serve(server, inp, out)
    replies = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700


def test_bdig_mcp_over_real_stdio(paths, tmp_path):
    """Справжній процес: stdout містить лише відповіді протоколу, навіть коли конфіг створюється вперше."""
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    proc = subprocess.run([sys.executable, "-m", "backlog_digest", "mcp"], capture_output=True, timeout=60,
                          input="".join(json.dumps(m) + "\n" for m in messages).encode(),
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    replies = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[0]["result"]["serverInfo"]["name"] == "backlog-digest"


def test_mcp_config_command(capsys):
    from backlog_digest import cli

    assert cli.main(["mcp", "--config"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mcpServers"]["backlog-digest"]["args"][-1] == "mcp"
