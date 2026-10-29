"""Моделі: будь-який OpenAI-сумісний провайдер, черга з запасними (fallback).

Працює на вбудованому в Python HTTP (urllib) — без пакета openai.
Уроки прототипу: денні ліміти запам'ятовуються до скидання (щоб не стукати марно),
перевантажена модель (503) вимикається після 2 збоїв, «зникла» модель (404) — одразу,
невірний ключ (401/403) вимикає весь провайдер, а не валить елементи.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__, keys
from .i18n import t
from .paths import Paths


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None              # None — ключ не потрібен (локальна модель)
    models: tuple[str, ...]
    json_mode: bool = False          # уміє response_format = json_object
    thinking_control: bool = False   # уміє вмикати/вимикати «роздуми» (reasoning_effort)
    signup_url: str = ""


# Готові профілі. Порядок моделей — від найстабільнішої: у кожної моделі Gemini окремий денний ліміт.
BUILTIN: dict[str, Provider] = {
    "gemini": Provider(
        "gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY",
        ("gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash",
         "gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"),
        json_mode=True, thinking_control=True, signup_url="https://aistudio.google.com/apikey"),
    "openrouter": Provider(
        "openrouter", "https://openrouter.ai/api/v1/", "OPENROUTER_API_KEY",
        ("nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free"),
        signup_url="https://openrouter.ai/keys"),
    "deepseek": Provider(
        "deepseek", "https://api.deepseek.com/", "DEEPSEEK_API_KEY", ("deepseek-chat",),
        json_mode=True, signup_url="https://platform.deepseek.com/api_keys"),
    "ollama": Provider("ollama", "http://localhost:11434/v1/", None, ("qwen3:4b",), json_mode=True),
    "lmstudio": Provider("lmstudio", "http://localhost:1234/v1/", None, ()),
}

_DAILY_HINTS = ("perday", "per day", "daily", "insufficient balance", "insufficient_quota", "credits", "billing")
TIMEOUT = 180


class LLMError(Exception):
    """Не вдалося отримати відповідь (не через квоту)."""


class QuotaExhausted(LLMError):
    """Усі моделі вичерпали ліміт або недоступні — треба зупинитися й продовжити пізніше."""


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


def providers_from_config(models_cfg: dict[str, Any]) -> list[Provider]:
    """Провайдери в порядку models.order; [models.<назва>] змінює готовий профіль або описує новий."""
    out = []
    for name in models_cfg["order"]:
        base = BUILTIN.get(name)
        custom: dict[str, Any] = models_cfg[name] if isinstance(models_cfg.get(name), dict) else {}
        if base is None:
            if not custom.get("base_url"):
                continue   # невідомий провайдер без опису — doctor про це скаже
            base = Provider(name, "", None, ())
        changes: dict[str, Any] = {}
        for k in ("base_url", "key_env", "json_mode", "thinking_control"):
            if k in custom:
                changes[k] = custom[k] or None if k == "key_env" else custom[k]
        if "models" in custom:
            m = custom["models"]
            changes["models"] = tuple(m if isinstance(m, list) else [m])
        p = replace(base, **changes)
        if not p.base_url.endswith("/"):
            p = replace(p, base_url=p.base_url + "/")
        out.append(p)
    return out


def quota_day() -> str:
    """Денні ліміти Gemini/OpenRouter оновлюються опівночі за тихоокеанським часом (≈ UTC−8)."""
    return (datetime.now(UTC) - timedelta(hours=8)).date().isoformat()


# ---------------------------------------------------------------- HTTP

def _request(method: str, url: str, key: str | None, payload: dict[str, Any] | None = None,
             timeout: float = TIMEOUT) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "User-Agent": f"backlog-digest/{__version__}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, _error_message(e.read().decode("utf-8", "replace"))) from e
    try:
        result: dict[str, Any] = json.loads(body)
    except ValueError as e:
        raise LLMError(short(body)) from e
    return result


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, list) and data:
            data = data[0]
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else err
        return short(str(msg or body))
    except (ValueError, AttributeError):
        return short(body)


def check_key(provider: Provider, key: str) -> tuple[bool, str]:
    """Перевіряє ключ запитом списку моделей (безкоштовно). (True, '') — ключ робочий."""
    try:
        _request("GET", provider.base_url + "models", key, timeout=20)
    except HttpError as e:
        return False, e.message
    except (urllib.error.URLError, OSError, LLMError) as e:
        return False, short(str(e))
    return True, ""


# ---------------------------------------------------------------- черга моделей

@dataclass
class _Slot:
    provider: Provider
    model: str
    key: str | None

    @property
    def name(self) -> str:
        return f"{self.provider.name}/{self.model}"


@dataclass
class Chain:
    """Черга моделей: пробує по черзі, вимикає вичерпані й зламані до кінця запуску."""

    slots: list[_Slot]
    state_path: Path | None = None
    temperature: float = 0.3
    rate_limit_wait: float = 60
    log: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    disabled: set[str] = field(default_factory=set)
    failures: dict[str, int] = field(default_factory=dict)
    day_exhausted: set[str] = field(default_factory=set)

    @classmethod
    def from_config(cls, cfg: dict[str, Any], paths: Paths, log: Callable[[str], None] = print) -> Chain:
        slots = []
        for p in providers_from_config(cfg["models"]):
            key = keys.get_secret(p.key_env, paths) if p.key_env else None
            if p.key_env and not key:
                continue
            slots += [_Slot(p, m, key) for m in p.models]
        chain = cls(slots, state_path=paths.data_dir / "quota.json",
                    temperature=float(cfg["models"]["temperature"]), log=log)
        chain._load_state()
        return chain

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        if st.get("day") == quota_day():
            self.day_exhausted = set(st.get("exhausted", []))
            self.disabled |= self.day_exhausted

    def _remember_exhausted(self, name: str) -> None:
        self.day_exhausted.add(name)
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"day": quota_day(), "exhausted": sorted(self.day_exhausted)}),
                                       encoding="utf-8")

    def active(self) -> list[str]:
        return [s.name for s in self.slots if s.name not in self.disabled]

    def complete(self, system: str, user: str, *, deep: bool = False, json_out: bool = True) -> LLMResult:
        errors = []
        for slot in self.slots:
            if slot.name in self.disabled:
                continue
            try:
                return self._call(slot, system, user, deep, json_out)
            except HttpError as e:
                errors.append(f"{slot.name}: {e.message}")
                self._handle_http(slot, e)
            except (urllib.error.URLError, OSError, LLMError) as e:
                errors.append(f"{slot.name}: {short(str(e))}")
                self._fail(slot, short(str(e)))
        if not self.active():
            raise QuotaExhausted("; ".join(errors[-3:]))
        raise LLMError("; ".join(errors) or "no model answered")

    def _handle_http(self, slot: _Slot, e: HttpError) -> None:
        if e.status in (429, 402):
            self.log(t("llm.quota", model=slot.name))
            self.disabled.add(slot.name)
            self._remember_exhausted(slot.name)
        elif e.status in (401, 403):
            # невірний ключ — усі моделі цього провайдера
            self.log(t("llm.bad_key", provider=slot.provider.name, detail=e.message))
            self.disabled |= {s.name for s in self.slots if s.provider.name == slot.provider.name}
        elif e.status == 404:
            self.log(t("llm.model_missing", model=slot.name))
            self.disabled.add(slot.name)
        else:
            self._fail(slot, e.message)

    def _fail(self, slot: _Slot, detail: str) -> None:
        self.failures[slot.name] = self.failures.get(slot.name, 0) + 1
        self.log(t("llm.error", model=slot.name, detail=detail))
        if self.failures[slot.name] >= 2:
            self.log(t("llm.unstable", model=slot.name))
            self.disabled.add(slot.name)

    def _call(self, slot: _Slot, system: str, user: str, deep: bool, json_out: bool,
              retried: bool = False) -> LLMResult:
        payload: dict[str, Any] = {
            "model": slot.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
        }
        if json_out and slot.provider.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if slot.provider.thinking_control:
            payload["reasoning_effort"] = "medium" if deep else "none"
        try:
            data = _request("POST", slot.provider.base_url + "chat/completions", slot.key, payload)
        except HttpError as e:
            # короткочасний ліміт (запитів на хвилину) — чекаємо й пробуємо ще раз; денний — далі по черзі
            if e.status == 429 and not retried and not any(h in e.message.lower() for h in _DAILY_HINTS):
                self.log(t("llm.waiting", model=slot.name, seconds=int(self.rate_limit_wait)))
                self.sleep(self.rate_limit_wait)
                return self._call(slot, system, user, deep, json_out, retried=True)
            raise
        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if not text.strip():
            raise LLMError("empty response")
        usage = data.get("usage") or {}
        return LLMResult(text, slot.name, int(usage.get("prompt_tokens") or 0),
                         int(usage.get("completion_tokens") or 0))


def short(s: str, n: int = 300) -> str:
    return " ".join(s.split())[:n]
