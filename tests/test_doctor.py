from __future__ import annotations

import sys
from collections import namedtuple

import pytest

from backlog_digest import doctor, i18n, tools

Usage = namedtuple("Usage", "total used free")
real_find = tools.find   # до заглушки нижче


@pytest.fixture(autouse=True)
def no_real_tools(monkeypatch):
    monkeypatch.setattr(tools, "find", lambda tool: None)
    monkeypatch.setattr(doctor, "_can_connect", lambda host, timeout=4.0: True)


def by_title(checks, key, **params):
    title = i18n.t(key, **params)
    return next(c for c in checks if c.title == title)


@pytest.mark.parametrize("lang", i18n.UI_LANGUAGES)
def test_report_in_every_language(paths, lang, capsys):
    i18n.set_language(lang)
    code = doctor.run(paths)
    out = capsys.readouterr().out
    assert code == 0
    assert "doctor." not in out and "plural." not in out   # жодного неперекладеного ключа
    assert i18n.tn("plural.doctor_warns", 2) in out   # немає config.toml і ключа моделі


def test_fresh_install_warns_about_config_and_keys(paths):
    checks = doctor.run_checks(paths)
    assert by_title(checks, "doctor.config").status == doctor.WARN
    assert by_title(checks, "doctor.model_keys").status == doctor.WARN
    assert by_title(checks, "doctor.database").status == doctor.OK
    assert by_title(checks, "doctor.python").status == doctor.OK
    assert by_title(checks, "doctor.tool_djvulibre").status == doctor.INFO


def test_key_values_are_never_shown(paths, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "SUPER-SECRET-VALUE")
    paths.config_dir.mkdir(parents=True)
    paths.env_file.write_text("RAINDROP_TOKEN=ANOTHER-SECRET\n", encoding="utf-8")
    doctor.run(paths)
    out = capsys.readouterr().out
    assert "SUPER-SECRET-VALUE" not in out and "ANOTHER-SECRET" not in out
    checks = doctor.run_checks(paths)
    assert by_title(checks, "doctor.key", service="Gemini").status == doctor.OK
    assert not any(c.title == i18n.t("doctor.model_keys") for c in checks)


def test_broken_config_fails_with_exit_code_1(paths, capsys):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text("this is = not toml =", encoding="utf-8")
    assert doctor.run(paths) == 1
    assert "config.toml" in capsys.readouterr().out


def test_network(monkeypatch):
    monkeypatch.setattr(doctor, "_can_connect", lambda host, timeout=4.0: host != "openrouter.ai")
    assert doctor.check_network().status == doctor.WARN
    monkeypatch.setattr(doctor, "_can_connect", lambda host, timeout=4.0: False)
    assert doctor.check_network().status == doctor.FAIL


def test_offline_skips_network(paths, monkeypatch):
    monkeypatch.setattr(doctor, "_can_connect", lambda *a, **k: pytest.fail("network used"))
    checks = doctor.run_checks(paths, network=False)
    assert not any(c.title == i18n.t("doctor.network") for c in checks)


@pytest.mark.parametrize(("free_gb", "status"), [(0.1, doctor.FAIL), (1.0, doctor.WARN), (50, doctor.OK)])
def test_disk(paths, monkeypatch, free_gb, status):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda p: Usage(0, 0, int(free_gb * 1024**3)))
    assert doctor.check_disk(paths).status == status


def test_tool_found_reported_with_path(monkeypatch):
    monkeypatch.setattr(tools, "find", lambda tool: f"/opt/{tool.exe}")
    checks = doctor.check_tools()
    assert all(c.status == doctor.OK for c in checks)
    assert checks[0].detail == "/opt/djvutxt"


class TestFind:
    """Справжній tools.find (без заглушки з no_real_tools)."""

    def test_found_in_path(self, monkeypatch):
        monkeypatch.setattr(tools.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert real_find(tools.TOOLS[0]) == "/usr/bin/djvutxt"

    def test_windows_registry_fallback(self, tmp_path, monkeypatch):
        install = tmp_path / "Other Disk" / "DjVuLibre"
        install.mkdir(parents=True)
        (install / "djvutxt.exe").write_bytes(b"")
        monkeypatch.setattr(tools.shutil, "which", lambda name: None)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(tools, "registry_dirs", lambda name: [str(install)] if name == "djvulibre" else [])
        assert real_find(tools.TOOLS[0]) == str(install / "djvutxt.exe")
        assert real_find(tools.TOOLS[1]) is None

    def test_not_windows_no_registry(self, monkeypatch):
        monkeypatch.setattr(tools.shutil, "which", lambda name: None)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(tools, "registry_dirs", lambda name: pytest.fail("registry used"))
        assert real_find(tools.TOOLS[2]) is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
    def test_registry_scan_does_not_crash(self):
        assert isinstance(tools.registry_dirs("definitely-not-installed-program"), list)
