"""End-to-end tests of the IDE window against the real compiler (need a
display).  Jobs run on worker threads exactly as in the app; tests pump the
Tk loop until the job reports back."""

import shutil
from pathlib import Path

import pytest

from ide.tests.conftest import LEX_ERROR, SEMANTIC_DUP, VALID, pump
from resnik.driver import Diagnostic
from resnik_ide.options import IdeCompileSettings
from resnik_ide.settings import load_settings


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

    def test_current_options_reflect_settings_and_toolbar_target(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.apply_compile_settings(IdeCompileSettings(emit_exe=True))
        app.var_target.set("cortex-m")
        app.update()
        opts = app.current_options(src)
        assert opts.emit_exe is True and opts.target == "cortex-m"
        assert opts.command_line() == f"resnik compile {src} --emit-exe --target cortex-m"
        assert not hasattr(app, "var_emit_exe")  # checkbox replaced by Build and Run
        assert app.btn_build_run.cget("text") == "Build and Run"

    def test_view_rust_without_compile_warns(self, app):
        app.view_rust()
        assert "run Compile first" in app.log.get_text()

    @pytest.mark.slow
    @pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not on PATH")
    def test_compile_with_emit_exe_builds_binary(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(emit_exe=True))
        app.compile()
        pump(app, lambda: not app.busy and app.dispatcher.pending == 0, timeout=180)
        log = app.log.get_text()
        assert "Built executable" in log, log
        assert (tmp_path / "hello").exists()
        assert app.status_msg.cget("text") == "Compile: OK"


class TestOptionsIntegration:
    def test_settings_loaded_from_private_file(self, app, settings_file):
        assert str(settings_file) in app.log.get_text()
        assert app.settings.compile == IdeCompileSettings()

    def test_toolbar_changes_are_persisted(self, app, settings_file):
        app.var_target.set("cortex-m")
        app.update()
        assert app.settings.compile.target == "cortex-m"
        loaded, _ = load_settings(settings_file)
        assert loaded.compile.target == "cortex-m"
        assert "--target cortex-m" in app.log.get_text()

    def test_apply_compile_settings_syncs_toolbar_and_saves(self, app, settings_file):
        new = IdeCompileSettings(target="esp32-c3", emit_exe=True, opt_level="z")
        app.apply_compile_settings(new)
        assert app.var_target.get() == "esp32-c3"
        assert load_settings(settings_file)[0].compile == new

    def test_settings_restored_on_next_start(self, app, settings_file):
        from resnik_ide.app import ResnikIDE

        app.apply_compile_settings(IdeCompileSettings(target="cortex-m", gen_tpl="TPL"))
        second = ResnikIDE()
        try:
            second.withdraw()
            second.update()
            assert second.settings.compile.target == "cortex-m"
            assert second.settings.compile.gen_tpl == "TPL"
            assert second.var_target.get() == "cortex-m"
        finally:
            second.shutdown()

    def test_compile_uses_options_relative_to_source(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(rust="gen/out.rs", emit_cargo="fw"))
        app.compile()
        _wait(app)
        log = app.log.get_text()
        assert "Compile succeeded" in log, log
        assert (tmp_path / "gen" / "out.rs").exists() or (tmp_path / "gen").exists() is False
        assert (tmp_path / "fw" / "Cargo.toml").exists()
        assert f"$ resnik compile {src}" in log
        assert "--emit-cargo" in log and "--rust" in log

    def test_invalid_extra_args_block_compile(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(extra_args="--bogus"))
        app.compile()
        _wait(app)
        log = app.log.get_text()
        assert "Invalid compiler options" in log and "No such option" in log
        assert app.status_msg.cget("text") == "Compile: invalid options"
        assert not (tmp_path / "hello.rs").exists()

    def test_current_options_reflect_settings(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.apply_compile_settings(IdeCompileSettings(emit_exe=True, opt_level="s"))
        opts = app.current_options(src)
        assert opts.emit_exe is True and opts.opt_level == "s" and opts.target == "host"

    def test_open_options_dialog_applies_result(self, app, settings_file, monkeypatch):
        import resnik_ide.app as appmod

        class FakeDialog:
            def __init__(self, master, settings, source=None):
                self.settings = settings

            def show(self):
                return IdeCompileSettings(target="esp32-c3", emit_cargo="fw")

        monkeypatch.setattr(appmod, "OptionsDialog", FakeDialog)
        app.open_options()
        assert app.settings.compile.emit_cargo == "fw"
        assert app.var_target.get() == "esp32-c3"
        assert load_settings(settings_file)[0].compile.emit_cargo == "fw"

    def test_geometry_saved_on_shutdown(self, app, settings_file):
        app.deiconify()
        app.geometry("800x600+5+5")
        app.update()
        app.shutdown()
        geo = load_settings(settings_file)[0].geometry
        assert geo.startswith("800x600")


import sys as _sys

from resnik_ide.toolchain import Tool


class TestTargetPanel:
    def test_startup_probe_fills_checklist_for_host(self, app):
        keys = [r.key for r in app.target_panel.requirements]
        assert keys == ["rustc", "cargo"]
        assert all(r.satisfied for r in app.target_panel.requirements)
        log = app.log.get_text()
        assert "toolchain: rustc: rustc 1.96.1" in log
        assert "devices: no serial ports" in log
        assert app.status_msg.cget("text") == "Toolchain: OK for host"

    def test_changing_target_rerenders_requirements(self, app):
        app.var_target.set("esp32-c3")
        app.update()
        keys = [r.key for r in app.target_panel.requirements]
        assert "target:riscv32imc-unknown-none-elf" in keys and "tool:espflash" in keys
        missing = [r for r in app.target_panel.requirements if not r.satisfied]
        assert len(missing) == 3
        assert app.status_msg.cget("text") == "Toolchain: 3 missing"
        # Install buttons exist for the installable rows
        from tkinter import ttk
        buttons = [w for w in app.target_panel._req_rows if isinstance(w, ttk.Button)]
        assert len(buttons) == 3

    def test_cortex_m_target_satisfied_except_probe_rs(self, app):
        app.var_target.set("cortex-m")
        app.update()
        by = {r.key: r for r in app.target_panel.requirements}
        assert by["target:thumbv7em-none-eabihf"].satisfied
        assert not by["tool:probe-rs"].satisfied

    def test_refresh_toolchain_picks_up_new_status(self, app, fake_toolchain):
        fake_toolchain.status.espflash = Tool("espflash", "/x/espflash", "espflash 3.1.0")
        fake_toolchain.status.installed_targets.append("riscv32imc-unknown-none-elf")
        fake_toolchain.status.installed_components.append("rust-src")
        app.var_target.set("esp32-c3")
        app.refresh_toolchain()
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        assert all(r.satisfied for r in app.target_panel.requirements)
        assert str(app.target_panel.btn_refresh_tc.cget("state")) == "normal"

    def test_ports_fill_combobox_and_default_selection(self, app, fake_toolchain):
        from resnik_ide.devices import SerialPort
        fake_toolchain.ports = [SerialPort("/dev/ttyACM0"), SerialPort("/dev/ttyUSB0")]
        app.refresh_devices()
        assert list(app.target_panel.cb_port["values"]) == ["/dev/ttyACM0", "/dev/ttyUSB0"]
        assert app.var_port.get() == "/dev/ttyACM0"

    def test_port_and_chip_persist(self, app, settings_file):
        app.var_port.set("/dev/ttyACM3")
        app.var_chip.set("STM32F411CEUx")
        app.update()
        loaded, _ = load_settings(settings_file)
        assert loaded.port == "/dev/ttyACM3" and loaded.chip == "STM32F411CEUx"


class TestProcessActions:
    def test_run_process_streams_and_reports(self, app):
        ok = app.run_process([_sys.executable, "-u", "-c", "print('alpha'); print('beta')"],
                             what="Demo")
        assert ok is True
        assert app.busy
        assert str(app.target_panel.btn_stop.cget("state")) == "normal"
        _wait(app)
        log = app.log.get_text()
        assert "── Demo ──" in log and "$ " in log
        assert "alpha" in log and "beta" in log
        assert "✓ Demo finished (exit 0" in log
        assert app.status_msg.cget("text") == "Demo: OK"
        assert str(app.target_panel.btn_stop.cget("state")) == "disabled"
        assert str(app.btn_compile.cget("state")) == "normal"

    def test_failed_process_is_reported(self, app):
        app.run_process([_sys.executable, "-c", "import sys; sys.exit(4)"], what="Bad")
        _wait(app)
        assert "✗ Bad failed (exit 4" in app.log.get_text()
        assert app.status_msg.cget("text") == "Bad: exit 4"

    def test_stop_cancels_running_process(self, app):
        app.run_process([_sys.executable, "-c", "import time; time.sleep(30)"], what="Sleepy")
        app.stop()
        pump(app, lambda: not app.busy and app.dispatcher.pending == 0, timeout=20)
        log = app.log.get_text()
        assert "Stopping" in log and "■ Sleepy stopped" in log
        assert app.status_msg.cget("text") == "Sleepy: stopped"

    def test_second_process_refused_while_running(self, app):
        app.run_process([_sys.executable, "-c", "import time; time.sleep(10)"], what="One")
        assert app.run_process([_sys.executable, "-c", "print(1)"], what="Two") is False
        assert "Another job is running" in app.log.get_text()
        app.stop()
        _wait(app)

    def test_missing_executable(self, app):
        assert app.run_process(["/no/such/tool"], what="Nope") is False
        _wait(app)
        assert "✗ Nope: /no/such/tool" in app.log.get_text()
        assert app.status_msg.cget("text") == "Nope: failed"
        assert not app.busy
        assert str(app.btn_compile.cget("state")) == "normal"

    def test_install_requirement_runs_argv_then_reprobes(self, app, fake_toolchain):
        from resnik_ide.toolchain import Requirement
        req = Requirement("tool:x", "fake tool", False,
                          [_sys.executable, "-c", "print('installing')"], "")
        fake_toolchain.status.espflash = Tool("espflash", "/x/espflash", "espflash 3")
        app.install_requirement(req)
        _wait(app)
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        log = app.log.get_text()
        assert "installing" in log and "Install fake tool finished" in log
        assert "toolchain: espflash: espflash 3" in log  # re-probed afterwards

    def test_build_without_project_logs_guidance(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "hello.hal", VALID)))
        app.build()
        assert "No Cargo project" in app.log.get_text()
        app.apply_compile_settings(IdeCompileSettings(emit_cargo="fw"))
        app.build()
        assert "Cargo.toml not found" in app.log.get_text()

    def test_flash_esp32_preflight_blocks_when_tools_missing(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(emit_cargo="fw", target="esp32-c3"))
        app.compile()
        _wait(app)
        assert (tmp_path / "fw" / "Cargo.toml").exists()
        app.flash_run()
        log = app.log.get_text()
        assert "✗ missing: target\nriscv32imc-unknown-none-elf" in log
        assert "✗ missing: espflash" in log
        assert not app.busy

    def test_flash_cortex_m_needs_chip(self, app, tmp_path, fake_toolchain):
        fake_toolchain.status.probe_rs = Tool("probe-rs", "/x/probe-rs", "probe-rs 0.24")
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(emit_cargo="fw", target="cortex-m"))
        app.compile()
        _wait(app)
        app.refresh_toolchain()
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        app.var_chip.set("")
        app.flash_run()
        assert "Enter the MCU name" in app.log.get_text()
        assert not app.busy

    def test_monitor_on_host_is_a_no_op(self, app):
        app.monitor()
        assert "Monitor applies to embedded targets" in app.log.get_text()

    def test_run_host_without_build(self, app):
        app.run_host()
        assert "Nothing to run" in app.log.get_text()

    def test_elf_path_per_target(self, app, tmp_path):
        proj = tmp_path / "my-fw"
        app.var_target.set("esp32-c3")
        assert app._elf_path(proj) == proj / "target" / "riscv32imc-unknown-none-elf" / "release" / "my_fw"
        app.var_target.set("host")
        assert app._elf_path(proj) == proj / "target" / "release" / "my_fw"

    @pytest.mark.slow
    @pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not on PATH")
    def test_run_host_streams_program_output(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(emit_exe=True))
        app.compile()
        pump(app, lambda: not app.busy and app.dispatcher.pending == 0, timeout=180)
        assert "Built executable" in app.log.get_text()
        app.run_host()
        _wait(app)
        log = app.log.get_text()
        assert "── Run hello ──" in log
        assert "HELLO, WORLD" in log
        assert "✓ Run hello finished (exit 0" in log


