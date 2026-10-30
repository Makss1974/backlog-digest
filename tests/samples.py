"""Маленькі файли різних форматів для тестів — створюються на льоту, без бінарних файлів у репозиторії."""
from __future__ import annotations

import zipfile
from pathlib import Path

PARA = "Sleep matters for memory. " * 12   # ~300 символів: більше за поріг «скану без тексту»


def pdf(path: Path, pages: list[str]) -> Path:
    """Найпростіший PDF з текстом: шрифт Helvetica, по рядку на сторінку."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        content = f"BT /F1 12 Tf 20 700 Td ({text}) Tj ET"
        kids.append(f"{len(objects) + 1} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects) + 2} 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>")
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


def docx(path: Path, paragraphs: list[str]) -> Path:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return path


def epub(path: Path, chapters: list[str]) -> Path:
    """Розділи у spine — у зворотному порядку: перевіряємо, що читаємо за spine, а не за іменами файлів."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        items = "".join(f'<item id="c{i}" href="c{i}.html"/>' for i in range(len(chapters)))
        spine = "".join(f'<itemref idref="c{i}"/>' for i in reversed(range(len(chapters))))
        z.writestr("OEBPS/book.opf", f'<package xmlns="http://www.idpf.org/2007/opf"><manifest>{items}'
                                     f'</manifest><spine>{spine}</spine></package>')
        for i, text in enumerate(chapters):
            z.writestr(f"OEBPS/c{i}.html", f"<html><body><h1>Ch {i}</h1><p>{text}</p>"
                                           f"<script>var hidden = 1;</script></body></html>")
    return path


def fb2(path: Path, paragraphs: list[str]) -> Path:
    ps = "".join(f"<p>{p}</p>" for p in paragraphs)
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/'
                    f'fictionbook/2.0"><body><title><p>Глава 1</p></title><section>{ps}</section></body>'
                    f'<body name="notes"><p>footnote text</p></body></FictionBook>', encoding="utf-8")
    return path


def odt(path: Path, heading: str, paragraphs: list[str]) -> Path:
    body = f"<text:h>{heading}</text:h>" + "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:'
                                  'xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                                  f"<office:body>{body}</office:body></office:document-content>")
    return path
