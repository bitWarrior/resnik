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


class TestRecentAndView:
    def test_touch_recent_moves_to_front_and_bounds(self):
        s = IdeSettings()
        for i in range(12):
            s.touch_recent(f"/f{i}.hal")
        assert len(s.recent) == 10 and s.recent[0] == "/f11.hal"
        s.touch_recent("/f5.hal")
        assert s.recent[0] == "/f5.hal" and s.recent.count("/f5.hal") == 1

    def test_round_trip_recent_font_highlight(self, tmp_path):
        p = tmp_path / "ide.json"
        s = IdeSettings(recent=["/a.hal", "/b.hal"], font_size=14, highlighting=False)
        save_settings(s, p)
        loaded, _ = load_settings(p)
        assert loaded.recent == ["/a.hal", "/b.hal"]
        assert loaded.font_size == 14 and loaded.highlighting is False

    def test_bad_values_fall_back(self):
        s = IdeSettings.from_dict({"recent": "nope", "font_size": "huge", "highlighting": 0})
        assert s.recent == [] and s.font_size == 11 and s.highlighting is False
        assert IdeSettings.from_dict({"font_size": 1000}).font_size == 40
        assert IdeSettings.from_dict({"recent": ["/x", 3, None]}).recent == ["/x"]
        assert IdeSettings.from_dict({"theme": "dark"}).theme == "dark"
        assert IdeSettings.from_dict({"theme": "purple"}).theme == "light"
        assert IdeSettings().theme == "light"
        assert IdeSettings().show_target_panel is True
        assert IdeSettings.from_dict({"show_target_panel": 0}).show_target_panel is False
        assert IdeSettings.from_dict({"log_height": "x"}).log_height == 0
        assert IdeSettings.from_dict({"log_height": 333}).log_height == 333
        assert IdeSettings.from_dict({"sash_target_panel": 640}).sash_target_panel == 640
        assert IdeSettings().sash_target_panel == 0
        assert IdeSettings().log_font_size == 10
        assert IdeSettings.from_dict({"log_font_size": 13}).log_font_size == 13
        assert IdeSettings.from_dict({"log_font_size": "big"}).log_font_size == 10
        s2 = IdeSettings.from_dict({"ui_font_family": "Noto Sans", "ui_font_size": 13,
                                    "mono_font_family": "Fira Code"})
        assert (s2.ui_font_family, s2.ui_font_size, s2.mono_font_family) == ("Noto Sans", 13, "Fira Code")
        assert IdeSettings.from_dict({"ui_font_size": 99}).ui_font_size == 24
        assert IdeSettings().ui_font_family == "" and IdeSettings().ui_font_size == 0
