from __future__ import annotations

import json

import pytest

from backlog_digest import summarize

GOOD = {
    "about": "About it.", "key_points": ["one", "two"], "useful": "first\nsecond",
    "recommendations_count": "4", "evidence": "High", "evidence_notes": "studies",
    "questionable_claims": [], "water_percent": "35%", "water_parts": "intro [00:00]",
    "audience": "everyone", "verdict": "summary enough", "verdict_reason": "because",
    "score": 4.4, "tags": ["#Sleep Science", "health!", ""],
}


def test_parse_normalizes_values():
    s = summarize.parse_summary("```json\n" + json.dumps(GOOD) + "\n```")
    assert s["useful"] == ["first", "second"]
    assert s["recommendations_count"] == 4 and s["water_percent"] == 35 and s["score"] == 4
    assert s["evidence"] == "high" and s["verdict"] == "summary_enough"
    assert s["tags"] == ["sleep-science", "health"]


def test_extra_text_around_json_is_ignored():
    text = "Here you go:\n" + json.dumps(GOOD) + '\n{"another": 1}\nHope it helps!'
    assert summarize.parse_summary(text)["about"] == "About it."


def test_apostrophe_in_tags_becomes_letter_apostrophe():
    s = summarize.parse_summary(json.dumps({**GOOD, "tags": ["здоров'я", "м’ясо"]}))
    assert s["tags"] == ["здоровʼя", "мʼясо"]


def test_unknown_codes_get_safe_defaults():
    s = summarize.parse_summary(json.dumps({**GOOD, "verdict": "Дивитися", "evidence": "?", "score": 9}))
    assert (s["verdict"], s["evidence"], s["score"]) == ("partial", "medium", 5)


def test_pseudoscience_rule_is_enforced_in_code():
    s = summarize.parse_summary(json.dumps({**GOOD, "evidence": "unscientific", "score": 5, "verdict": "watch"}))
    assert (s["score"], s["verdict"]) == (2, "skip")


@pytest.mark.parametrize("text", ["no json here", "[1, 2]", json.dumps({**GOOD, "key_points": []}),
                                  json.dumps({**GOOD, "about": ""})])
def test_unusable_answers(text):
    with pytest.raises(ValueError):
        summarize.parse_summary(text)


def test_prompts():
    assert "Svenska (language code: sv)" in summarize.system_prompt("sv")
    assert 'code "xx"' in summarize.system_prompt("xx")
    prompt = summarize.user_prompt("T", "C", "05:00", "x" * 50, "en", True, max_chars=10)
    assert prompt.endswith("TRANSCRIPT:\n" + "x" * 10)
    assert "automatic" in prompt and "cut" in prompt


def test_parse_labels():
    assert summarize.parse_labels('{"a": "A", "b": "B", "c": "x"}', ["a", "b"]) == {"a": "A", "b": "B"}
    with pytest.raises(ValueError):
        summarize.parse_labels('{"a": "A"}', ["a", "b"])
