# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran
"""End-to-end tests of the IDE window against the real compiler (need a
display).  Jobs run on worker threads exactly as in the app; tests pump the
Tk loop until the job reports back."""

import shutil
from pathlib import Path

import pytest

from ide.tests.conftest import LEX_ERROR, SEMANTIC_DUP, VALID, pump
from resnik.driver import Diagnostic
from resnik_ide import theme
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
        assert app.status_msg.cget("text") == "FAULT — 1 ERROR · Semantic Check"
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
            def __init__(self, master, settings, source=None, on_apply=None, **kw):
                self.settings = settings
                self.on_apply = on_apply
                assert callable(kw.get("desktop_install")) and callable(kw.get("desktop_remove"))

            def show(self):
                # Apply pressed mid-way, then OK with a further change
                self.on_apply(IdeCompileSettings(target="cortex-m"))
                new = IdeCompileSettings(target="esp32-c3", emit_cargo="fw")
                self.on_apply(new)
                return new

        monkeypatch.setattr(appmod, "OptionsDialog", FakeDialog)
        app.open_options()
        assert app.settings.compile.emit_cargo == "fw"
        assert app.var_target.get() == "esp32-c3"
        assert load_settings(settings_file)[0].compile.emit_cargo == "fw"
        log = app.log.get_text()
        assert "--target cortex-m" in log and "--emit-cargo fw --target esp32-c3" in log

    def test_real_options_dialog_apply_path(self, app, settings_file):
        from resnik_ide.options_dialog import OptionsDialog
        dlg = OptionsDialog(app, app.settings.compile, on_apply=app.apply_compile_settings)
        dlg.v_target.set("cortex-m")
        dlg.btn_apply.invoke()
        assert app.settings.compile.target == "cortex-m"
        assert app.var_target.get() == "cortex-m"
        assert load_settings(settings_file)[0].compile.target == "cortex-m"
        assert dlg.winfo_exists()
        dlg._on_cancel()
        assert app.settings.compile.target == "cortex-m"  # Apply is not undone by Cancel

    def test_geometry_saved_on_shutdown(self, app, settings_file):
        app.deiconify()
        app.geometry("800x600+5+5")
        app.update()
        app.shutdown()
        geo = load_settings(settings_file)[0].geometry
        assert geo.startswith("800x600")


import sys as _sys
from tkinter import ttk

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
        assert "rustc not found" not in log

    def test_missing_rustc_warns_that_build_is_unavailable(self, app, fake_toolchain):
        fake_toolchain.status.rustc = Tool("rustc")
        app.refresh_toolchain()
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        log = app.log.get_text()
        assert "toolchain: rustc: not found" in log
        assert "rustc not found — Semantic Check and Compile still work" in log
        assert "Build and Run needs the Rust compiler" in log

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
        assert app.status_msg.cget("text") == "FAULT — 1 ERROR · Build and Run"
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


