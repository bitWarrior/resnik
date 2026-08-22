"""desktop.py — .desktop entry generation/installation (no display needed)."""

import sys

from resnik_ide import desktop


class TestDesktopEntry:
    def test_entry_text(self):
        txt = desktop.desktop_entry_text(exec_cmd='"/x/resnik-ide" %F', icon="/x/logo.png")
        assert txt.startswith("[Desktop Entry]\n")
        assert "Name=Resnik IDE\n" in txt
        assert 'Exec="/x/resnik-ide" %F\n' in txt
        assert "Icon=/x/logo.png\n" in txt
        assert f"StartupWMClass={desktop.STARTUP_WM_CLASS}\n" in txt
        assert desktop.STARTUP_WM_CLASS == "Resnik-ide"
        assert "Categories=Development;IDE;\n" in txt

    def test_defaults_point_at_this_python_and_the_logo(self):
        txt = desktop.desktop_entry_text()
        assert sys.executable.rsplit("/", 1)[0] in txt  # venv bin dir
        assert "resnik_logo.png" in txt
        assert desktop.ICON_PNG.exists()

    def test_install_uninstall_in_directory(self, tmp_path):
        d = tmp_path / "apps"
        assert desktop.is_installed(d) is False
        p = desktop.install_desktop_entry(d, refresh=False)
        assert p == d / "resnik-ide.desktop" and p.exists()
        assert desktop.is_installed(d)
        assert "StartupWMClass=Resnik-ide" in p.read_text(encoding="utf-8")
        assert desktop.uninstall_desktop_entry(d, refresh=False) is True
        assert desktop.uninstall_desktop_entry(d, refresh=False) is False
        assert not p.exists()

    def test_applications_dir_honours_xdg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert desktop.applications_dir() == tmp_path / "applications"
        assert desktop.entry_path() == tmp_path / "applications" / "resnik-ide.desktop"
