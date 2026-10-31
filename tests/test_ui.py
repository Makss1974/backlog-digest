from __future__ import annotations

import pytest

from backlog_digest import config, ui


def test_width_counts_wide_and_invisible():
    assert ui.width("abc") == 3
    assert ui.width("📊 Статистика") == 2 + 1 + 10
    assert ui.width("\033[1;36m 1\033[0m") == 2          # коди кольорів не займають місця
    assert ui.width("é") == 1                      # літера з наголосом


def test_box_lines_have_equal_width_and_wrap():
    lines = ui.box(["short", "📚 " + "word " * 30, ""], title="📥 Title", size=40)
    assert {ui.width(line) for line in lines} == {40}
    assert lines[0].startswith("╭─ 📥 Title ") and lines[-1].startswith("╰")
    assert len(lines) > 5                                 # довгий рядок перенесено


def test_grid_reduces_columns_when_too_wide():
    cells = [f"{i}  item number {i}" for i in range(1, 10)]
    assert len(ui.grid(cells, size=100, columns=3)) == 3
    assert len(ui.grid(cells, size=40, columns=3)) == 5      # дві колонки
    assert len(ui.grid(cells, size=30, columns=3)) == 9      # одна
    assert ui.grid(cells, size=100)[0].startswith("1  item number 1")   # нумерація вниз по колонках


def test_table_aligns_numbers_right():
    lines = ui.table(["Name", "Done"], [["Telegram", "67"], ["YouTube", "5"]], align="lr")
    assert lines[3] == "│ Telegram │   67 │" and lines[4] == "│ YouTube  │    5 │"
    assert len({ui.width(line) for line in lines}) == 1


def test_no_colors_when_not_a_terminal_or_switched_off(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    ui.set_colors(True)
    if ui.colors_on():
        assert ui.style("x", "bold") == "\033[1mx\033[0m"
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.style("x", "bold") == "x"
    monkeypatch.delenv("NO_COLOR")
    ui.set_colors(False)
    assert ui.style("x", "bold") == "x"
    ui.set_colors(True)


def test_colors_setting_is_validated(paths):
    cfg = config.defaults()
    cfg["general"]["colors"] = "yes"
    with pytest.raises(config.ConfigError):
        config.validate(cfg)


def test_menu_colors_toggle_is_saved(paths, monkeypatch):
    from backlog_digest import db, menu

    answers = iter(["9", "0"])
    monkeypatch.setattr(menu, "ask", lambda prompt="": next(answers))
    m = menu.Menu(paths, config.defaults(), db.connect(paths.db_file))
    m.settings()
    assert config.load(paths)["general"]["colors"] is False
    ui.set_colors(True)
