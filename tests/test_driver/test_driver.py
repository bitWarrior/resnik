# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""Tests for resnik.driver — the reusable compile pipeline behind the CLI
and the IDE.  No GUI, no Rich; rustc only where marked."""

import shutil
from pathlib import Path

import pytest

from resnik.driver import (
    CheckResult,
    CompileOptions,
    Diagnostic,
    GenerateResult,
    Reporter,
    check,
    generate,
)

VALID = "HELLO: PROGRAM;\n  WRITE(6) 'HELLO, WORLD';\nCLOSE HELLO;\n"
LEX_ERROR = "T: PROGRAM;\n  DECLARE B BIT(8) INITIAL(BIN '10201010');\nCLOSE T;\n"
SYNTAX_ERROR = "T: PROGRAM;\n  DECLARE X INTEGER\n  X = 1;\nCLOSE T;\n"
SEMANTIC_DUP = "T: PROGRAM;\n  DECLARE X INTEGER;\n  DECLARE X SCALAR;\nCLOSE T;\n"
SEMANTIC_EXIT = "T: PROGRAM;\n  DECLARE X INTEGER;\n  EXIT;\nCLOSE T;\n"
WITH_PROC = (
    "MAIN: PROGRAM;\n"
    "  DECLARE A INTEGER;\n"
    "  ADD_ONE: PROCEDURE(P) ASSIGN(Q);\n"
    "    DECLARE P INTEGER;\n"
    "    DECLARE Q INTEGER;\n"
    "    Q = P + 1;\n"
    "  CLOSE ADD_ONE;\n"
    "  CALL ADD_ONE(1) ASSIGN(A);\n"
    "CLOSE MAIN;\n"
)


class RecordingReporter(Reporter):
    """Collects every driver event for assertions."""

    def __init__(self) -> None:
        self.events = []

    def phase(self, title):
        self.events.append(("phase", title))
        return super().phase(title)

    def success(self, m):
        self.events.append(("success", m))

    def info(self, m):
        self.events.append(("info", m))

    def error(self, m):
        self.events.append(("error", m))

    def diagnostic(self, d):
        self.events.append(("diagnostic", d))

    def raw(self, t):
        self.events.append(("raw", t))

    def kinds(self):
        return [k for k, _ in self.events]


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------


class TestCheck:
    def test_valid_source_is_ok(self, tmp_path):
        r = check(_write(tmp_path, "ok.hal", VALID))
        assert isinstance(r, CheckResult)
        assert r.ok is True
        assert r.diagnostics == []
        assert r.ast is not None
        assert len(r.tokens) > 0

    def test_valid_source_reports_phases_and_successes(self, tmp_path):
        rep = RecordingReporter()
        check(_write(tmp_path, "ok.hal", VALID), reporter=rep)
        assert rep.kinds() == ["phase", "success", "phase", "success"]
        assert rep.events[0][1] == "Phase 1: Lexical Analysis"
        assert rep.events[1][1].startswith("Tokenized ")
        assert rep.events[2][1] == "Phase 2: Syntactic Analysis"
        assert rep.events[3][1] == "Generated Abstract Syntax Tree"

    def test_lexer_error_is_a_lexer_diagnostic_with_location(self, tmp_path):
        rep = RecordingReporter()
        r = check(_write(tmp_path, "lex.hal", LEX_ERROR), reporter=rep)
        assert r.ok is False
        assert r.ast is None
        assert [d.phase for d in r.diagnostics] == ["lexer"]
        d = r.diagnostics[0]
        assert d.line == 2
        assert "BIN" in d.message
        # Legacy CLI text is preserved verbatim.
        assert str(d) == d.text
        assert d.text.startswith("File ") and "Line 2, Column" in d.text
        # Reporter saw: phase, error summary, one diagnostic — and stopped.
        assert rep.kinds() == ["phase", "error", "diagnostic"]
        assert rep.events[1][1] == "Lexer found 1 error(s)"

    def test_syntax_error_is_a_parser_diagnostic(self, tmp_path):
        rep = RecordingReporter()
        r = check(_write(tmp_path, "syn.hal", SYNTAX_ERROR), reporter=rep)
        assert r.ok is False
        assert [d.phase for d in r.diagnostics] == ["parser"]
        assert r.diagnostics[0].line == 3
        assert ("error", "Parser found 1 error(s)") in rep.events
        # The lexer phase succeeded first.
        assert rep.kinds()[:2] == ["phase", "success"]

    @pytest.mark.parametrize(
        "source, needle",
        [
            (SEMANTIC_DUP, "already declared"),
            (SEMANTIC_EXIT, "EXIT"),
        ],
    )
    def test_semantic_errors_surface_as_parser_diagnostics(
        self, tmp_path, source, needle
    ):
        r = check(_write(tmp_path, "sem.hal", source))
        assert r.ok is False
        assert len(r.diagnostics) == 1
        d = r.diagnostics[0]
        assert d.phase == "parser"
        assert d.line == 3
        assert d.column > 0
        assert needle in d.message

    def test_errors_property_filters_by_severity(self, tmp_path):
        r = check(_write(tmp_path, "sem.hal", SEMANTIC_DUP))
        assert r.errors == r.diagnostics
        r.diagnostics.append(Diagnostic("parser", "note", severity="warning"))
        assert len(r.errors) == 1

    def test_token_and_ast_export(self, tmp_path):
        src = _write(tmp_path, "ok.hal", VALID)
        tok = tmp_path / "t.json"
        ast = tmp_path / "a.json"
        rep = RecordingReporter()
        r = check(src, reporter=rep, tokens_out=tok, ast_out=ast)
        assert r.ok
        assert tok.exists() and ast.exists()
        infos = [m for k, m in rep.events if k == "info"]
        assert any("Tokens exported" in m for m in infos)
        assert any("AST exported" in m for m in infos)

    def test_templib_is_searched(self, tmp_path):
        lib = tmp_path / "TEMPLIB"
        lib.mkdir()
        (lib / "ADD_ONE.tpl").write_text(
            "ADD_ONE: PROCEDURE(P) ASSIGN(Q) TEMPLATE;\n"
            "  DECLARE P INTEGER;\n  DECLARE Q INTEGER;\nCLOSE ADD_ONE;\n",
            encoding="utf-8",
        )
        src = _write(
            tmp_path,
            "user.hal",
            "D INCLUDE TEMPLATE ADD_ONE\n"
            "MAIN: PROGRAM;\n  DECLARE A INTEGER;\n"
            "  CALL ADD_ONE(1) ASSIGN(A);\nCLOSE MAIN;\n",
        )
        # Put the source somewhere the library is NOT a sibling so the
        # explicit templib path is what makes it resolve.
        other = tmp_path / "elsewhere"
        other.mkdir()
        moved = other / "user.hal"
        moved.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        assert check(moved).ok is False  # not found without --templib
        assert check(moved, templib=[lib]).ok is True


# ---------------------------------------------------------------------------
# CompileOptions
# ---------------------------------------------------------------------------


class TestCompileOptions:
    def test_defaults_mirror_cli(self, tmp_path):
        o = CompileOptions(input_file=tmp_path / "a.hal")
        assert o.target == "host"
        assert o.opt_level == "2"
        assert o.emit_exe is False and o.build is False
        assert o.rust_path == tmp_path / "a.rs"
        assert o.cross_triple is None

    def test_rust_override_and_cross_triple(self, tmp_path):
        o = CompileOptions(
            input_file=tmp_path / "a.hal", rust=tmp_path / "out.rs", target="esp32-c3"
        )
        assert o.rust_path == tmp_path / "out.rs"
        assert o.cross_triple == "riscv32imc-unknown-none-elf"

    def test_unknown_target_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown target"):
            CompileOptions(input_file=tmp_path / "a.hal", target="z80")


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_writes_rust_next_to_input(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        r = generate(CompileOptions(input_file=src))
        assert isinstance(r, GenerateResult)
        assert r.ok is True
        assert r.rust_path == tmp_path / "hello.rs"
        assert r.rust_path.exists()
        assert "fn main" in r.rust_code
        assert r.exe is None and r.cargo is None
        assert r.executable is None

    def test_honours_rust_override(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        out = tmp_path / "sub" / "x.rs"
        out.parent.mkdir()
        r = generate(CompileOptions(input_file=src, rust=out))
        assert r.ok and out.exists()

    def test_front_end_error_stops_before_codegen(self, tmp_path):
        src = _write(tmp_path, "bad.hal", SEMANTIC_DUP)
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src), rep)
        assert r.ok is False
        assert r.rust_path is None and r.rust_code is None
        assert not (tmp_path / "bad.rs").exists()
        assert [d.phase for d in r.diagnostics] == ["parser"]
        assert r.check.ok is False
        assert ("phase", "Phase 3: Code Generation") not in rep.events

    def test_gen_tpl_writes_templates(self, tmp_path):
        src = _write(tmp_path, "p.hal", WITH_PROC)
        lib = tmp_path / "TEMPLIB"
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src, gen_tpl=lib), rep)
        assert r.ok
        assert r.templates == [str(lib / "ADD_ONE.tpl")]
        assert (lib / "ADD_ONE.tpl").exists()
        assert any("Generated 1 template(s)" in m for k, m in rep.events if k == "success")

    def test_gen_tpl_without_units_is_informational(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src, gen_tpl=tmp_path / "TPL"), rep)
        assert r.ok and r.templates == []
        assert ("info", "No reusable units (PROCEDURE/FUNCTION/COMPOOL) to template.") in rep.events

    def test_emit_cargo_scaffolds_without_building(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        proj = tmp_path / "fw"
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src, emit_cargo=proj), rep)
        assert r.ok
        assert r.cargo is not None and r.cargo.built is False
        assert (proj / "Cargo.toml").exists()
        assert (proj / "src" / "main.rs").exists()
        assert ("phase", "Phase 4: Cargo Project") in rep.events

    def test_emit_cargo_embedded_profile(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        proj = tmp_path / "fw"
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src, emit_cargo=proj, target="esp32-c3"), rep)
        assert r.ok
        assert (proj / ".cargo" / "config.toml").exists()
        assert ("info", "Target profile: esp32-c3 (no_std bare-metal)") == rep.events[0]

    def test_result_reports_no_executable_without_build(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        r = generate(CompileOptions(input_file=src, emit_cargo=tmp_path / "fw"))
        assert r.executable is None


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not on PATH")
class TestGenerateWithRustc:
    def test_emit_exe_builds_and_reports_executable(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src, emit_exe=True), rep)
        assert r.ok, [e for e in rep.events if e[0] in ("error", "raw")]
        assert r.exe is not None and r.exe.success
        assert r.executable == str(tmp_path / "hello")
        assert Path(r.executable).exists()
        assert ("phase", "Phase 4: Native Compilation") in rep.events

    def test_template_unit_validates_as_library(self, tmp_path):
        src = _write(
            tmp_path,
            "tpl.hal",
            "ADD_ONE: PROCEDURE(P) ASSIGN(Q) TEMPLATE;\n"
            "  DECLARE P INTEGER;\n  DECLARE Q INTEGER;\nCLOSE ADD_ONE;\n",
        )
        r = generate(CompileOptions(input_file=src, emit_exe=True))
        assert r.ok
        assert r.exe.library is True
        assert r.executable is None


# ---------------------------------------------------------------------------
# CLI is a thin wrapper over the driver
# ---------------------------------------------------------------------------


class TestCliWrapper:
    def _run(self, args):
        from click.testing import CliRunner

        from resnik.cli import main

        return CliRunner().invoke(main, args)

    def test_success_exit_code_and_banner(self, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        res = self._run(["compile", str(src)])
        assert res.exit_code == 0, res.output
        assert "Compilation completed successfully" in res.output
        assert (tmp_path / "hello.rs").exists()

    def test_front_end_error_exit_code_and_legacy_text(self, tmp_path):
        src = _write(tmp_path, "dup.hal", SEMANTIC_DUP)
        res = self._run(["compile", str(src)])
        assert res.exit_code == 1
        assert "Parser found 1 error(s)" in res.output
        assert "Line 3, Column" in res.output
        assert "already declared" in res.output

    def test_lexer_error_exit_code(self, tmp_path):
        src = _write(tmp_path, "lex.hal", LEX_ERROR)
        res = self._run(["compile", str(src)])
        assert res.exit_code == 1
        assert "Lexer found 1 error(s)" in res.output


# ---------------------------------------------------------------------------
# CLI argument round trip (used by the IDE's options dialog)
# ---------------------------------------------------------------------------


class TestCliArgsRoundTrip:
    def _full(self, tmp_path):
        src = _write(tmp_path, "p.hal", VALID)
        return CompileOptions(
            input_file=src, tokens=Path("t.json"), ast=Path("a.json"), rust=Path("o.rs"),
            emit_exe=True, opt_level="s", link_search=[Path("lib")], link_lib=["m"],
            link_obj=[Path("i2c.o")], emit_cargo=Path("fw"), build=True,
            target="esp32-c3", gen_tpl=Path("TPL"), templib=[Path("T1"), Path("T2")],
        )

    def test_defaults_produce_only_the_input(self, tmp_path):
        src = _write(tmp_path, "p.hal", VALID)
        o = CompileOptions(input_file=src)
        assert o.to_cli_args() == [str(src)]
        assert o.command_line() == f"resnik compile {src}"

    def test_to_cli_args_parses_back_to_equal_options(self, tmp_path):
        from resnik.cli import parse_compile_args

        o = self._full(tmp_path)
        assert parse_compile_args(o.to_cli_args()) == o

    def test_command_line_is_shell_quoted(self, tmp_path):
        src = _write(tmp_path, "p.hal", VALID)
        o = CompileOptions(input_file=src, templib=[Path("my lib")])
        assert o.command_line().endswith("--templib 'my lib'")

    def test_parse_compile_args_rejects_bad_input(self, tmp_path):
        import click

        from resnik.cli import parse_compile_args

        src = _write(tmp_path, "p.hal", VALID)
        with pytest.raises(click.UsageError):
            parse_compile_args([str(src), "--bogus"])
        with pytest.raises(click.UsageError):
            parse_compile_args([str(src), "--target", "z80"])
        with pytest.raises(click.UsageError):
            parse_compile_args([str(tmp_path / "missing.hal")])

    def test_resolved_relative_to_anchors_only_relative_paths(self, tmp_path):
        o = self._full(tmp_path)
        o.templib.append(Path("/abs/T3"))
        r = o.resolved_relative_to(tmp_path / "base")
        base = tmp_path / "base"
        assert r.rust == base / "o.rs" and r.tokens == base / "t.json"
        assert r.emit_cargo == base / "fw" and r.gen_tpl == base / "TPL"
        assert r.link_search == [base / "lib"] and r.link_obj == [base / "i2c.o"]
        assert r.templib == [base / "T1", base / "T2", Path("/abs/T3")]
        assert r.link_lib == ["m"] and r.input_file == o.input_file
        assert o.rust == Path("o.rs")  # original untouched


class TestForceScaffoldOption:
    def test_cli_round_trip(self, tmp_path):
        from resnik.cli import parse_compile_args

        src = _write(tmp_path, "p.hal", VALID)
        o = CompileOptions(input_file=src, emit_cargo=Path("fw"), force_scaffold=True)
        assert "--force-scaffold" in o.to_cli_args()
        assert parse_compile_args(o.to_cli_args()) == o
        o2 = CompileOptions(input_file=src, emit_cargo=Path("fw"))
        assert "--force-scaffold" not in o2.to_cli_args()

    def test_generate_reports_kept_files(self, tmp_path):
        src = _write(tmp_path, "p.hal", VALID)
        proj = tmp_path / "fw"
        generate(CompileOptions(input_file=src, emit_cargo=proj, target="esp32-c3"))
        (proj / "Cargo.toml").write_text("# mine\n", encoding="utf-8")
        rep = RecordingReporter()
        r = generate(CompileOptions(input_file=src, emit_cargo=proj, target="esp32-c3"), rep)
        assert r.ok and (proj / "Cargo.toml").read_text() == "# mine\n"
        infos = [m for k, m in rep.events if k == "info"]
        assert any(m.startswith("kept existing") and "Cargo.toml" in m for m in infos)
        assert any("--force-scaffold regenerates" in m for m in infos)
