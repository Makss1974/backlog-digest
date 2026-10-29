from __future__ import annotations

import json

import pytest

from backlog_digest import i18n, notes

SUMMARY = {
    "about": "About.", "key_points": ["k1", "k2"], "useful": [], "recommendations_count": 3,
    "evidence": "low", "evidence_notes": "", "questionable_claims": ["claim"], "water_percent": 20,
    "water_parts": "", "audience": "all", "verdict": "skip", "verdict_reason": "weak", "score": 2,
    "tags": ["a", "b"],
}
ITEM = {"title": 'Sleep: "why" [part 1]', "url": "https://www.youtube.com/watch?v=abc", "author": "Chan",
        "list_name": "Health"}


@pytest.mark.parametrize(("raw", "clean"), [
    ('a<b>:c"d/e\\f|g?h*i', "a b c d e f g h i"),
    ("[Wiki] #tag ^x", "(Wiki) tag x"),
    ("  trailing dots... ", "trailing dots"),
    ("CON", "_CON"),
    ("", "_"),
])
def test_sanitize(raw, clean):
    assert notes.sanitize(raw) == clean


def test_note_dir_and_unique_path(tmp_path):
    d = notes.note_dir(tmp_path, "Video/Science", "A: B")
    assert d == tmp_path / "Video" / "Science" / "A B"
    first = notes.note_path(d, "Title", "abc", lambda p: False)
    assert first.name == "Title.md"
    assert notes.note_path(d, "Title", "abc", lambda p: True).name == "Title (abc).md"


def test_long_titles_respect_windows_path_limit(tmp_path):
    deep = tmp_path / ("x" * 60)
    path = notes.note_path(deep, "y" * 300, "abcdefghijk", lambda p: True)
    assert len(str(path)) <= notes.MAX_PATH


def test_frontmatter_is_yaml_compatible():
    fm = notes.frontmatter({"title": 'He said: "hi" #1', "score": 4, "empty": "", "tags": ["a", "b"], "flag": True})
    lines = fm.splitlines()
    assert lines[0] == lines[-1] == "---"
    assert lines[1] == 'title: "He said: \\"hi\\" #1"'
    assert "score: 4" in lines and 'tags: ["a", "b"]' in lines and "flag: true" in lines
    assert not any(line.startswith("empty") for line in lines)


@pytest.mark.parametrize("lang", i18n.UI_LANGUAGES)
def test_render_uses_labels_of_summary_language(lang):
    labels = notes.catalog_labels(lang)
    text = notes.render_video_note(ITEM, SUMMARY, labels, language=lang, processed="2026-10-29T10:00:00",
                                   duration="05:00")
    assert text.startswith("---\ntitle: ")
    assert f"# {ITEM['title']}" in text
    assert f"**{labels['verdict']}: {labels['verdict_skip']}**" in text
    assert f"## {labels['key_points']}\n- k1\n- k2" in text
    assert f"## {labels['useful']}\n- —" in text
    assert "- ⚠️ claim" in text
    assert "★★☆☆☆" in text


def test_labels_for_other_language_are_translated_once_and_cached(paths):
    calls = []

    def translate(english):
        calls.append(1)
        return {k: f"SV {v}" for k, v in english.items()}

    labels = notes.load_labels(paths, "sv", translate)
    assert labels["verdict_skip"] == "SV Skip"
    assert notes.load_labels(paths, "sv", translate) == labels and len(calls) == 1
    assert notes.labels_file(paths, "sv").exists()


def test_labels_fall_back_to_english(paths):
    def broken(english):
        raise ValueError("model failed")

    assert notes.load_labels(paths, "pl", broken) == notes.catalog_labels("en")
    assert notes.load_labels(paths, "pl", None) == notes.catalog_labels("en")
    assert not notes.labels_file(paths, "pl").exists()


def test_labels_file_can_be_edited_by_user(paths):
    path = notes.labels_file(paths, "de")
    path.parent.mkdir(parents=True)
    path.write_text('verdict = "Urteil"\n', encoding="utf-8")
    labels = notes.load_labels(paths, "de", lambda e: pytest.fail("must not translate"))
    assert labels["verdict"] == "Urteil" and labels["score"] == "Score"


def test_write_note_is_atomic(tmp_path):
    path = tmp_path / "a" / "n.md"
    notes.write_note(path, "one")
    notes.write_note(path, "two")
    assert path.read_text(encoding="utf-8") == "two"
    assert [p.name for p in path.parent.iterdir()] == ["n.md"]


def test_summary_json_roundtrip():
    assert json.loads(json.dumps(SUMMARY)) == SUMMARY
