from __future__ import annotations

import zipfile

import pytest

from backlog_digest import folders, readers, tools
from samples import PARA, docx, epub, fb2, odt, pdf


def test_pdf_keeps_page_numbers(tmp_path):
    text = readers.extract_text(pdf(tmp_path / "b.pdf", [PARA, "Second page " + PARA]))
    assert text.startswith("[p. 1]\nSleep matters") and "[p. 2]\nSecond page" in text


def test_scanned_pdf_is_no_text_not_an_error(tmp_path):
    with pytest.raises(readers.NoText):
        readers.extract_text(pdf(tmp_path / "scan.pdf", [""]))


def test_epub_follows_spine_and_skips_scripts(tmp_path):
    text = readers.extract_text(epub(tmp_path / "b.epub", ["first " + PARA, "second " + PARA]))
    assert text.index("## Ch 1") < text.index("## Ch 0") and "hidden" not in text


def test_fb2_skips_footnotes_and_marks_titles(tmp_path):
    text = readers.extract_text(fb2(tmp_path / "b.fb2", [PARA]))
    assert text.startswith("## Глава 1") and "footnote" not in text and text.count("Глава 1") == 1


def test_fb2_zip(tmp_path):
    inner = fb2(tmp_path / "b.fb2", [PARA])
    with zipfile.ZipFile(tmp_path / "b.fb2.zip", "w") as z:
        z.write(inner, "b.fb2")
    assert "Sleep matters" in readers.extract_text(tmp_path / "b.fb2.zip")
    assert readers.title_of(tmp_path / "b.fb2.zip") == "b"


def test_docx_odt_html_txt(tmp_path):
    assert readers.extract_text(docx(tmp_path / "d.docx", [PARA, "end"])).endswith("\nend")
    assert readers.extract_text(odt(tmp_path / "d.odt", "Intro", [PARA])).startswith("## Intro\n")
    (tmp_path / "p.html").write_text(f"<h1>Head</h1><style>.x{{}}</style><p>{PARA}</p>", encoding="utf-8")
    assert readers.extract_text(tmp_path / "p.html").startswith("## Head")
    (tmp_path / "old.txt").write_bytes(("Привіт. " * 40).encode("cp1251"))   # старе кодування Windows
    assert readers.extract_text(tmp_path / "old.txt").startswith("Привіт.")


def test_rtf_with_cyrillic(tmp_path):
    b = chr(92)   # зворотна скісна риска RTF; так простіше, ніж екранувати
    cp1251 = "".join(f"{b}'{h}" for h in ("cf", "f0", "e8", "e2", "b3", "f2"))          # «Привіт» у cp1251
    uni = "".join(f"{b}u{ord(ch)}?" for ch in "слово")                                   # «слово» як \uN
    rtf = f"{{{b}rtf1{{{b}fonttbl{{{b}f0 Arial;}}}}{b}pard {cp1251} {uni} {PARA}{b}par End}}"
    (tmp_path / "r.rtf").write_text(rtf, encoding="latin-1")
    text = readers.extract_text(tmp_path / "r.rtf")
    assert text.startswith("Привіт слово") and text.endswith("\nEnd")


def test_errors(tmp_path, monkeypatch):
    (tmp_path / "empty.pdf").write_bytes(b"")
    with pytest.raises(readers.ReadError):
        readers.extract_text(tmp_path / "empty.pdf")
    (tmp_path / "broken.epub").write_bytes(b"not a zip at all")
    with pytest.raises(readers.ReadError):
        readers.extract_text(tmp_path / "broken.epub")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises((readers.ReadError, readers.NoText)):
        readers.extract_text(tmp_path / "broken.pdf")
    monkeypatch.setattr(tools, "find", lambda tool: None)
    (tmp_path / "b.djvu").write_bytes(b"AT&T")
    with pytest.raises(readers.ReadError, match="DjVuLibre"):
        readers.extract_text(tmp_path / "b.djvu")


def test_split_chunks():
    text = "\n".join(["a" * 40] * 10)
    chunks = readers.split_chunks(text, 100)
    assert len(chunks) == 5 and all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks) == text
    assert readers.split_chunks("x" * 250, 100) == ["x" * 100, "x" * 100, "x" * 50]
    assert readers.split_chunks("short", 100) == ["short"]


def test_folder_scan_lists_and_content_keys(tmp_path):
    root = tmp_path / "Books"
    (root / "Science").mkdir(parents=True)
    (root / "a.txt").write_text(PARA, encoding="utf-8")
    (root / "Science" / "b.txt").write_text(PARA, encoding="utf-8")     # той самий вміст
    (root / "Science" / "c.txt").write_text(PARA + "!", encoding="utf-8")
    (root / "Science" / "song.mp3").write_bytes(b"x")                     # аудіо — розпізнає Whisper
    (root / "Science" / "photo.jpg").write_bytes(b"x")                    # не підтримується
    (root / "~$lock.docx").write_bytes(b"x")                              # службовий файл Word
    entries = list(folders.scan(root))
    assert [(e.list_name, e.title, e.format) for e in entries] == [
        ("", "a", "TXT"), ("Science", "b", "TXT"), ("Science", "c", "TXT"), ("Science", "song", "MP3")]
    assert entries[0].key == entries[1].key != entries[2].key
    (root / "a.txt").rename(root / "renamed.txt")
    assert next(folders.scan(root)).key == entries[0].key        # перейменування — не новий файл