class TestPhase4Polish:
    def test_open_file_updates_recent_and_menu(self, app, tmp_path, settings_file):
        a = _write(tmp_path, "a.hal", VALID)
        b = _write(tmp_path, "b.hal", VALID)
        app.open_file(str(a))
        app.open_file(str(b))
        assert app.settings.recent == [str(b.resolve()), str(a.resolve())]
        assert load_settings(settings_file)[0].recent[0] == str(b.resolve())
        labels = [app._menu_recent.entrycget(i, "label")
                  for i in range(app._menu_recent.index("end") + 1)
                  if app._menu_recent.type(i) == "command"]
        assert labels[:2] == [str(b.resolve()), str(a.resolve())]
        assert labels[-1] == "Clear Recent"

    def test_open_recent_missing_file_is_pruned(self, app, tmp_path):
        app.settings.recent = [str(tmp_path / "gone.hal")]
        assert app.open_recent(str(tmp_path / "gone.hal")) is False
        assert app.settings.recent == []
        assert "no longer exists" in app.log.get_text()

    def test_zoom_persists(self, app, settings_file):
        assert app.zoom(+1) == 12 and app.editor.font_size == 12
        assert load_settings(settings_file)[0].font_size == 12
        assert app.zoom(0) == 11

    def test_highlight_toggle_persists(self, app, tmp_path, settings_file):
        app.open_file(str(_write(tmp_path, "a.hal", VALID)))
        assert app.editor.highlighted("hl_keyword")
        app.var_highlight.set(False)
        app._toggle_highlight()
        assert app.editor.highlighted("hl_keyword") == []
        assert load_settings(settings_file)[0].highlighting is False

    def test_settings_restore_font_and_highlight(self, app, tmp_path, settings_file):
        from resnik_ide.app import ResnikIDE
        app.zoom(+3)
        app.var_highlight.set(False)
        app._toggle_highlight()
        second = ResnikIDE()
        try:
            second.withdraw()
            second.update()
            assert second.editor.font_size == 14
            assert second.editor.highlighting is False
        finally:
            second.shutdown()

    def test_open_folder_uses_file_manager(self, app, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(type(app), "_reveal", staticmethod(lambda p: calls.append(p) or "xdg-open"))
        assert app.open_folder() is False  # nothing open
        app.open_file(str(_write(tmp_path, "a.hal", VALID)))
        assert app.open_folder() is True and calls == [tmp_path.resolve()]
        assert app.open_project_folder() is False  # not configured
        app.apply_compile_settings(IdeCompileSettings(emit_cargo="fw"))
        assert app.open_project_folder() is False  # not created yet
        (tmp_path / "fw").mkdir()
        assert app.open_project_folder() is True and calls[-1] == tmp_path.resolve() / "fw"

    def test_target_aware_semantic_check_warns_on_embedded(self, app, tmp_path):
        # CHARACTER variables are the still-unsupported no_std feature
        # (VECTOR/MATRIX lower via libm since roadmap item 1).
        src = _write(tmp_path, "chars.hal",
                     "T: PROGRAM;\n  DECLARE S CHARACTER(8);\n  S = 'HI';\nCLOSE T;\n")
        app.open_file(str(src))
        app.var_target.set("esp32-c3")
        app.update()
        app.semantic_check()
        _wait(app)
        log = app.log.get_text()
        assert "(target esp32-c3)" in log
        assert "not yet supported on the 'esp32-c3'" in log and "CHARACTER" in log
        assert app.status_msg.cget("text") == "Semantic Check: OK; 1 target issue(s)"
        assert app.last_result.ok is True
        # host: clean
        app.var_target.set("host")
        app.update()
        app.semantic_check()
        _wait(app)
        assert app.status_msg.cget("text") == "Semantic Check: OK"


class TestDarkMode:
    def test_default_is_light(self, app):
        assert app.theme_name == "light" and app.var_dark.get() is False
        assert app.editor.text.cget("background") == theme.LIGHT["editor_bg"]

    def test_toggle_dark_restyles_and_persists(self, app, settings_file):
        app.toggle_dark()
        assert app.theme_name == "dark" and app.var_dark.get() is True
        assert app.editor.text.cget("background") == theme.DARK["editor_bg"]
        assert app.editor.gutter.cget("background") == theme.DARK["gutter_bg"]
        assert app.log.text.cget("background") == theme.DARK["log_bg"]
        assert app.editor.text.tag_cget("hl_keyword", "foreground") == theme.DARK["hl_keyword"]
        assert app.log.text.tag_cget("error", "foreground") == theme.DARK["error"]
        assert str(app._menubar.cget("background")) == theme.DARK["bg"]
        assert ttk.Style(app).theme_use() == "clam"
        assert load_settings(settings_file)[0].theme == "dark"
        assert "Theme: dark" in app.log.get_text()
        app.toggle_dark()
        assert app.theme_name == "light"
        assert app.editor.text.cget("background") == theme.LIGHT["editor_bg"]
        assert load_settings(settings_file)[0].theme == "light"

    def test_menu_checkbutton_path(self, app):
        app.var_dark.set(True)
        app._toggle_dark()
        assert app.theme_name == "dark"
        app.var_dark.set(False)
        app._toggle_dark()
        assert app.theme_name == "light"

    def test_dark_theme_restored_on_next_start(self, app, settings_file):
        from resnik_ide.app import ResnikIDE
        app.set_theme("dark")
        second = ResnikIDE()
        try:
            second.withdraw()
            second.update()
            assert second.theme_name == "dark"
            assert second.editor.text.cget("background") == theme.DARK["editor_bg"]
            second.set_theme("light")
        finally:
            second.shutdown()

    def test_options_dialog_follows_theme(self, app):
        from resnik_ide.options_dialog import OptionsDialog
        app.set_theme("dark")
        dlg = OptionsDialog(app, IdeCompileSettings())
        assert dlg.cget("background") == theme.DARK["bg"]
        assert dlg.lf_templib.text.cget("background") == theme.DARK["field_bg"]
        dlg.destroy()
        app.set_theme("light")


class TestTargetPanelVisibility:
    def test_default_visible(self, app):
        assert app.target_panel_visible is True
        assert app.var_show_target.get() is True

    def test_toggle_hides_and_persists(self, app, settings_file):
        app.toggle_target_panel()
        assert app.target_panel_visible is False
        assert app.var_show_target.get() is False
        assert str(app.target_panel) not in app._outer.panes()
        assert load_settings(settings_file)[0].show_target_panel is False
        # actions still work without the panel
        app.run_process([_sys.executable, "-c", "print('still here')"], what="Ping")
        _wait(app)
        assert "still here" in app.log.get_text()
        app.toggle_target_panel()
        assert app.target_panel_visible is True
        assert load_settings(settings_file)[0].show_target_panel is True

    def test_menu_checkbutton_path(self, app):
        app.var_show_target.set(False)
        app._toggle_target_panel()
        assert app.target_panel_visible is False
        app.var_show_target.set(True)
        app._toggle_target_panel()
        assert app.target_panel_visible is True

    def test_hidden_state_restored_on_next_start(self, app, settings_file):
        from resnik_ide.app import ResnikIDE
        app.set_target_panel_visible(False)
        second = ResnikIDE()
        try:
            second.withdraw()
            second.update()
            pump(second, lambda: not second.probing and second.dispatcher.pending == 0)
            assert second.target_panel_visible is False
            assert second.var_show_target.get() is False
            # toolchain probe still updates the (hidden) panel without error
            assert second.target_panel.requirements
        finally:
            second.shutdown()


class TestLogHeightPersistence:
    def _shown(self, app, geo="1000x700+5+5"):
        app.deiconify()
        app.geometry(geo)
        pump(app, lambda: app._paned.winfo_height() > 1, timeout=10)
        app._end_sash_restore()  # simulate "startup window over"

    def test_log_height_saved_on_release_and_shutdown(self, app, settings_file):
        self._shown(app)
        h = app._paned.winfo_height()
        app._paned.sashpos(0, h - 250)
        app.update()
        app._on_sash_release()  # what the <ButtonRelease-1> binding calls
        assert abs(app.log_height - 250) <= 2
        assert abs(load_settings(settings_file)[0].log_height - 250) <= 2
        app._paned.sashpos(0, h - 180)
        app.update()
        app.shutdown()
        assert abs(load_settings(settings_file)[0].log_height - 180) <= 2

    def test_log_height_restored_even_with_a_different_window_size(self, app, settings_file):
        from resnik_ide.app import ResnikIDE
        self._shown(app)
        app._paned.sashpos(0, app._paned.winfo_height() - 220)
        app.update()
        app.shutdown()
        second = ResnikIDE()
        try:
            second.deiconify()
            second.geometry("1000x850+5+5")  # taller window than last time
            pump(second, lambda: abs(second.log_height - 220) <= 2, timeout=10)
            assert abs(second.log_height - 220) <= 2
            assert second._paned.winfo_height() > 750  # taller than before: the editor got the extra space
        finally:
            second.shutdown()

    def test_zero_means_default_split(self, app):
        assert app.settings.log_height == 0
        app._restore_sash()  # no-op, must not raise


class TestTargetPanelWidthPersistence:
    def _shown(self, app):
        app.deiconify()
        app.geometry("1100x700+5+5")
        pump(app, lambda: app._outer.winfo_width() > 1 and app._paned.winfo_height() > 1,
             timeout=10)

    def test_width_saved_and_restored(self, app, settings_file):
        from resnik_ide.app import ResnikIDE
        self._shown(app)
        app._end_sash_restore()
        app._outer.sashpos(0, 600)
        app.update()
        app._remember_sash()
        saved = load_settings(settings_file)[0].sash_target_panel
        assert abs(saved - 600) <= 2
        app.shutdown()
        second = ResnikIDE()
        try:
            second.deiconify()
            second.geometry("1100x700+5+5")
            pump(second, lambda: abs(second._outer.sashpos(0) - 600) <= 2, timeout=10)
            assert abs(second._outer.sashpos(0) - 600) <= 2
        finally:
            second.shutdown()

    def test_hidden_panel_does_not_overwrite_width(self, app, settings_file):
        self._shown(app)
        app._end_sash_restore()
        app._outer.sashpos(0, 650)
        app.update()
        app._remember_sash()
        before = load_settings(settings_file)[0].sash_target_panel
        assert before > 0
        app.set_target_panel_visible(False)
        app.update()
        app._remember_sash()
        assert load_settings(settings_file)[0].sash_target_panel == before
        app.set_target_panel_visible(True)
        pump(app, lambda: abs(app._outer.sashpos(0) - before) <= 2, timeout=10)
        assert abs(app._outer.sashpos(0) - before) <= 2


class TestLogFontPersistence:
    def test_log_zoom_persists_and_restores(self, app, settings_file):
        assert app.log.font_size == 10
        app.log.zoom(+2)
        assert app.settings.log_font_size == 12
        assert load_settings(settings_file)[0].log_font_size == 12
        assert app.status_msg.cget("text") == "Log font size 12"
        from resnik_ide.app import ResnikIDE
        second = ResnikIDE()
        try:
            second.withdraw()
            second.update()
            assert second.log.font_size == 12
        finally:
            second.shutdown()

    def test_editor_and_log_fonts_are_independent(self, app):
        app.zoom(+3)
        assert app.editor.font_size == 14 and app.log.font_size == 10


class TestIdeFonts:
    def test_set_fonts_applies_and_persists(self, app, settings_file):
        from tkinter import font as tkfont
        app.set_fonts("DejaVu Sans", 13, "DejaVu Sans Mono")
        assert tkfont.nametofont("TkDefaultFont", root=app).cget("family") == "DejaVu Sans"
        assert int(tkfont.nametofont("TkDefaultFont", root=app).cget("size")) == 13
        assert app.editor.font_family == "DejaVu Sans Mono"
        assert app.log.font_family == "DejaVu Sans Mono"
        assert app.editor.font_size == 11 and app.log.font_size == 10  # sizes untouched
        s = load_settings(settings_file)[0]
        assert (s.ui_font_family, s.ui_font_size, s.mono_font_family) == \
            ("DejaVu Sans", 13, "DejaVu Sans Mono")
        assert "Fonts: UI DejaVu Sans 13" in app.log.get_text()
        # sizes through the same call
        app.set_fonts("DejaVu Sans", 13, "DejaVu Sans Mono", editor_size=14, log_size=9)
        assert app.editor.font_size == 14 and app.log.font_size == 9
        s = load_settings(settings_file)[0]
        assert s.font_size == 14 and s.log_font_size == 9

    def test_defaults_are_stored_as_empty(self, app, settings_file):
        from resnik_ide import fonts
        def_fam, def_size = fonts.default_ui_font(app)
        app.set_fonts("", 0, "")
        s = load_settings(settings_file)[0]
        assert (s.ui_font_family, s.ui_font_size, s.mono_font_family) == ("", 0, "")
        assert fonts.current_ui_font(app) == (def_fam, def_size)
        assert app.editor.font_family == fonts.default_mono_family(app)

    def test_fonts_restored_on_next_start(self, app, settings_file):
        from tkinter import font as tkfont
        from resnik_ide.app import ResnikIDE
        app.set_fonts("DejaVu Sans", 14, "DejaVu Sans Mono")
        second = ResnikIDE()
        try:
            second.withdraw()
            second.update()
            assert tkfont.nametofont("TkDefaultFont", root=second).cget("family") == "DejaVu Sans"
            assert int(tkfont.nametofont("TkDefaultFont", root=second).cget("size")) == 14
            assert second.editor.font_family == "DejaVu Sans Mono"
            assert second.log.font_family == "DejaVu Sans Mono"
        finally:
            second.shutdown()

    def test_open_fonts_dialog_applies_via_callback(self, app, monkeypatch):
        import resnik_ide.app as appmod

        class FakeDialog:
            def __init__(self, master, **kw):
                self.kw = kw

            def show(self):
                self.kw["on_apply"]("DejaVu Sans", 12, "", 13, 11)
                return ("DejaVu Sans", 12, "", 13, 11)

        monkeypatch.setattr(appmod, "FontDialog", FakeDialog)
        app.zoom(+1)  # editor 12 → the dialog must be opened with current sizes
        app.open_fonts()
        assert app.settings.ui_font_size == 12 and app.settings.ui_font_family == "DejaVu Sans"
        assert app.editor.font_size == 13 and app.log.font_size == 11

    def test_dialog_opens_with_current_sizes(self, app, monkeypatch):
        import resnik_ide.app as appmod
        seen = {}

        class FakeDialog:
            def __init__(self, master, **kw):
                seen.update(kw)

            def show(self):
                return None

        monkeypatch.setattr(appmod, "FontDialog", FakeDialog)
        app.zoom(+2)
        app.log.zoom(-1)
        app.open_fonts()
        assert seen["editor_size"] == 13 and seen["log_size"] == 9

    def test_view_menu_has_no_size_entries(self, app):
        labels = []
        m = app._menubar
        for i in range(m.index("end") + 1):
            if m.type(i) == "cascade" and m.entrycget(i, "label") == "View":
                sub = m.nametowidget(m.entrycget(i, "menu"))
                labels = [sub.entrycget(j, "label") for j in range(sub.index("end") + 1)
                          if sub.type(j) in ("command", "checkbutton")]
        assert "Fonts…" in labels
        assert not any("Font Size" in lab or "Larger" in lab or "Smaller" in lab for lab in labels)


class TestAbout:
    def test_about_window_has_logo_and_description(self, app):
        import tkinter as tk
        app.about()
        win = app._about_window
        assert isinstance(win, tk.Toplevel) and win.winfo_exists()
        assert win.title() == "About Resnik"
        assert app._about_logo is not None and app._about_logo.width() > 0
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(str(c.cget("text")))
                except tk.TclError:
                    pass
                walk(c)
        walk(win)
        blob = "\n".join(texts)
        assert "Resnik HAL/S Compiler" in blob
        assert "Space Shuttle" in blob and "Rust" in blob
        assert "GPL-3.0" in blob
        from resnik import __version__ as cv
        assert cv in blob
        win.destroy()

    def test_about_without_logo_still_opens(self, app, monkeypatch):
        import tkinter as tk
        real = tk.PhotoImage

        def boom(*a, **k):
            raise tk.TclError("no image")

        monkeypatch.setattr(tk, "PhotoImage", boom)
        app.about()
        assert app._about_window.winfo_exists() and app._about_logo is None
        app._about_window.destroy()
        monkeypatch.setattr(tk, "PhotoImage", real)


class TestWindowIcon:
    def test_icon_images_loaded_in_three_sizes(self, app):
        sizes = sorted(img.width() for img in app._icon_images)
        assert sizes == [32, 64, 200]

    def test_missing_icons_are_tolerated(self, tk_root, settings_file, fake_toolchain, monkeypatch, tmp_path):
        # tk_root skips the test on a machine with no display (it builds its
        # own ResnikIDE instead of using the ``app`` fixture, which skips).
        tk_root.destroy()
        from resnik_ide.app import ResnikIDE
        monkeypatch.setattr(ResnikIDE, "IMAGES_DIR", tmp_path / "nope")
        app = ResnikIDE()
        try:
            app.withdraw()
            assert app._icon_images == []
        finally:
            app.shutdown()


class TestDesktopIntegration:
    def test_wm_class_matches_desktop_entry(self, app):
        from resnik_ide import desktop
        # Tk capitalises the class part; the .desktop entry must carry exactly that.
        assert app.winfo_class() == desktop.STARTUP_WM_CLASS == "Resnik-ide"
        assert "StartupWMClass=" + app.winfo_class() in desktop.desktop_entry_text()

    def test_install_and_remove_via_options_desktop_tab(self, app, tmp_path, monkeypatch):
        from resnik_ide.options_dialog import OptionsDialog
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        dlg = OptionsDialog(app, app.settings.compile, on_apply=app.apply_compile_settings,
                            desktop_install=app.install_desktop_entry,
                            desktop_remove=app.uninstall_desktop_entry)
        assert "Not installed" in dlg.lbl_desktop_status.cget("text")
        dlg.btn_desktop_install.invoke()
        path = tmp_path / "applications" / "resnik-ide.desktop"
        assert path.exists()
        assert "Desktop entry installed" in app.log.get_text()
        assert dlg.lbl_desktop_status.cget("text") == f"Installed: {path}"
        dlg.btn_desktop_remove.invoke()
        assert not path.exists()
        assert "Desktop entry removed" in app.log.get_text()
        assert "Not installed" in dlg.lbl_desktop_status.cget("text")
        dlg.destroy()

    def test_help_menu_has_only_about(self, app):
        m = app._menubar
        for i in range(m.index("end") + 1):
            if m.type(i) == "cascade" and m.entrycget(i, "label") == "Help":
                sub = m.nametowidget(m.entrycget(i, "menu"))
                labels = [sub.entrycget(j, "label") for j in range(sub.index("end") + 1)
                          if sub.type(j) == "command"]
                assert labels == ["About"]

    def test_cli_flags(self, tmp_path, monkeypatch, capsys):
        from resnik_ide.app import main
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert main(["--install-desktop"]) == 0
        assert (tmp_path / "applications" / "resnik-ide.desktop").exists()
        assert "Installed" in capsys.readouterr().out
        assert main(["--uninstall-desktop"]) == 0
        assert not (tmp_path / "applications" / "resnik-ide.desktop").exists()


class TestShuttleFlavour:
    def test_met_holds_at_zero_before_launch(self, app):
        import time
        assert app.launched is False
        time.sleep(1.1)
        assert app.met() == "MET 000:00:00:00"  # pre-launch: clock not running
        text = app.log_met()
        assert "pre-launch" in text and text in app.log.get_text()

    def test_met_starts_at_flash_completion_and_reflash_resets(self, app):
        import re
        import time
        app._met_line_hook("some other output line")
        assert app.launched is False
        app._met_line_hook("[2026-09-13T04:00:00Z INFO ] Flashing has completed!")
        assert app.launched is True
        assert "LIFTOFF — MET 000:00:00:00" in app.log.get_text()
        time.sleep(1.1)
        assert app.met() == "MET 000:00:00:01"
        text = app.log_met()
        assert "pre-launch" not in text and re.fullmatch(r"MET \d{3}:\d{2}:\d{2}:\d{2}", text)
        app.met_launch()  # re-flash → T-0 again
        assert app.met() == "MET 000:00:00:00"

    def test_flash_run_wires_the_met_hook(self, app, fake_toolchain, tmp_path, monkeypatch):
        fake_toolchain.status.espflash = Tool("espflash", "/x/espflash", "espflash 4.6.0")
        fake_toolchain.status.installed_targets.append("riscv32imc-unknown-none-elf")
        fake_toolchain.status.installed_components.append("rust-src")
        app.refresh_toolchain()
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(target="esp32-c3", emit_cargo="fw"))
        app.compile()
        _wait(app)
        seen = {}
        monkeypatch.setattr(app, "run_process",
                            lambda argv, **kw: seen.update(kw) or True)
        app.flash_run()
        assert seen.get("line_hook") == app._met_line_hook

    def test_menu_has_met_command(self, app):
        m = app._menubar
        for i in range(m.index("end") + 1):
            if m.type(i) == "cascade" and m.entrycget(i, "label") == "View":
                sub = m.nametowidget(m.entrycget(i, "menu"))
                labels = [sub.entrycget(j, "label") for j in range(sub.index("end") + 1)
                          if sub.type(j) == "command"]
                assert "Mission Elapsed Time" in labels

    def test_fault_status_and_clear(self, app, tmp_path):
        bad = _write(tmp_path, "dup.hal", SEMANTIC_DUP)
        good = _write(tmp_path, "ok.hal", VALID)
        app.open_file(str(bad))
        app.compile()
        _wait(app)
        assert app.status_msg.cget("text") == "FAULT — 1 ERROR · Compile"
        assert "GPC FAULT CLEARED" not in app.log.get_text()
        app.open_file(str(good))
        app.semantic_check()
        _wait(app)
        assert "GPC FAULT CLEARED" in app.log.get_text()
        assert app.status_msg.cget("text") == "Semantic Check: OK"
        # a second clean run does not repeat the message
        app.semantic_check()
        _wait(app)
        assert app.log.get_text().count("GPC FAULT CLEARED") == 1

    def test_pass_commands(self, app, tmp_path, monkeypatch):
        from resnik_ide import pass_commands
        app.open_file(str(_write(tmp_path, "ok.hal", VALID)))
        assert app.run_pass_command("SPEC 1 PRO") == "toolchain"
        assert "SPEC 1  rustc: rustc 1.96.1" in app.log.get_text()
        assert app.run_pass_command("spec 2") == "options"
        assert "SPEC 2  resnik compile ok.hal" in app.log.get_text()
        assert app.run_pass_command("SPEC 67 PRO") == "sts1"
        assert "bug heard 'round the world" in app.log.get_text()
        assert app.run_pass_command("ITEM 1 EXEC") == "semantic_check"
        _wait(app)
        assert "Semantic Check passed" in app.log.get_text()
        assert app.run_pass_command("item 5") == "clear_log"
        assert app.log.get_text() == ""
        assert app.run_pass_command("ITEM 6 EXEC") == "met"
        assert "MET 000:00:00:00 (pre-launch" in app.log.get_text()
        app.met_launch()
        app.run_pass_command("item 6")
        lines = app.log.get_text().splitlines()
        assert any("MET 000:00:00:0" in ln and "pre-launch" not in ln for ln in lines[-1:])
        assert app.run_pass_command("?") == "help"
        assert "ITEM 3 EXEC — Build and Run" in app.log.get_text()
        assert app.run_pass_command("SPEC 99 PRO") == pass_commands.ILLEGAL_ENTRY
        assert "ILLEGAL ENTRY" in app.log.get_text()
        assert app.run_pass_command("hello world") == pass_commands.ILLEGAL_ENTRY
        opened = []
        monkeypatch.setattr(app, "about", lambda: opened.append(1))
        assert app.run_pass_command("OPS 9 PRO") == "about" and opened == [1]
        assert app.run_pass_command("") == ""

    def test_pass_entry_return_and_history(self, app):
        # What the <Return> binding on the PASS> entry calls: run the entry's text.
        app.var_pass.set("SPEC 67 PRO")
        assert app.run_pass_command() == "sts1"
        assert app.var_pass.get() == "" and "STS-1" in app.log.get_text()
        app._pass_history(-1)
        assert app.var_pass.get() == "SPEC 67 PRO"
        app._pass_history(+1)
        assert app.var_pass.get() == ""


