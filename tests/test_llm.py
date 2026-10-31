from __future__ import annotations

import json

import pytest

from backlog_digest import config, llm


def answer(text="{}", tin=10, tout=5):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": tin, "completion_tokens": tout}}


class FakeServer:
    """Підставляється замість llm._request: відповідає за сценарієм для кожної моделі."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, method, url, key, payload=None, timeout=0):
        self.calls.append((url, key, payload))
        reply = self.script[payload["model"]].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_chain(tmp_path, *models_by_provider, **kw):
    slots = []
    for provider_name, models in models_by_provider:
        p = llm.Provider(provider_name, f"https://{provider_name}.test/v1/", "KEY", tuple(models), json_mode=True,
                         thinking_control=provider_name == "gemini")
        slots += [llm._Slot(p, m, "secret") for m in models]
    logs = []
    chain = llm.Chain(slots, state_path=tmp_path / "quota.json", log=logs.append, sleep=lambda s: None, **kw)
    return chain, logs


def test_success_and_payload(tmp_path, monkeypatch):
    server = FakeServer({"m1": [answer('{"a": 1}')]})
    monkeypatch.setattr(llm, "_request", server)
    chain, _ = make_chain(tmp_path, ("gemini", ["m1"]))
    res = chain.complete("sys", "user", deep=False)
    assert (res.text, res.model, res.tokens_in, res.tokens_out) == ('{"a": 1}', "gemini/m1", 10, 5)
    url, key, payload = server.calls[0]
    assert url == "https://gemini.test/v1/chat/completions" and key == "secret"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "none"
    server.script["m1"].append(answer())
    chain.complete("sys", "user", deep=True)
    assert server.calls[1][2]["reasoning_effort"] == "medium"


def test_daily_quota_falls_back_and_is_remembered(tmp_path, monkeypatch):
    server = FakeServer({"m1": [llm.HttpError(429, "Quota exceeded: requests per day")], "m2": [answer("ok")]})
    monkeypatch.setattr(llm, "_request", server)
    chain, logs = make_chain(tmp_path, ("gemini", ["m1", "m2"]))
    assert chain.complete("s", "u").model == "gemini/m2"
    state = json.loads((tmp_path / "quota.json").read_text())
    assert state == {"day": llm.quota_day(), "exhausted": ["gemini/m1"]}
    # новий запуск того ж дня — m1 одразу пропускається
    chain2, _ = make_chain(tmp_path, ("gemini", ["m1", "m2"]))
    chain2._load_state()
    assert chain2.active() == ["gemini/m2"]


def test_short_rate_limit_waits_and_retries(tmp_path, monkeypatch):
    server = FakeServer({"m1": [llm.HttpError(429, "Too many requests per minute"), answer("ok")]})
    monkeypatch.setattr(llm, "_request", server)
    waits = []
    chain, logs = make_chain(tmp_path, ("gemini", ["m1"]), rate_limit_wait=5)
    chain.sleep = waits.append
    assert chain.complete("s", "u").text == "ok"
    assert waits == [5]


def test_bad_key_disables_whole_provider(tmp_path, monkeypatch):
    server = FakeServer({"m1": [llm.HttpError(401, "API key not valid")], "x": [answer("ok")]})
    monkeypatch.setattr(llm, "_request", server)
    chain, _ = make_chain(tmp_path, ("gemini", ["m1", "m2"]), ("deepseek", ["x"]))
    assert chain.complete("s", "u").model == "deepseek/x"
    assert chain.active() == ["deepseek/x"]


def test_missing_model_and_unstable_model(tmp_path, monkeypatch):
    server = FakeServer({"gone": [llm.HttpError(404, "not found")],
                         "busy": [llm.HttpError(503, "overloaded"), llm.HttpError(503, "overloaded")]})
    monkeypatch.setattr(llm, "_request", server)
    chain, _ = make_chain(tmp_path, ("gemini", ["gone", "busy"]))
    with pytest.raises(llm.LLMError) as e1:
        chain.complete("s", "u")
    assert not isinstance(e1.value, llm.QuotaExhausted)          # busy ще може відповісти
    with pytest.raises(llm.QuotaExhausted):                       # другий збій — вимкнено все
        chain.complete("s", "u")
    assert chain.active() == []


def test_empty_answer_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_request", FakeServer({"m1": [answer("  "), answer("ok")]}))
    chain, _ = make_chain(tmp_path, ("deepseek", ["m1"]))
    with pytest.raises(llm.LLMError):
        chain.complete("s", "u")
    assert chain.complete("s", "u").text == "ok"


def test_providers_from_config():
    cfg = config.defaults()["models"]
    cfg["order"] = ["gemini", "mine", "nobody", "deepseek"]
    cfg["gemini"] = {"models": "only-one"}
    cfg["mine"] = {"base_url": "http://localhost:9000/v1", "key_env": "", "models": ["local"]}
    providers = llm.providers_from_config(cfg)
    assert [p.name for p in providers] == ["gemini", "mine", "deepseek"]
    assert providers[0].models == ("only-one",) and providers[0].json_mode
    assert providers[1].base_url == "http://localhost:9000/v1/" and providers[1].key_env is None


def test_from_config_skips_providers_without_keys(paths, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    chain = llm.Chain.from_config(config.defaults(), paths)
    assert chain.active() == ["deepseek/deepseek-chat"]


def test_check_key(monkeypatch):
    def fake(method, url, key, payload=None, timeout=0):
        if key != "good":
            raise llm.HttpError(400, "API key not valid")
        assert method == "GET" and url.endswith("/models")
        return {"data": []}

    monkeypatch.setattr(llm, "_request", fake)
    assert llm.check_key(llm.BUILTIN["gemini"], "good") == (True, "")
    assert llm.check_key(llm.BUILTIN["gemini"], "bad") == (False, "API key not valid")


def test_error_message_parsing():
    assert llm._error_message('[{"error": {"message": "Quota exceeded"}}]') == "Quota exceeded"
    assert llm._error_message('{"error": "plain"}') == "plain"
    assert llm._error_message("<html>oops</html>") == "<html>oops</html>"


# ---------------------------------------------------------------- Gemini дивиться відео

def gemini_chain(tmp_path, models=("gemini-3-flash-preview", "gemini-2.5-flash")):
    p = llm.BUILTIN["gemini"]
    return llm.Chain([llm._Slot(p, m, "secret") for m in models], state_path=tmp_path / "q.json",
                     log=lambda s: None, sleep=lambda s: None)


def video_answer(text='{"a": 1}', thought="thinking..."):
    return {"candidates": [{"content": {"parts": [{"text": thought, "thought": True}, {"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 20, "thoughtsTokenCount": 5}}


def test_watch_payload_and_answer(tmp_path, monkeypatch):
    calls = []

    def fake(method, url, key, payload=None, timeout=0, key_header=""):
        calls.append((url, key, key_header, payload))
        return video_answer()

    monkeypatch.setattr(llm, "_request", fake)
    chain = gemini_chain(tmp_path)
    assert chain.has_watcher() and chain.can_watch()
    res = chain.watch("sys", "user", "https://www.youtube.com/watch?v=abc", start=60, end=120)
    assert (res.text, res.model, res.tokens_in, res.tokens_out) == ('{"a": 1}', "gemini/gemini-3-flash-preview",
                                                                    1000, 25)
    url, key, header, payload = calls[0]
    assert url.endswith("/v1beta/models/gemini-3-flash-preview:generateContent")
    assert (key, header) == ("secret", "x-goog-api-key")
    video, text = payload["contents"][0]["parts"]
    assert video == {"fileData": {"fileUri": "https://www.youtube.com/watch?v=abc"},
                     "videoMetadata": {"startOffset": "60s", "endOffset": "120s"}}
    assert text == {"text": "user"} and payload["systemInstruction"]["parts"][0]["text"] == "sys"
    gen = payload["generationConfig"]
    assert gen["responseMimeType"] == "application/json" and gen["thinkingConfig"] == {"thinkingLevel": "low"}
    chain.disabled.add("gemini/gemini-3-flash-preview")
    chain.watch("s", "u", "https://www.youtube.com/watch?v=abc", json_out=False)
    gen = calls[1][3]["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingBudget": 0} and "responseMimeType" not in gen
    assert "videoMetadata" not in calls[1][3]["contents"][0]["parts"][0]


@pytest.mark.parametrize("status,message", [(400, "Request contains an invalid argument."),
                                            (403, "The caller does not have permission")])   # приватне відео
def test_watch_rejected_video_does_not_disable_models(tmp_path, monkeypatch, status, message):
    def fake(method, url, key, payload=None, timeout=0, key_header=""):
        raise llm.HttpError(status, message)

    monkeypatch.setattr(llm, "_request", fake)
    chain = gemini_chain(tmp_path)
    for _ in range(3):
        with pytest.raises(llm.VideoRejected):
            chain.watch("s", "u", "https://www.youtube.com/watch?v=gone")
    assert chain.can_watch() and not chain.disabled


def test_watch_quota_and_no_gemini(tmp_path, monkeypatch):
    def fake(method, url, key, payload=None, timeout=0, key_header=""):
        raise llm.HttpError(429, "Quota exceeded for requests per day")

    monkeypatch.setattr(llm, "_request", fake)
    chain = gemini_chain(tmp_path)
    with pytest.raises(llm.QuotaExhausted):
        chain.watch("s", "u", "https://www.youtube.com/watch?v=abc")
    assert not chain.can_watch() and chain.has_watcher()
    other, _ = make_chain(tmp_path, ("deepseek", ["m1"]))
    assert not other.has_watcher()


def test_minute_limit_is_not_remembered_for_the_day(tmp_path, monkeypatch):
    minute = llm.HttpError(429, "Quota exceeded [GenerateContentInputTokensPerModelPerMinute-FreeTier]")
    server = FakeServer({"m1": [minute, minute], "m2": [answer("ok")]})
    monkeypatch.setattr(llm, "_request", server)
    chain, logs = make_chain(tmp_path, ("gemini", ["m1", "m2"]))
    assert chain.complete("s", "u").model == "gemini/m2"
    assert "gemini/m1" in chain.disabled and not (tmp_path / "quota.json").exists()
    assert any("per-minute" in line for line in logs)


def test_error_message_keeps_gemini_quota_ids():
    body = json.dumps({"error": {"message": "You exceeded your current quota.", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"},
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41s"}]}})
    assert llm._error_message(body) == (
        "You exceeded your current quota. [GenerateRequestsPerDayPerProjectPerModel-FreeTier]")
