"""Оформлення меню в терміналі: рамки, таблиці, меню сіткою, кольори — без сторонніх пакетів.

Кольори — коди ANSI; вимикаються в налаштуваннях ([general] colors = false), змінною NO_COLOR
або коли вивід іде не в термінал (у файл, конвеєр, тести). Рамки лишаються й без кольорів.
Ширину рядка рахуємо «як на екрані»: емодзі й ієрогліфи займають дві клітинки, коди кольорів — нуль.
Тому значки — лише ті, що всюди широкі (без U+FE0F): 📊 📥 📝 ...
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata

_ANSI = re.compile(r"\033\[[0-9;]*m")
_STYLES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35",
           "cyan": "36", "gold": "38;5;178"}
MAX_WIDTH = 100

_colors = True
_vt_ready = False


def set_colors(on: bool) -> None:
    global _colors
    _colors = on


def colors_on() -> bool:
    return _colors and "NO_COLOR" not in os.environ and sys.stdout.isatty() and _enable_vt()


def _enable_vt() -> bool:
    """У консолі Windows коди кольорів треба ввімкнути (Windows 10+); не вийшло — без кольорів."""
    global _vt_ready
    if _vt_ready or sys.platform != "win32":
        _vt_ready = True
        return True
    try:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        _vt_ready = bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))   # VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        return False
    return _vt_ready


def style(text: str, *names: str) -> str:
    if not names or not colors_on():
        return text
    codes = ";".join(_STYLES[n] for n in names)
    return f"\033[{codes}m{text}\033[0m"


def width(text: str) -> int:
    """Ширина на екрані: без кодів кольорів; широкі символи — 2, позначки й невидимі — 0."""
    n = 0
    for ch in _ANSI.sub("", text):
        if unicodedata.combining(ch) or ch in "‍️︎":
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def pad(text: str, size: int) -> str:
    return text + " " * max(0, size - width(text))


def screen_width() -> int:
    return max(40, min(MAX_WIDTH, shutil.get_terminal_size((80, 24)).columns - 1))


def wrap(text: str, size: int) -> list[str]:
    """Перенесення за словами з урахуванням ширини на екрані; задовге слово ріжемо."""
    out: list[str] = []
    line = ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if width(candidate) <= size:
            line = candidate
            continue
        if line:
            out.append(line)
        while width(word) > size:
            cut = size
            while width(word[:cut]) > size:
                cut -= 1
            out.append(word[:cut])
            word = word[cut:]
        line = word
    out.append(line)
    return out


def box(lines: list[str], title: str = "", size: int | None = None, color: str = "gold") -> list[str]:
    """Рамка з заокругленими кутами й назвою вгорі; довгі рядки переносяться."""
    size = size or screen_width()
    inner = size - 4
    body: list[str] = []
    for line in lines:
        # рядок зі значком на початку («📦 ...») переноситься з відступом — під текст, а не під значок
        lead = line.split(" ", 1)[0]
        indent = " " * (width(lead) + 1) if line and width(lead) <= 2 and not lead.isalnum() else ""
        parts = wrap(line, inner) if line else [""]
        if indent and len(parts) > 1:
            parts = [parts[0], *(indent + p for p in wrap(line[len(parts[0]):].strip(), inner - len(indent)))]
        body += parts
    head = f" {title} " if title else ""
    top = "╭─" + head + "─" * max(0, size - 3 - width(head)) + "╮"
    edge = style("│", color)
    out = [style(top, color)]
    out += [f"{edge} {pad(line, inner)} {edge}" for line in body]
    out.append(style("╰" + "─" * (size - 2) + "╯", color))
    return out


def grid(cells: list[str], size: int | None = None, columns: int = 3) -> list[str]:
    """Пункти меню сіткою (нумерація вниз по колонках); не вміщаються — менше колонок."""
    size = size or screen_width()
    inner = size - 4
    while True:
        rows = -(-len(cells) // columns)
        cols = [cells[c * rows:(c + 1) * rows] for c in range(columns)]
        sizes = [max(width(x) for x in col) + 3 for col in cols if col]
        if columns == 1 or sum(sizes) - 3 <= inner:
            break
        columns -= 1
    return ["".join(pad(col[r], sizes[c]) for c, col in enumerate(cols) if r < len(col)).rstrip()
            for r in range(rows)]


def table(headers: list[str], rows: list[list[str]], align: str = "") -> list[str]:
    """Таблиця з рамкою. align — по літері на колонку: l (ліворуч, типово) або r (праворуч)."""
    cols = len(headers)
    sizes = [max([width(headers[i]), *(width(r[i]) for r in rows)]) for i in range(cols)]

    def line(cells: list[str], bold: bool = False) -> str:
        parts = []
        for i, c in enumerate(cells):
            right = i < len(align) and align[i] == "r"
            text = " " * (sizes[i] - width(c)) + c if right else pad(c, sizes[i])
            parts.append(style(text, "bold") if bold else text)
        return "│ " + " │ ".join(parts) + " │"

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (s + 2) for s in sizes) + right

    return [rule("┌", "┬", "┐"), line(headers, True), rule("├", "┼", "┤"), *(line(r) for r in rows),
            rule("└", "┴", "┘")]


def header(title: str, size: int | None = None) -> str:
    """Заголовок розділу: ━━ 📥 Джерела ━━━━━━━━"""
    size = size or screen_width()
    text = f"━━ {title} "
    return style(text + "━" * max(0, size - width(text)), "gold", "bold")


def item(key: str, text: str) -> str:
    """Пункт меню: номер виділено."""
    return f"{style(key.rjust(2), 'bold', 'cyan')}  {text}"
