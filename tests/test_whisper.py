from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from backlog_digest import config, doctor, whisper, youtube


@dataclass
class Seg:
    start: float
    end: float
    text: str


class FakeModel:
    instances: list[FakeModel] = []

    def __init__(self, name, device, compute_type, download_root, cpu_threads):
        self.name = name
        self.args = (device, compute_type, download_root)
        self.calls = []
        FakeModel.instances.append(self)

    def transcribe(self, path, **kw):
        self.calls.append((path, kw))
        if path.endswith("bad.mp3"):
            raise RuntimeError("Invalid data found when processing input")
        if path.endswith("music.mp3"):
            return iter([Seg(0, 3, " la la")]), types.SimpleNamespace(language="en", duration=200)
        segs = [Seg(i * 30.0, i * 30.0 + 30, f" sentence {i} of a long talk.") for i in range(25)]
        return iter(segs), types.SimpleNamespace(language="uk", duration=750)


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    FakeModel.instances = []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(whisper, "_models", {})


def test_transcribe_with_timestamps_progress_and_model_reuse(fake_faster_whisper, paths, tmp_path):
    logs = []
    tr, total = whisper.transcribe(tmp_path / "talk.mp3", "small", paths, logs.append)
    assert (tr.language, tr.generated, tr.recognized, total) == ("uk", True, True, 750)
    assert tr.text.startswith("[00:00] sentence 0") and "\n[01:00] sentence 2" in tr.text
    assert any("Downloading the speech recognition model small (≈484 MB)" in line for line in logs)
    assert sum("recognized" in line and "of 12:30" in line for line in logs) == 2   # 05:00 і 10:00
    model = FakeModel.instances[0]
    assert model.args == ("auto", "int8", str(whisper.model_dir(paths)))
    assert model.calls[0][1]["vad_filter"] is True and model.calls[0][1]["language"] is None
    whisper.transcribe(tmp_path / "other.mp3", "small", paths, logs.append, language="en")
    assert len(FakeModel.instances) == 1 and model.calls[1][1]["language"] == "en"


def test_no_speech_and_broken_file(fake_faster_whisper, paths, tmp_path):
    with pytest.raises(whisper.NoSpeech):
        whisper.transcribe(tmp_path / "music.mp3", "small", paths, lambda s: None)
    with pytest.raises(whisper.WhisperError, match="Invalid data"):
        whisper.transcribe(tmp_path / "bad.mp3", "small", paths, lambda s: None)


def test_not_installed(monkeypatch, paths, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)    # import faster_whisper -> ImportError
    monkeypatch.setattr(whisper, "_models", {})
    with pytest.raises(whisper.WhisperError, match="not installed"):
        whisper.transcribe(tmp_path / "a.mp3", "small", paths, lambda s: None)


def test_model_ready_and_doctor(paths, monkeypatch):
    cfg = config.defaults()
    monkeypatch.setattr(whisper, "installed", lambda: False)
    assert doctor.check_whisper(cfg, paths).status == doctor.INFO
    monkeypatch.setattr(whisper, "installed", lambda: True)
    check = doctor.check_whisper(cfg, paths)
    assert check.status == doctor.INFO and "484 MB" in check.detail
    snap = whisper.model_dir(paths) / "models--Systran--faster-whisper-small" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    assert whisper.model_ready("small", paths) and not whisper.model_ready("base", paths)
    assert doctor.check_whisper(cfg, paths).status == doctor.OK


def test_config_modes_and_model(paths):
    cfg = config.defaults()
    cfg["youtube"]["no_subtitles"] = "whisper"
    config.validate(cfg)
    cfg["youtube"]["no_subtitles"] = "ears"
    with pytest.raises(config.ConfigError):
        config.validate(cfg)
    cfg["youtube"]["no_subtitles"] = "skip"
    cfg["whisper"]["model"] = " "
    with pytest.raises(config.ConfigError):
        config.validate(cfg)


def test_download_audio(monkeypatch, tmp_path):
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            seen["url"] = url
            if "blocked" in url:
                raise RuntimeError("ERROR: Sign in to confirm you're not a bot")
            if "gone" in url:
                raise RuntimeError("ERROR: [youtube] gone: Private video")
            (tmp_path / "a" / "vid12345678.m4a").write_bytes(b"x")
            return {"ext": "m4a"}

        def prepare_filename(self, info):
            return str(tmp_path / "a" / "vid12345678.m4a")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    path = youtube.download_audio("vid12345678", tmp_path / "a")
    assert path.name == "vid12345678.m4a" and seen["url"].endswith("v=vid12345678")
    assert seen["opts"]["format"].startswith("bestaudio") and "postprocessors" not in seen["opts"]
    with pytest.raises(youtube.BlockedError):
        youtube.download_audio("blocked0000", tmp_path / "a")
    with pytest.raises(youtube.AudioError, match="Private video"):
        youtube.download_audio("gone0000000", tmp_path / "a")
