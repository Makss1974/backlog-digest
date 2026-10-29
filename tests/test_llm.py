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
