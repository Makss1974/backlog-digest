"""Джерело «папка»: локальна тека або синхронізована хмарна (Google Drive, OneDrive, Dropbox).

Список елемента — підтека першого рівня (файли в корені — назва джерела).
Ключ елемента — від вмісту файлу (розмір + початок), а не від шляху: та сама книга в двох теках
конспектується один раз, а перейменований чи переміщений файл не стає новим.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from . import readers

HASH_BYTES = 1 << 20   # перший мегабайт + розмір — швидко навіть для тисяч великих книг


@dataclass
class FileEntry:
    key: str
    path: Path
    list_name: str
    title: str
    format: str         # PDF, EPUB...


def file_key(path: Path) -> str:
    h = hashlib.sha1(str(path.stat().st_size).encode())
    with path.open("rb") as f:
        h.update(f.read(HASH_BYTES))
    return "file:" + h.hexdigest()[:20]


def scan(folder: Path, default_list: str) -> Iterator[FileEntry]:
    """Усі підтримувані файли теки (рекурсивно), у сталому порядку."""
    files = sorted((p for p in folder.rglob("*") if p.is_file() and readers.is_supported(p)
                    and not p.name.startswith((".", "~$"))),
                   key=lambda p: str(p.relative_to(folder)).lower())
    for path in files:
        rel = path.relative_to(folder)
        try:
            key = file_key(path)
        except OSError:   # файл зайнятий чи ще синхронізується — візьмемо наступного разу
            continue
        yield FileEntry(key, path, rel.parts[0] if len(rel.parts) > 1 else default_list,
                        readers.title_of(path), readers.file_ext(path).lstrip(".").upper())
