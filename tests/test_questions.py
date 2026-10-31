from __future__ import annotations

import json

from backlog_digest import config, db, i18n, notes, questions
from test_pipeline import FakeChain


def summary(about, tags, score=3, evidence="medium", points=()):
    return {"about": about, "key_points": list(points) or [about], "useful": [], "evidence": evidence,
            "evidence_notes": "", "questionable_claims": [], "verdict": "summary_enough", "score": score,
            "tags": list(tags)}


def library(conn, root, items):
    """items: (key, title, summary, kind) — готові конспекти з нотатками; той самий key у двох списках — двічі."""
    sid = db.add_source(conn, "web", "Lib")
    for n, (key, title, s, kind) in enumerate(items):
        list_name = "B" if key.endswith("#2") else "A"
        key = key.removesuffix("#2")
        db.upsert_item(conn, sid, key, list_name, title=title, kind=kind, url=f"https://e.com/{n}")
        item_id = conn.execute("SELECT id FROM items WHERE key=? AND list_name=?", (key, list_name)).fetchone()[0]
        note = root / "Lib" / list_name / f"{title}.md"
        notes.write_note(note, "# " + title)
        db.save_result(conn, key, "en", s, source_language="en", model="m", tokens_in=1, tokens_out=1)
        db.mark_done(conn, item_id, str(note))
    return sid


SLEEP = summary("How sleep consolidates memory", ["sleep", "memory"], 5, "high", ["Deep sleep moves memories"])
NAPS = summary("Short naps and alertness", ["sleep", "naps"], 4, "medium")
DIET = summary("Keto diet basics", ["diet"], 2, "low")


def test_stems_and_rank():
    assert questions.stems("Sleeping, sleeps; сну снам") >= {"sleepi", "slee", "сну", "снам"}
    items = [{"title": "Keto", "s": DIET}, {"title": "Naps", "s": NAPS}, {"title": "Sleep and memory", "s": SLEEP}]
    ranked = questions.rank(items, ["sleep", "memory"])
    assert [it["title"] for it in ranked] == ["Sleep and memory", "Naps"]   # кето без збігів — не кандидат


def test_ask_small_library_answers_with_links(paths, tmp_path):
    conn = db.connect(paths.db_file)
    root = tmp_path / "vault"
    library(conn, root, [("k1", "Sleep and memory", SLEEP, "video"), ("k2", "Naps", NAPS, "article"),
                         ("k3", "Keto", DIET, "article"), ("k1#2", "Sleep and memory", SLEEP, "video")])
    chain = FakeChain([
        json.dumps({"relevant": [1, 2]}),        # маленька бібліотека — без слів для пошуку, одразу вибір
        json.dumps({"answer": "Sleep helps memory [1]; naps help too [2, 1].",
                    "points": [{"text": "Deep sleep matters", "sources": [1]}],
                    "repeats": [{"ids": [1, 2], "why": "both on sleep", "best": 1}],
                    "disputed": [{"text": "Nap length", "sources": [2, 9]}],
                    "grounding": "HIGH", "grounding_note": "research", "gaps": "Nothing on insomnia."}),
    ])
    steps = []
    result = questions.ask(conn, chain, "Why is sleep important?", "en", on_step=lambda k, n: steps.append((k, n)))
    assert steps == [("library", 3), ("candidates", 3), ("relevant", 2)]   # повтор k1 в іншому списку — раз
    assert [it["title"] for it in result.sources] == ["Sleep and memory", "Naps"]
    assert result.grounding == "high" and result.disputed[0]["sources"] == [2]   # [9] — немає такого джерела
    assert "[1] Sleep and memory" in chain.calls[1] and "Deep sleep moves memories" in chain.calls[1]

    labels = notes.catalog_labels("en")
    path = questions.write_answer(result, labels, kind="obsidian", root=root)
    assert path == root / "_Questions" / "Why is sleep important.md"
    text = path.read_text(encoding="utf-8")
    assert "Sleep helps memory [[[Lib/A/Sleep and memory|1]]]" in text
    assert "naps help too [[[Lib/A/Naps|2]], [[Lib/A/Sleep and memory|1]]]" in text
    assert "> **Grounding:** high — research" in text and "Nothing on insomnia." in text
    assert "✅ [[Lib/A/Sleep and memory|Sleep and memory]]" in text
    md = questions.render(result, labels, kind="markdown", root=root, path=path)
    assert "[1](<../Lib/A/Sleep and memory.md>)" in md
    conn.close()


def test_ask_big_library_searches_by_keywords(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(questions, "SMALL", 1)
    monkeypatch.setattr(questions, "BATCH", 1)
    conn = db.connect(paths.db_file)
    library(conn, tmp_path / "vault", [("k1", "Sleep and memory", SLEEP, "video"), ("k2", "Naps", NAPS, "article"),
                                       ("k3", "Keto", DIET, "article")])
    chain = FakeChain([json.dumps({"keywords": ["sleep", "nap"]}),
                       json.dumps({"relevant": [1]}), json.dumps({"relevant": []}),   # два кандидати, по одному
                       json.dumps({"answer": "A [1]", "grounding": "medium"})])
    result = questions.ask(conn, chain, "sleep?", "en")
    assert result.candidates == 2 and [it["title"] for it in result.sources] == ["Sleep and memory"]
    assert "Keto" not in "".join(chain.calls)
    conn.close()


def test_nothing_relevant(paths, tmp_path):
    conn = db.connect(paths.db_file)
    library(conn, tmp_path / "vault", [("k3", "Keto", DIET, "article")])
    result = questions.ask(conn, FakeChain([json.dumps({"relevant": []})]), "Quantum gravity?", "en")
    assert not result.sources
    text = questions.render(result, notes.catalog_labels("en"), kind="markdown", root=tmp_path, path=tmp_path / "x.md")
    assert notes.catalog_labels("en")["nothing_found"] in text
    conn.close()


def test_cli_ask(paths, tmp_path, monkeypatch, capsys):
    from backlog_digest import cli, pipeline

    cfg = config.defaults()
    cfg["output"]["path"] = str(tmp_path / "vault")
    config.save(paths, cfg)
    conn = db.connect(paths.db_file)
    library(conn, tmp_path / "vault", [("k1", "Sleep and memory", SLEEP, "video")])
    conn.close()
    chain = FakeChain([json.dumps({"relevant": [1]}), json.dumps({"answer": "Sleep helps [1].", "grounding": "low"})])
    monkeypatch.setattr(pipeline.Chain, "from_config", classmethod(lambda cls, cfg, paths, log=print: chain))
    assert cli.main(["--lang", "en", "ask", "why", "sleep?"]) == 0
    out = capsys.readouterr().out
    assert "Sleep helps [1]." in out and "[1] Sleep and memory" in out and "Grounding: low" in out
    assert (tmp_path / "vault" / "_Questions" / "why sleep.md").exists()
    assert i18n.t("questions.saved", path="").strip() in out


def test_menu_ask(paths, tmp_path, monkeypatch):
    from backlog_digest import menu

    called = []
    monkeypatch.setattr(questions, "run", lambda conn, cfg, p, q, source_id=None: called.append(q) or 0)
    answers = iter(["Why sleep?", ""])
    monkeypatch.setattr(menu, "ask", lambda prompt="": next(answers))
    m = menu.Menu(paths, config.defaults(), db.connect(paths.db_file))
    assert any(k == "9" for k, _, _ in m.items())
    m.ask_library()
    assert called == ["Why sleep?"]
