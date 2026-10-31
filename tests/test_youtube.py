from __future__ import annotations

import sys
import types

import pytest

from backlog_digest import youtube


@pytest.mark.parametrize(("url", "vid"), [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLx", None),   # це плейлист
    ("https://www.youtube.com/playlist?list=PLx", None),
    ("https://www.youtube.com/@kurzgesagt", None),
    ("https://example.com/watch?v=dQw4w9WgXcQ", None),
])
def test_video_id(url, vid):
    assert youtube.video_id(url) == vid


@pytest.mark.parametrize(("url", "expected"), [
    ("https://www.youtube.com/@kurzgesagt", "https://www.youtube.com/@kurzgesagt/videos"),
    ("https://www.youtube.com/@kurzgesagt/", "https://www.youtube.com/@kurzgesagt/videos"),
    ("https://www.youtube.com/channel/UC123", "https://www.youtube.com/channel/UC123/videos"),
    ("https://www.youtube.com/@kurzgesagt/shorts", "https://www.youtube.com/@kurzgesagt/shorts"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1", "https://www.youtube.com/playlist?list=PL1"),
    ("WL", "https://www.youtube.com/playlist?list=WL"),
])
def test_normalize_list_url(url, expected):
    assert youtube.normalize_list_url(url) == expected


def test_format_duration():
    assert youtube.format_duration(None) == ""
    assert youtube.format_duration(65) == "01:05"
    assert youtube.format_duration(3725) == "1:02:05"


def test_with_timestamps_marks_every_minute():
    text = youtube.with_timestamps([(0, "hello\nworld"), (30, "a"), (61, "b"), (90, "c"), (125, "d")])
    assert text == "[00:00] hello world a \n[01:01] b c \n[02:05] d"


@pytest.fixture
def fake_ytdlp(monkeypatch):
    calls = {}

    class FakeYDL:
        def __init__(self, opts):
            calls["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            calls["url"] = url
            if "broken" in url:
                raise RuntimeError("ERROR: [youtube:tab] This playlist does not exist\nmore details")
            return {"title": "Science - Videos", "entries": [
                {"id": "aaaaaaaaaaa", "title": "First", "channel": "Chan", "duration": 61.0},
                {"id": "bbbbbbbbbbb", "title": "[Private video]"},
                {"id": "ccccccccccc", "title": "Gone", "availability": "needs_auth"},
                None,
                {"id": "ddddddddddd", "title": "Second", "uploader": "Up"},
            ]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    return calls


def test_list_playlist(fake_ytdlp):
    listing = youtube.list_url("https://www.youtube.com/@science", cookies_file="c.txt")
    assert fake_ytdlp["url"] == "https://www.youtube.com/@science/videos"
    assert fake_ytdlp["opts"]["extract_flat"] == "in_playlist" and fake_ytdlp["opts"]["cookiefile"] == "c.txt"
    assert listing.title == "Science"
    assert [(e.id, e.title, e.channel, e.duration) for e in listing.entries] == [
        ("aaaaaaaaaaa", "First", "Chan", 61), ("ddddddddddd", "Second", "Up", None)]


def test_list_error_is_readable(fake_ytdlp):
    with pytest.raises(youtube.ListError, match="^\\[youtube:tab\\] This playlist does not exist$"):
        youtube.list_url("https://www.youtube.com/playlist?list=broken")


def test_single_video_uses_oembed(monkeypatch):
    monkeypatch.setattr(youtube, "video_info", lambda vid: youtube.Entry(vid, "Title", "Chan"))
    listing = youtube.list_url("https://youtu.be/dQw4w9WgXcQ")
    assert listing.title == "" and listing.entries[0].title == "Title"


class FakeTranscript:
    def __init__(self, lang, generated):
        self.language_code = lang
        self.is_generated = generated


class FakeList(list):
    def find_manually_created_transcript(self, langs):
        return self._find(langs, False)

    def find_generated_transcript(self, langs):
        return self._find(langs, True)

    def _find(self, langs, generated):
        for lang in langs:
            for tr in self:
                if tr.language_code == lang and tr.is_generated == generated:
                    return tr
        raise LookupError


@pytest.mark.parametrize(("available", "langs", "expected"), [
    ([("uk", False), ("en", True)], ["en", "uk"], ("uk", False)),      # ручні важливіші за мову
    ([("uk", True), ("en", True)], ["en", "uk"], ("en", True)),
    ([("de", True), ("fr", False)], ["en"], ("fr", False)),            # будь-які, ручні першими
    ([], ["en"], None),
])
def test_choose_transcript(available, langs, expected):
    tlist = FakeList(FakeTranscript(lang, gen) for lang, gen in available)
    chosen = youtube._choose(tlist, langs, LookupError)
    assert (None if chosen is None else (chosen.language_code, chosen.is_generated)) == expected


def test_video_duration_from_the_page(monkeypatch):
    import io
    import urllib.request

    from conftest import REAL_VIDEO_DURATION as duration

    pages = {"ok": b'..."lengthSeconds":"427",...', "none": b"<html></html>"}

    def fake(req, timeout=0):
        vid = req.full_url.rsplit("=", 1)[1]
        if vid == "err":
            raise OSError("offline")
        return io.BytesIO(pages[vid])

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert (duration("ok"), duration("none"), duration("err")) == (427, None, None)
