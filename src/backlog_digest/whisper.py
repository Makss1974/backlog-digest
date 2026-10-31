"""Розпізнавання мовлення на цьому комп'ютері (faster-whisper): відео без субтитрів, аудіо й відео з папок.

Необов'язкова частина: `uv tool install "backlog-digest[whisper]"` або `pip install "backlog-digest[whisper]"`
(≈100 МБ завантаження). Модель розпізнавання завантажується один раз, при першому використанні, у теку даних
програми. Відеокарта не потрібна: на процесорі модель працює в режимі int8; з відеокартою NVIDIA — швидше.
Нічого не надсилається в мережу: звук розпізнається тут.
"""
from __future__ import annotations

import importlib.util
import os
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import youtube
from .i18n import t
from .paths import Paths

# Приблизний розмір моделей (МБ) — щоб попередити перед першим завантаженням
MODEL_SIZES_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3100,
                  "large-v3-turbo": 1620}
DEFAULT_MODEL = "small"
PROGRESS_EVERY = 300   # повідомлення про хід розпізнавання — кожні 5 хвилин запису
MIN_TEXT = 50          # менше символів на весь запис — мовлення немає (музика, тиша)

Log = Callable[[str], None]
_models: dict[str, Any] = {}   # завантажена модель — одна на весь запуск


class WhisperError(Exception):
    """Не вдалося розпізнати (пошкоджений файл, непідтримуваний формат, немає моделі)."""


class NoSpeech(Exception):
    """Запис прочитано, але мовлення в ньому немає — не помилка, повторювати не треба."""


def installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def model_dir(paths: Paths) -> Path:
    return paths.data_dir / "whisper"


def model_ready(name: str, paths: Paths) -> bool:
    """Модель уже завантажена (тоді розпізнавання працює й без мережі)."""
    return any(model_dir(paths).glob(f"models--*--faster-whisper-{name}/snapshots/*/model.bin"))


def _model(name: str, paths: Paths, log: Log) -> Any:
    if name not in _models:
        # бібліотека завантаження моделей (huggingface_hub) інакше сипле в журнал смуги прогресу й попередження
        # про символьні посилання Windows і вхід без токена — для користувача вони нічого не значать
        for var in ("HF_HUB_DISABLE_PROGRESS_BARS", "HF_HUB_DISABLE_SYMLINKS_WARNING",
                    "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
            os.environ.setdefault(var, "1")
        os.environ.setdefault("HF_HUB_VERBOSITY", "error")
        warnings.filterwarnings("ignore", module="huggingface_hub")
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise WhisperError(t("whisper.not_installed")) from e
        if not model_ready(name, paths):
            log("  " + t("whisper.downloading", model=name, size=MODEL_SIZES_MB.get(name, "?")))
        try:
            _models[name] = WhisperModel(name, device="auto", compute_type="int8",
                                         download_root=str(model_dir(paths)), cpu_threads=os.cpu_count() or 4)
        except Exception as e:  # немає мережі для першого завантаження, невідома назва моделі
            raise WhisperError(t("whisper.model_error", model=name, detail=str(e)[:200])) from e
    return _models[name]


def transcribe(path: Path, model: str, paths: Paths, log: Log, language: str = "") -> tuple[youtube.Transcript,
                                                                                            int]:
    """Текст запису з мітками часу [мм:сс] і тривалість у секундах."""
    m = _model(model, paths, log)
    try:
        # vad_filter — пропускає тишу й музику; без «умови на попередній текст» модель рідше зациклюється
        segments, info = m.transcribe(str(path), language=language or None, vad_filter=True, beam_size=5,
                                      condition_on_previous_text=False)
        total = int(info.duration or 0)
        snippets: list[tuple[float, str]] = []
        next_report = PROGRESS_EVERY
        for seg in segments:
            snippets.append((float(seg.start), str(seg.text)))
            if seg.end >= next_report and total:
                log("  " + t("whisper.progress", done=youtube.format_duration(int(seg.end)),
                             total=youtube.format_duration(total)))
                next_report += PROGRESS_EVERY
    except (WhisperError, NoSpeech):
        raise
    except Exception as e:  # PyAV: пошкоджений чи непідтримуваний файл
        raise WhisperError(t("whisper.error", detail=str(e)[:200])) from e
    text = youtube.with_timestamps(snippets)
    if len(text) < MIN_TEXT:
        raise NoSpeech(t("whisper.no_speech"))
    return youtube.Transcript(text, str(info.language or ""), True, recognized=True), total