class TestStartupBannerAndRemembrance:
    def test_banner_logged_at_startup(self, app):
        log = app.log.get_text()
        assert "PASS STARTUP ✓" in log and "GPC 1 ONLINE" in log

    def test_remembrance_dates(self, app):
        import datetime as dt
        assert app.refresh_remembrance(dt.date(2026, 1, 28)) == "In memory of the crew of STS-51-L"
        assert app.status_remember.cget("text") == "In memory of the crew of STS-51-L"
        assert "Challenger" in app.log.get_text()
        assert app.refresh_remembrance(dt.date(2026, 2, 1)) == "In memory of the crew of STS-107"
        assert "Columbia, STS-107" in app.log.get_text()
        assert app.refresh_remembrance(dt.date(2026, 4, 12)) == "HAIL Columbia!"
        assert app.status_remember.cget("text") == "HAIL Columbia!"
        assert app.refresh_remembrance(dt.date(2026, 8, 21)) is None
        assert app.status_remember.cget("text") == ""
        # logged when the text appears (again, after being cleared) — but the
        # hourly re-check on the same day does not repeat it
        app.refresh_remembrance(dt.date(2026, 4, 12))
        app.refresh_remembrance(dt.date(2026, 4, 12))
        app.refresh_remembrance(dt.date(2026, 4, 12))
        assert app.log.get_text().count("HAIL Columbia!") == 2