class TestBuildAndRun:
    def _wait_all(self, app, needle, timeout=180):
        # two stages: compile job, then the external process
        pump(app, lambda: (needle in app.log.get_text()) and not app.busy
             and app.dispatcher.pending == 0, timeout=timeout)

    def test_compile_error_stops_before_running(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "dup.hal", SEMANTIC_DUP)))
        app.build_and_run()
        _wait(app)
        log = app.log.get_text()
        assert "── Build and Run: dup.hal ──" in log
        assert "--emit-exe" in log  # forced for host
        assert "Compile failed" in log and "── Run" not in log
        assert app.status_msg.cget("text") == "Build and Run: compile failed"
        assert app.editor.error_lines == [3]

    def test_does_not_change_saved_options(self, app, tmp_path, settings_file):
        app.open_file(str(_write(tmp_path, "dup.hal", SEMANTIC_DUP)))
        app.build_and_run()
        _wait(app)
        assert app.settings.compile.emit_exe is False
        assert load_settings(settings_file)[0].compile.emit_exe is False

    def test_embedded_target_requires_cargo_project(self, app, tmp_path):
        app.open_file(str(_write(tmp_path, "hello.hal", VALID)))
        app.apply_compile_settings(IdeCompileSettings(target="esp32-c3"))
        app.build_and_run()
        assert "needs a Cargo project" in app.log.get_text()
        assert not app.busy

    def test_embedded_target_compiles_then_runs_preflight(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(target="esp32-c3", emit_cargo="fw"))
        app.build_and_run()
        self._wait_all(app, "missing: espflash", timeout=60)
        log = app.log.get_text()
        assert (tmp_path / "fw" / "Cargo.toml").exists()
        assert "Compile succeeded" in log
        assert "✗ missing: target\nriscv32imc-unknown-none-elf" in log  # flash pre-flight ran

    @pytest.mark.slow
    @pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not on PATH")
    def test_host_builds_and_streams_program_output(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.build_and_run()
        self._wait_all(app, "✓ Run hello finished")
        log = app.log.get_text()
        assert "Built executable" in log
        assert "── Run hello ──" in log
        assert "HELLO, WORLD" in log
        assert "✓ Run hello finished (exit 0" in log
        assert app.status_msg.cget("text") == "Run hello: OK"

    @pytest.mark.slow
    @pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not on PATH")
    def test_template_unit_has_nothing_to_run(self, app, tmp_path):
        src = _write(tmp_path, "tpl.hal",
                     "ADD_ONE: PROCEDURE(P) ASSIGN(Q) TEMPLATE;\n"
                     "  DECLARE P INTEGER;\n  DECLARE Q INTEGER;\nCLOSE ADD_ONE;\n")
        app.open_file(str(src))
        app.build_and_run()
        self._wait_all(app, "nothing to run")
        assert "── Run" not in app.log.get_text()
