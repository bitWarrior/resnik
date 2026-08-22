"""IdeCompileSettings: serialisation, CLI-argument generation and the
round-trip through the real Click parser.  No display needed."""

from pathlib import Path

import pytest

from resnik.cli import parse_compile_args
from resnik.driver import CompileOptions

from resnik_ide.options import IdeCompileSettings, OptionsError

FULL = IdeCompileSettings(
    target="esp32-c3", emit_exe=True, opt_level="s", emit_cargo="fw", build=True,
    rust="out/x.rs", tokens="t.json", ast="a.json",
    link_search=["lib", "/abs/lib"], link_lib=["m", "i2c"], link_obj=["i2c.o"],
    gen_tpl="TPL", templib=["T1", "/abs/T2"], extra_args="",
)


def _src(tmp_path: Path) -> Path:
    p = tmp_path / "prog.hal"
    p.write_text("A: PROGRAM;\nCLOSE A;\n", encoding="utf-8")
    return p


class TestSerialisation:
    def test_round_trip(self):
        assert IdeCompileSettings.from_dict(FULL.to_dict()) == FULL
        assert FULL.copy() == FULL and FULL.copy() is not FULL

    def test_defaults_round_trip(self):
        d = IdeCompileSettings()
        assert IdeCompileSettings.from_dict(d.to_dict()) == d

    def test_tolerant_of_unknown_missing_and_bad_types(self):
        s = IdeCompileSettings.from_dict({
            "target": "z80",          # unknown target → default
            "opt_level": 7,           # not a valid level → default
            "emit_exe": 1,            # truthy → True
            "link_lib": "m",          # wrong type → default list
            "templib": ["a", "", 3],  # coerced, blanks dropped
            "future_key": {"x": 1},   # ignored
        })
        assert s.target == "host"
        assert s.opt_level == "2"
        assert s.emit_exe is True
        assert s.link_lib == []
        assert s.templib == ["a", "3"]
        assert s.extra_args == ""


class TestCliArgs:
    def test_defaults_are_minimal(self):
        assert IdeCompileSettings().cli_args("p.hal") == ["p.hal"]
        assert IdeCompileSettings().command_line("p.hal") == "resnik compile p.hal"
        assert IdeCompileSettings().command_line() == "resnik compile '<file>.hal'"

    def test_all_flags_emitted(self):
        args = FULL.cli_args("p.hal")
        assert args == [
            "p.hal", "--tokens", "t.json", "--ast", "a.json", "--rust", "out/x.rs",
            "--emit-exe", "--opt-level", "s",
            "--link-search", "lib", "--link-search", "/abs/lib",
            "--link-lib", "m", "--link-lib", "i2c", "--link-obj", "i2c.o",
            "--emit-cargo", "fw", "--build", "--target", "esp32-c3",
            "--gen-tpl", "TPL", "--templib", "T1", "--templib", "/abs/T2",
        ]

    def test_extra_args_are_shell_split_and_appended(self):
        s = IdeCompileSettings(extra_args="--opt-level 3 --templib 'my lib'")
        assert s.cli_args("p.hal") == ["p.hal", "--opt-level", "3", "--templib", "my lib"]
        assert s.command_line("p.hal") == "resnik compile p.hal --opt-level 3 --templib 'my lib'"

    def test_unbalanced_quote_in_extra_args(self):
        s = IdeCompileSettings(extra_args="--templib 'oops")
        with pytest.raises(OptionsError, match="Extra arguments"):
            s.cli_args("p.hal")
        assert "(Extra arguments" in s.command_line("p.hal")


class TestToCompileOptions:
    def test_matches_click_parse_and_anchors_relative_paths(self, tmp_path):
        src = _src(tmp_path)
        opts = FULL.to_compile_options(src)
        assert isinstance(opts, CompileOptions)
        # Same as the shell would parse, then anchored at the source dir.
        expected = parse_compile_args(FULL.cli_args(src)).resolved_relative_to(tmp_path)
        assert opts == expected
        assert opts.rust == tmp_path / "out" / "x.rs"
        assert opts.link_search == [tmp_path / "lib", Path("/abs/lib")]
        assert opts.templib == [tmp_path / "T1", Path("/abs/T2")]
        assert opts.emit_cargo == tmp_path / "fw"
        assert opts.target == "esp32-c3" and opts.build and opts.emit_exe
        assert opts.opt_level == "s"
        assert opts.link_lib == ["m", "i2c"]

    def test_defaults_give_plain_options(self, tmp_path):
        src = _src(tmp_path)
        opts = IdeCompileSettings().to_compile_options(src)
        assert opts == CompileOptions(input_file=src)
        assert opts.rust_path == tmp_path / "prog.rs"

    def test_extra_args_override_fields(self, tmp_path):
        src = _src(tmp_path)
        s = IdeCompileSettings(opt_level="2", extra_args="--opt-level z --emit-exe")
        opts = s.to_compile_options(src)
        assert opts.opt_level == "z" and opts.emit_exe is True

    def test_invalid_extra_args_raise_options_error(self, tmp_path):
        src = _src(tmp_path)
        with pytest.raises(OptionsError, match="No such option"):
            IdeCompileSettings(extra_args="--bogus").to_compile_options(src)
        with pytest.raises(OptionsError, match="Invalid value"):
            IdeCompileSettings(extra_args="--target z80").to_compile_options(src)
        with pytest.raises(OptionsError, match="not valid here"):
            IdeCompileSettings(extra_args="--help").to_compile_options(src)

    def test_missing_source_is_an_options_error(self, tmp_path):
        with pytest.raises(OptionsError, match="does not exist"):
            IdeCompileSettings().to_compile_options(tmp_path / "nope.hal")