class TestRustViewer:
    def test_viewer_has_line_numbers_and_is_read_only(self, app, tmp_path):
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.compile()
        _wait(app)
        app.view_rust()
        win, viewer = app._rust_window, app._rust_viewer
        win.deiconify()
        win.geometry("800x500+10+10")
        pump(app, lambda: viewer.gutter.find_all(), timeout=10)
        numbers = [viewer.gutter.itemcget(i, "text") for i in viewer.gutter.find_all()]
        assert numbers[:3] == ["1", "2", "3"]
        assert "fn main" in viewer.get_text()
        assert str(viewer.text.cget("state")) == "disabled"
        assert viewer.font_family == app.editor.font_family
        # viewer look: tinted background, Rust highlighting, header strip
        assert viewer.viewer_mode is True
        assert viewer.text.cget("background") == theme.LIGHT["viewer_bg"]
        assert viewer.highlighting is True and viewer.highlighted("hl_keyword")  # fn / let …
        from tkinter import ttk as _ttk
        texts = []
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, (_ttk.Label, _ttk.Button)):
                    texts.append(str(c.cget("text")))
                walk(c)
        walk(win)
        assert "Generated Rust" in texts and "Copy all" in texts and "Open folder" in texts
        assert any("read-only" in t for t in texts)
        app._copy_viewer()
        assert "copied to the clipboard" in app.log.get_text()
        win.destroy()


