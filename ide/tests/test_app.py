"""End-to-end tests of the IDE window against the real compiler (need a
display).  Jobs run on worker threads exactly as in the app; tests pump the
Tk loop until the job reports back."""

import shutil
from pathlib import Path

import pytest

from ide.tests.conftest import LEX_ERROR, SEMANTIC_DUP, VALID, pump
from resnik.driver import Diagnostic


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _wait(app):
    pump(app, lambda: not app.busy and app.dispatcher.pending == 0)


class TestFileOps:
    def test_open_file_updates_title_and_status(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        assert app.open_file(str(src)) is True
        assert app.current_path == src.resolve()
        assert app.editor.get_text() == VALID
        assert app.title().endswith("hello.hal")
        assert str(src) in app.status_file.cget("text")
        assert f"Opened {src}" in app.log.get_text()

    def test_edit_marks_dirty_in_title(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.editor.text.insert("end", "\n")
        app.update()
        assert app.title().endswith("hello.hal*")
        assert app.status_file.cget("text").endswith("*")

    def test_save_writes_buffer(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.editor.text.insert("end", "/* tail */\n")
        assert app.save_file() is True
        assert src.read_text(encoding="utf-8").endswith("/* tail */\n")
        assert app.editor.dirty is False

    def test_new_file_resets(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.new_file()
        assert app.current_path is None
        assert app.editor.get_text() == ""
        assert "Untitled" in app.title()


class TestSemanticCheck:
    def test_valid_file_passes(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "hello.hal", VALID)))
        app.semantic_check()
        _wait(app)
        log = app.log.get_text()
        assert "Semantic Check passed" in log
        assert "Tokenized" in log
        assert app.status_msg.cget("text") == "Semantic Check: OK"
        assert app.editor.error_lines == []
        assert app.last_result.ok is True

    def test_semantic_error_is_logged_and_marked(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "dup.hal", SEMANTIC_DUP)))
        app.semantic_check()
        _wait(app)
        log = app.log.get_text()
        assert "Parser found 1 error(s)" in log
        assert "already declared" in log
        assert "Semantic Check failed" in log
        assert app.status_msg.cget("text") == "Semantic Check: 1 error(s)"
        assert app.editor.error_lines == [3]

    def test_lexer_error_is_marked(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "lex.hal", LEX_ERROR)))
        app.semantic_check()
        _wait(app)
        assert "Lexer found 1 error(s)" in app.log.get_text()
        assert app.editor.error_lines == [2]

    def test_diagnostic_click_jumps_editor(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "dup.hal", SEMANTIC_DUP)))
        app.semantic_check()
        _wait(app)
        diag = app.last_result.diagnostics[0]
        app._jump_to(diag)
        line, col = app.editor.cursor()
        assert line == 3
        assert col == diag.column

    def test_diagnostic_for_other_file_does_not_jump(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "hello.hal", VALID)))
        app.editor.goto(1, 1)
        app._jump_to(Diagnostic("lexer", "x", line=3, column=1,
                                filename=str(tmp_path / "OTHER.tpl")))
        assert app.editor.cursor() == (1, 1)
        assert "not the open file" in app.log.get_text()

    def test_untitled_buffer_is_checked_from_temp_file(self, app):
        app.editor.set_text(SEMANTIC_DUP)
        app.semantic_check()
        _wait(app)
        assert app.current_path is None
        assert app._tmpdir is not None
        assert (Path(app._tmpdir) / "untitled.hal").read_text(encoding="utf-8") == SEMANTIC_DUP
        assert app.editor.error_lines == [3]
        assert app.editor.dirty is True  # still unsaved as far as the user knows

    def test_dirty_named_file_is_saved_before_check(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.editor.text.insert("end", "/* edited */\n")
        app.semantic_check()
        _wait(app)
        assert src.read_text(encoding="utf-8").endswith("/* edited */\n")
        assert app.editor.dirty is False
        assert "Semantic Check passed" in app.log.get_text()

    def test_buttons_disabled_while_busy_then_restored(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "hello.hal", VALID)))
        app.semantic_check()
        # Immediately after starting, controls are disabled.
        assert str(app.btn_check.cget("state")) == "disabled"
        _wait(app)
        assert str(app.btn_check.cget("state")) == "normal"
        assert app.busy_label.cget("text") == ""

    def test_worker_crash_is_logged_not_fatal(self, app, tmp_path, monkeypatch):
        import resnik_ide.app as appmod

        def boom(*a, **k):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(appmod, "check", boom)
        app.open_file(str(_write(tmp_path, "hello.hal", VALID)))
        app.semantic_check()
        _wait(app)
        log = app.log.get_text()
        assert "crashed" in log and "synthetic failure" in log
        assert app.status_msg.cget("text") == "Semantic Check: internal error"
        assert str(app.btn_check.cget("state")) == "normal"


class TestCompile:
    def test_compile_writes_rust_and_links_viewer(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.compile()
        _wait(app)
        log = app.log.get_text()
        assert "$ resnik compile" in log
        assert "Compile succeeded" in log
        assert "View generated Rust" in log
        assert (tmp_path / "hello.rs").exists()
        assert app.last_rust_path == tmp_path / "hello.rs"
        assert app.status_msg.cget("text") == "Compile: OK"

    def test_compile_error_marks_line(self, app, tmp_path):
        src = _write(tmp_path, "dup.hal", SEMANTIC_DUP)
        app.open_file(str(src))
        app.compile()
        _wait(app)
        assert "Compile failed" in app.log.get_text()
        assert app.editor.error_lines == [3]
        assert not (tmp_path / "dup.rs").exists()
        assert app.last_rust_path is None

    def test_current_options_reflect_toolbar(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.var_emit_exe.set(True)
        opts = app.current_options(src)
        assert opts.emit_exe is True and opts.target == "host"
        assert app._describe(opts) == f"$ resnik compile {src} --emit-exe"

    def test_view_rust_without_compile_warns(self, app):
        app.view_rust()
        assert "run Compile first" in app.log.get_text()

    @pytest.mark.slow
    @pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not on PATH")
    def test_compile_with_emit_exe_builds_binary(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.var_emit_exe.set(True)
        app.compile()
        pump(app, lambda: not app.busy and app.dispatcher.pending == 0, timeout=180)
        log = app.log.get_text()
        assert "Built executable" in log, log
        assert (tmp_path / "hello").exists()
        assert app.status_msg.cget("text") == "Compile: OK"
