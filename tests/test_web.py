from __future__ import annotations

import urllib.error

import pytest

from backlog_digest import readers, web
from samples import PARA, pdf

BOOKMARKS = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE><H1>Bookmarks</H1>
<DL><p>
  <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
  <DL><p>
    <DT><A HREF="https://example.com/a?utm_source=x#top">Article A</A>
    <DT><H3>Наука</H3>
    <DL><p>
      <DT><A HREF="https://example.com/b">Article B</A>
      <DT><A HREF="https://youtu.be/dQw4w9WgXcQ">A video</A>
    </DL><p>
  </DL><p>
  <DT><H3>Other</H3>
  <DL><p>
    <DT><A HREF="https://example.com/a">Article A again</A>
    <DT><A HREF="javascript:alert(1)">Not a link</A>
  </DL><p>
</DL><p>
"""


def test_bookmarks_keep_folders_and_drop_duplicates(tmp_path):
    path = tmp_path / "bookmarks.html"
    path.write_text(BOOKMARKS, encoding="utf-8")
    links = web.read_links(path)
    assert [(lk.title, lk.list_name) for lk in links] == [
        ("Article A", ""), ("Article B", "Наука"), ("A video", "Наука")]   # панель закладок — не тема


def test_csv_exports(tmp_path):
    (tmp_path / "instapaper.csv").write_text(
        "URL,Title,Selection,Folder,Timestamp\n"
        "https://example.com/1,One,,Unread,1\nhttps://example.com/2,Two,,Health,2\n", encoding="utf-8")
    links = web.read_links(tmp_path / "instapaper.csv")
    assert [(lk.url, lk.title, lk.list_name) for lk in links] == [
        ("https://example.com/1", "One", ""), ("https://example.com/2", "Two", "Health")]
    (tmp_path / "pocket.csv").write_text("title,url,time_added,tags,status\nP,https://example.com/p,1,,unread\n",
                                         encoding="utf-8")
    assert web.read_links(tmp_path / "pocket.csv")[0].title == "P"
    (tmp_path / "no_url.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert web.read_links(tmp_path / "no_url.csv") == []
    (tmp_path / "list.txt").write_text("https://example.com/x\nnot a link\n  https://example.com/y  \n",
                                       encoding="utf-8")
    assert [lk.url for lk in web.read_links(tmp_path / "list.txt")] == ["https://example.com/x",
                                                                         "https://example.com/y"]


def test_keys():
    assert web.item_key("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5") == ("youtube:dQw4w9WgXcQ", "video")
    a = web.item_key("https://Example.com/post/?utm_source=mail&id=7#comments")
    assert a == web.item_key("https://example.com/post?id=7") and a[1] == "article"
    assert a != web.item_key("https://example.com/post?id=8")
    assert web.item_key("https://www.youtube.com/@channel")[1] == "article"   # не відео — просто сторінка


def page(body: str) -> bytes:
    return (f"<html><head><title>Big title</title><meta name='author' content='Jane Doe'></head><body>"
            f"<nav>Home | About | Login</nav><article><h1>Big title</h1>{body}</article>"
            f"<footer>Cookie banner</footer></body></html>").encode()


def test_fetch_article_extracts_main_text(monkeypatch):
    paras = "".join(f"<p>{PARA} Paragraph {i}.</p>" for i in range(5))
    monkeypatch.setattr(web, "_download", lambda url: (page(paras), "text/html", "https://www.news.example/x"))
    article = web.fetch_article("https://news.example/x")
    assert "Sleep matters" in article.text and "Paragraph 4" in article.text
    assert "Cookie banner" not in article.text and "Login" not in article.text
    assert (article.title, article.author, article.site) == ("Big title", "Jane Doe", "news.example")


def test_fetch_article_without_text(monkeypatch):
    monkeypatch.setattr(web, "_download", lambda url: (page("<p>Please log in.</p>"), "text/html", url))
    with pytest.raises(readers.NoText):
        web.fetch_article("https://example.com/paywall")


def test_pdf_link_is_read_as_a_book(monkeypatch, tmp_path):
    data = pdf(tmp_path / "x.pdf", [PARA]).read_bytes()
    monkeypatch.setattr(web, "_download", lambda url: (data, "application/pdf", "https://example.com/My%20paper.pdf"))
    article = web.fetch_article("https://example.com/My%20paper.pdf")
    assert article.title == "My paper" and article.text.startswith("[p. 1]")


def test_http_errors(monkeypatch):
    def fail(code):
        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)   # type: ignore[arg-type]
        return urlopen

    monkeypatch.setattr(web.urllib.request, "urlopen", fail(404))
    with pytest.raises(web.Gone):
        web.fetch_article("https://example.com/old")
    monkeypatch.setattr(web.urllib.request, "urlopen", fail(503))
    with pytest.raises(web.FetchError):
        web.fetch_article("https://example.com/busy")


def test_wrapper_folder_and_broken_links_in_bookmarks(tmp_path):
    path = tmp_path / "tabsbook.html"
    path.write_text("""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p><DT><H3>!Tabsbook Export</H3><DL><p>
  <DT><A HREF="https://example.com/top">Top</A>
  <DT><H3>База 1</H3><DL><p>
    <DT><A HREF="http://[not-an-ip]/x">Broken</A>
    <DT><A HREF="https://example.com/a">A</A>
  </DL><p>
</DL><p></DL><p>
""", encoding="utf-8")
    links = web.read_links(path)   # зіпсоване посилання пропущено, обгортку прибрано
    assert [(lk.title, lk.list_name) for lk in links] == [("Top", ""), ("A", "База 1")]


def test_folder_filter_and_counts():
    links = [web.Link("https://e.com/1", "", "A"), web.Link("https://e.com/2", "", "A/B"),
             web.Link("https://e.com/3", "", "AB"), web.Link("https://e.com/4", "", "")]
    assert web.folder_counts(links) == {"": 4, "A": 2, "A/B": 1, "AB": 1}
    assert [lk.url[-1] for lk in links if web.in_folders(lk.list_name, ["a"])] == ["1", "2"]   # «AB» — інша тека
    assert all(web.in_folders(lk.list_name, []) for lk in links)
    assert web.relative_list("Base/Sci/Phys", ["base/sci"]) == "Sci/Phys"
    assert web.relative_list("Top/X", ["Top"]) == "Top/X"


def test_note_dir_depth_and_length(tmp_path):
    from backlog_digest import notes

    d = notes.note_dir(tmp_path, "Src", "L1/L2/L3/L4/L5/L6")
    assert d == tmp_path / "Src" / "L1" / "L2" / "L3" / "L4 · L5 · L6"
    long = notes.note_dir(tmp_path, "Src", "/".join(["x" * 60] * 8))
    assert len(long.relative_to(tmp_path).parts) < 5   # задовгий шлях — далі рівні зливаються
