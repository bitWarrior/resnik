"""ide.json persistence.  No display needed."""

import json

from resnik_ide.options import IdeCompileSettings
from resnik_ide.settings import (
    ENV_VAR,
    IdeSettings,
    load_settings,
    save_settings,
    settings_path,
)


class TestPath:
    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, str(tmp_path / "x.json"))
        assert settings_path() == tmp_path / "x.json"

    def test_xdg_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.platform", "linux")
        assert settings_path() == tmp_path / "resnik" / "ide.json"


class TestLoadSave:
    def test_missing_file_gives_defaults_no_warning(self, tmp_path):
        s, warn = load_settings(tmp_path / "none.json")
        assert s == IdeSettings() and warn is None

    def test_round_trip(self, tmp_path):
        p = tmp_path / "deep" / "dir" / "ide.json"
        s = IdeSettings(
            compile=IdeCompileSettings(target="cortex-m", emit_exe=True, templib=["T"]),
            geometry="800x600+10+10", port="/dev/ttyACM0", chip="STM32F411CEUx",
        )
        assert save_settings(s, p) == p
        assert not p.with_suffix(".json.tmp").exists()
        loaded, warn = load_settings(p)
        assert warn is None and loaded == s
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["version"] == 1 and data["compile"]["target"] == "cortex-m"
        assert data["port"] == "/dev/ttyACM0" and data["chip"] == "STM32F411CEUx"

    def test_corrupt_file_gives_defaults_with_warning(self, tmp_path):
        p = tmp_path / "ide.json"
        p.write_text("{not json", encoding="utf-8")
        s, warn = load_settings(p)
        assert s == IdeSettings()
        assert warn and "using defaults" in warn

    def test_wrong_shape_gives_defaults_with_warning(self, tmp_path):
        p = tmp_path / "ide.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        s, warn = load_settings(p)
        assert s == IdeSettings() and warn

    def test_env_path_used_by_default(self, tmp_path, monkeypatch):
        p = tmp_path / "cfg.json"
        monkeypatch.setenv(ENV_VAR, str(p))
        save_settings(IdeSettings(geometry="1x1+0+0"))
        assert p.exists()
        assert load_settings()[0].geometry == "1x1+0+0"