class TestEspflashNonInteractive:
    def _ready_esp32(self, app, fake_toolchain, tmp_path):
        fake_toolchain.status.espflash = Tool("espflash", "/x/espflash", "espflash 4.6.0")
        fake_toolchain.status.installed_targets.append("riscv32imc-unknown-none-elf")
        fake_toolchain.status.installed_components.append("rust-src")
        app.refresh_toolchain()
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        src = _write(tmp_path, "hello.hal", VALID)
        app.open_file(str(src))
        app.apply_compile_settings(IdeCompileSettings(target="esp32-c3", emit_cargo="fw"))
        app.compile()
        _wait(app)
        app.var_port.set("/dev/ttyACM0")

    def test_flash_run_appends_non_interactive(self, app, fake_toolchain, tmp_path, monkeypatch):
        self._ready_esp32(app, fake_toolchain, tmp_path)
        calls = []
        monkeypatch.setattr(app, "run_process", lambda argv, **kw: calls.append((argv, kw)) or True)
        app.flash_run()
        argv, kw = calls[0]
        assert argv[:3] == ["/usr/bin/cargo", "run", "--release"]
        assert argv[3:] == ["--", "--port", "/dev/ttyACM0", "--non-interactive"]
        assert kw["header_suffix"] == "  MET 000:00:00:00"  # pre-launch at first flash

    def test_monitor_appends_non_interactive(self, app, fake_toolchain, tmp_path, monkeypatch):
        self._ready_esp32(app, fake_toolchain, tmp_path)
        calls = []
        monkeypatch.setattr(app, "run_process", lambda argv, **kw: calls.append(argv) or True)
        app.monitor()
        assert calls[0] == ["/x/espflash", "monitor", "--port", "/dev/ttyACM0", "--non-interactive"]

    def test_old_espflash_gets_no_flag(self, app, fake_toolchain, tmp_path, monkeypatch):
        self._ready_esp32(app, fake_toolchain, tmp_path)
        fake_toolchain.status.espflash = Tool("espflash", "/x/espflash", "espflash 2.1.0")
        app.refresh_toolchain()
        pump(app, lambda: not app.probing and app.dispatcher.pending == 0)
        calls = []
        monkeypatch.setattr(app, "run_process", lambda argv, **kw: calls.append(argv) or True)
        app.monitor()
        assert calls[0] == ["/x/espflash", "monitor", "--port", "/dev/ttyACM0"]
        assert app._espflash_extra(None) == ["--non-interactive"]  # unknown version → modern default


class TestFlashHeaderMet:
    def test_header_line_carries_mission_time(self, app):
        import sys as _s
        app.met_launch()
        app.run_process([_s.executable, "-c", "print('x')"], what="Flash & Run (espflash)",
                        header_suffix=f"  {app.met()}")
        _wait(app)
        assert "── Flash & Run (espflash) ──  MET 000:00:00:0" in app.log.get_text()

    def test_plain_headers_are_unchanged(self, app):
        import sys as _s
        app.run_process([_s.executable, "-c", "print('x')"], what="Build")
        _wait(app)
        assert "── Build ──\n" in app.log.get_text() + "\n"
