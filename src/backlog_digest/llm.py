"""Моделі: будь-який OpenAI-сумісний провайдер, черга з запасними (fallback).

Працює на вбудованому в Python HTTP (urllib) — без пакета openai.
Уроки прототипу: денні ліміти запам'ятовуються до скидання (щоб не стукати марно),
перевантажена модель (503) вимикається після 2 збоїв, «зникла» модель (404) — одразу,
невірний ключ (401/403) вимикає весь провайдер, а не валить елементи.

Відео без субтитрів «дивиться» Gemini за посиланням на YouTube (власний API Gemini, не OpenAI-сумісний):
нічого не завантажуємо, YouTube відкриває Google, а не наша IP.
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

GEMINI_NATIVE = "https://generativelanguage.googleapis.com/v1beta/"
_SHORT_HINTS = ("perminute", "per minute")   # хвилинний ліміт: модель вимикаємо лише до кінця запуску
_DAILY_HINTS = ("perday", "per day", "daily", "insufficient balance", "insufficient_quota", "credits", "billing")
TIMEOUT = 180


class LLMError(Exception):
    """Не вдалося отримати відповідь (не через квоту)."""


class QuotaExhausted(LLMError):
    """Усі моделі вичерпали ліміт або недоступні — треба зупинитися й продовжити пізніше."""


class VideoRejected(LLMError):
    """Gemini не може відкрити це відео (приватне, видалене, з обмеженнями) — помилка елемента, не моделі."""


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
             timeout: float = TIMEOUT, key_header: str = "") -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "User-Agent": f"backlog-digest/{__version__}"}
    if key and key_header:
        headers[key_header] = key
    elif key:
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
        # Gemini пише, ЯКИЙ ліміт вичерпано (за день чи за хвилину), лише в details: quotaId
        ids = [v.get("quotaId", "") for d in (err.get("details") or [] if isinstance(err, dict) else [])
               if isinstance(d, dict) for v in d.get("violations") or [] if isinstance(v, dict)]
        ids = [i for i in dict.fromkeys(ids) if i]
        return short(str(msg or body) + (f" [{', '.join(ids)}]" if ids else ""))
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
        return self._each(self.slots, lambda slot: self._call(slot, system, user, deep, json_out))

    # -- відео за посиланням (лише Gemini)

    def _watchers(self) -> list[_Slot]:
        return [s for s in self.slots if s.provider.name == "gemini" and s.provider.base_url.startswith(
            "https://generativelanguage.googleapis.com/")]

    def has_watcher(self) -> bool:
        """Є ключ Gemini — відео без субтитрів можна «дивитися»."""
        return bool(self._watchers())

    def can_watch(self) -> bool:
        """Є Gemini, що не вичерпав ліміт сьогодні."""
        return any(s.name not in self.disabled for s in self._watchers())

    def watch(self, system: str, user: str, url: str, *, start: int | None = None, end: int | None = None,
              deep: bool = False, json_out: bool = True) -> LLMResult:
        """Gemini дивиться відео YouTube (або його відрізок start..end, у секундах) і відповідає на запит."""
        return self._each(self._watchers(),
                          lambda slot: self._call_video(slot, system, user, url, start, end, deep, json_out))

    def _each(self, slots: list[_Slot], call: Callable[[_Slot], LLMResult]) -> LLMResult:
        errors = []
        for slot in slots:
            if slot.name in self.disabled:
                continue
            try:
                return call(slot)
            except VideoRejected:
                raise
            except HttpError as e:
                errors.append(f"{slot.name}: {e.message}")
                self._handle_http(slot, e)
            except (urllib.error.URLError, OSError, LLMError) as e:
                errors.append(f"{slot.name}: {short(str(e))}")
                self._fail(slot, short(str(e)))
        if not any(s.name not in self.disabled for s in slots):
            raise QuotaExhausted("; ".join(errors[-3:]))
        raise LLMError("; ".join(errors) or "no model answered")

    def _handle_http(self, slot: _Slot, e: HttpError) -> None:
        if e.status == 429 and any(h in e.message.lower() for h in _SHORT_HINTS):
            self.log(t("llm.busy", model=slot.name))   # завтра чекати не треба — лише цей запуск
            self.disabled.add(slot.name)
        elif e.status in (429, 402):
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


    def _call_video(self, slot: _Slot, system: str, user: str, url: str, start: int | None, end: int | None,
                    deep: bool, json_out: bool, retried: bool = False) -> LLMResult:
        video: dict[str, Any] = {"fileData": {"fileUri": url}}
        if start is not None and end is not None:
            video["videoMetadata"] = {"startOffset": f"{start}s", "endOffset": f"{end}s"}
        gen: dict[str, Any] = {"temperature": self.temperature,
                               "mediaResolution": "MEDIA_RESOLUTION_LOW"}   # ~100 токенів на секунду відео
        if json_out:
            gen["responseMimeType"] = "application/json"
        if slot.model.startswith("gemini-2"):
            gen["thinkingConfig"] = {"thinkingBudget": -1 if deep else 0}
        elif slot.model.startswith("gemini-"):
            gen["thinkingConfig"] = {"thinkingLevel": "high" if deep else "low"}
        payload = {"systemInstruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": [video, {"text": user}]}],
                   "generationConfig": gen}
        try:
            data = _request("POST", f"{GEMINI_NATIVE}models/{slot.model}:generateContent", slot.key, payload,
                            timeout=TIMEOUT * 3, key_header="x-goog-api-key")
        except HttpError as e:
            if e.status == 400 or (e.status == 403 and "caller does not have permission" in e.message.lower()):
                # відео недоступне Gemini (приватне, видалене) — справа у відео, не в моделі й не в ключі
                raise VideoRejected(e.message) from e
            if e.status == 429 and not retried and not any(h in e.message.lower() for h in _DAILY_HINTS):
                self.log(t("llm.waiting", model=slot.name, seconds=int(self.rate_limit_wait)))
                self.sleep(self.rate_limit_wait)
                return self._call_video(slot, system, user, url, start, end, deep, json_out, retried=True)
            raise
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            if reason:
                raise VideoRejected(f"blocked: {reason}")
            raise LLMError("empty response")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMError(f"empty response ({candidates[0].get('finishReason', '')})")
        usage = data.get("usageMetadata") or {}
        return LLMResult(text, slot.name, int(usage.get("promptTokenCount") or 0),
                         int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0))


def model_status(cfg: dict[str, Any], paths: Paths) -> tuple[list[str], list[str]]:
    """(моделі з ключем, з них вичерпані на сьогодні) — для панелі меню, без звернень до мережі."""
    names = [f"{p.name}/{m}" for p in providers_from_config(cfg["models"])
             if not p.key_env or keys.get_secret(p.key_env, paths) for m in p.models]
    state_path = paths.data_dir / "quota.json"
    try:
        st = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except (ValueError, OSError):
        st = {}
    used = set(st.get("exhausted", [])) if st.get("day") == quota_day() else set()
    return names, [n for n in names if n in used]


def short(s: str, n: int = 300) -> str:
    return " ".join(s.split())[:n]
