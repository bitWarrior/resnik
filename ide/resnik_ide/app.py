"""
Resnik IDE main window.

Layout::

    ┌ menu ───────────────────────────────────────────────┐
    │ [Semantic Check] [Compile]  ☐ Build executable       │  toolbar
    ├─────────────────────────────────────────────────────┤
    │ editor (line numbers + text)                         │
    ├─────────────────────────────────────────────────────┤
    │ log                                                  │
    ├─────────────────────────────────────────────────────┤
    │ file  *              Ln 1, Col 1     status   target │  status bar
    └─────────────────────────────────────────────────────┘

Compiler work runs on a worker thread through ``resnik.driver``; all widget
updates go through the :class:`Dispatcher`.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Optional

from resnik import __version__ as compiler_version
from resnik.backend.target_spec import TARGET_NAMES
from resnik.driver import (
    CheckResult,
    CompileOptions,
    Diagnostic,
    GenerateResult,
    check,
    generate,
)

from resnik_ide import __version__
from resnik_ide.dispatch import Dispatcher, Job
from resnik_ide.editor import Editor
from resnik_ide.logview import LogSink, LogView
from resnik_ide.options import IdeCompileSettings, OptionsError
from resnik_ide import devices, toolchain
from resnik_ide.options_dialog import OptionsDialog
from resnik_ide.reporter import LogReporter
from resnik_ide.runner import ProcessResult, ProcessRunner
from resnik_ide.settings import load_settings, save_settings, settings_path
from resnik_ide.target_panel import TargetPanel
from resnik_ide.toolchain import Requirement, ToolchainStatus

HAL_FILETYPES = [("HAL/S source", "*.hal *.hs"), ("All files", "*")]
UNTITLED = "Untitled"


class ResnikIDE(tk.Tk):
    """The IDE window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"Resnik IDE — {UNTITLED}")
        self.geometry("1100x780")
        self.minsize(700, 480)

        self.current_path: Optional[Path] = None
        self._job: Optional[Job] = None
        self._tmpdir: Optional[str] = None
        self.last_result: Any = None
        self.last_rust_path: Optional[Path] = None

        # Persistent settings (compiler arguments, window geometry).
        self.settings, settings_warning = load_settings()
        self.var_target = tk.StringVar(value=self.settings.compile.target)
        self.var_port = tk.StringVar(value=self.settings.port)
        self.var_chip = tk.StringVar(value=self.settings.chip)
        self._syncing = False  # guard: toolbar vars <-> settings

        self.dispatcher = Dispatcher(self)
        self.runner = ProcessRunner(self.dispatcher)
        self.toolchain_status: Optional[ToolchainStatus] = None
        self._probe_job: Optional[Job] = None
        self._build_menu()
        self._build_toolbar()
        self._build_panes()
        self._build_statusbar()
        self._bind_keys()

        self.log_sink = LogSink(self.log, self.dispatcher)
        self.dispatcher.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.log.log(
            f"Resnik IDE {__version__} — compiler {compiler_version} — "
            f"Python {sys.version.split()[0]}",
            "info",
        )
        if settings_warning:
            self.log.log(settings_warning, "warn")
        self.log.log(f"Settings: {settings_path()}", "info")
        if self.settings.geometry:
            try:
                self.geometry(self.settings.geometry)
            except tk.TclError:
                pass
        self.var_target.trace_add("write", lambda *_a: self._toolbar_changed())
        self.var_port.trace_add("write", lambda *_a: self._device_changed())
        self.var_chip.trace_add("write", lambda *_a: self._device_changed())
        self.log.log("Ready. File → Open… to load a .hal file, or start typing.", "info")
        self._update_title()
        self.refresh_devices()
        self.refresh_toolchain()

    # ------------------------------------------------------------------ UI --

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label="New", accelerator="Ctrl+N", command=self.new_file)
        m_file.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_file)
        m_file.add_command(label="Save", accelerator="Ctrl+S", command=self.save_file)
        m_file.add_command(label="Save As…", accelerator="Ctrl+Shift+S",
                           command=self.save_file_as)
        m_file.add_separator()
        m_file.add_command(label="Exit", accelerator="Ctrl+Q", command=self.on_close)
        menubar.add_cascade(label="File", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=False)
        for item in (
            ("Undo", "Ctrl+Z", "<<Undo>>"),
            ("Redo", "Ctrl+Y", "<<Redo>>"),
            None,
            ("Cut", "Ctrl+X", "<<Cut>>"),
            ("Copy", "Ctrl+C", "<<Copy>>"),
            ("Paste", "Ctrl+V", "<<Paste>>"),
            None,
            ("Select All", "Ctrl+A", "<<SelectAll>>"),
        ):
            if item is None:
                m_edit.add_separator()
            else:
                label, accel, ev = item
                m_edit.add_command(
                    label=label, accelerator=accel,
                    command=lambda e=ev: self.editor.text.event_generate(e))
        menubar.add_cascade(label="Edit", menu=m_edit)

        m_run = tk.Menu(menubar, tearoff=False)
        m_run.add_command(label="Semantic Check", accelerator="F7",
                          command=self.semantic_check)
        m_run.add_command(label="Compile", accelerator="F5", command=self.compile)
        m_run.add_command(label="Build and Run", accelerator="F9", command=self.build_and_run)
        m_run.add_separator()
        m_run.add_command(label="Compiler Options…", accelerator="Ctrl+,",
                          command=self.open_options)
        m_run.add_separator()
        m_run.add_command(label="Build (cargo build)", accelerator="F6", command=self.build)
        m_run.add_command(label="Flash & Run", accelerator="F8", command=self.flash_run)
        m_run.add_command(label="Monitor (serial console)", command=self.monitor)
        m_run.add_command(label="Run on host", command=self.run_host)
        m_run.add_command(label="Stop", accelerator="Esc", command=self.stop)
        m_run.add_separator()
        m_run.add_command(label="View Generated Rust", command=self.view_rust)
        menubar.add_cascade(label="Run", menu=m_run)
        self._menu_run = m_run

        m_log = tk.Menu(menubar, tearoff=False)
        m_log.add_command(label="Clear Log", command=lambda: self.log.clear())
        m_log.add_command(label="Save Log…", command=lambda: self.log.save_dialog())
        menubar.add_cascade(label="Log", menu=m_log)

        m_help = tk.Menu(menubar, tearoff=False)
        m_help.add_command(label="About", command=self.about)
        menubar.add_cascade(label="Help", menu=m_help)

        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(side="top", fill="x")
        self.btn_check = ttk.Button(bar, text="Semantic Check", command=self.semantic_check)
        self.btn_check.pack(side="left")
        self.btn_compile = ttk.Button(bar, text="Compile", command=self.compile)
        self.btn_compile.pack(side="left", padx=(6, 0))
        self.btn_build_run = ttk.Button(bar, text="Build and Run", command=self.build_and_run)
        self.btn_build_run.pack(side="left", padx=(6, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(bar, text="Target:").pack(side="left", padx=(0, 4))
        self.cb_target = ttk.Combobox(bar, textvariable=self.var_target,
                                      values=list(TARGET_NAMES), state="readonly", width=10)
        self.cb_target.pack(side="left")
        self.btn_options = ttk.Button(bar, text="Options…", command=self.open_options)
        self.btn_options.pack(side="left", padx=(10, 0))
        self.busy_label = ttk.Label(bar, text="", foreground="#5a32a3")
        self.busy_label.pack(side="right")

    def _build_panes(self) -> None:
        outer = ttk.PanedWindow(self, orient="horizontal")
        outer.pack(side="top", fill="both", expand=True)
        paned = ttk.PanedWindow(outer, orient="vertical")
        self.editor = Editor(paned, on_dirty=self._on_dirty, on_cursor=self._on_cursor)
        self.log = LogView(paned)
        paned.add(self.editor, weight=3)
        paned.add(self.log, weight=1)
        self.target_panel = TargetPanel(
            outer, var_target=self.var_target, var_port=self.var_port, var_chip=self.var_chip,
            actions={
                "refresh_toolchain": self.refresh_toolchain,
                "refresh_devices": self.refresh_devices,
                "install": self.install_requirement,
                "build": self.build,
                "flash_run": self.flash_run,
                "monitor": self.monitor,
                "run": self.run_host,
                "stop": self.stop,
            })
        outer.add(paned, weight=4)
        outer.add(self.target_panel, weight=0)
        self._paned = paned
        self._outer = outer

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, relief="sunken", padding=(6, 2))
        bar.pack(side="bottom", fill="x")
        self.status_file = ttk.Label(bar, text=UNTITLED, anchor="w")
        self.status_file.pack(side="left", fill="x", expand=True)
        self.status_pos = ttk.Label(bar, text="Ln 1, Col 1", width=16, anchor="center")
        self.status_pos.pack(side="left")
        self.status_msg = ttk.Label(bar, text="", width=36, anchor="e")
        self.status_msg.pack(side="right")

    def _bind_keys(self) -> None:
        self.bind_all("<Control-n>", lambda e: self.new_file())
        self.bind_all("<Control-o>", lambda e: self.open_file())
        self.bind_all("<Control-s>", lambda e: self.save_file())
        self.bind_all("<Control-S>", lambda e: self.save_file_as())
        self.bind_all("<Control-q>", lambda e: self.on_close())
        self.bind_all("<F7>", lambda e: self.semantic_check())
        self.bind_all("<F5>", lambda e: self.compile())
        self.bind_all("<Control-comma>", lambda e: self.open_options())
        self.bind_all("<F6>", lambda e: self.build())
        self.bind_all("<F8>", lambda e: self.flash_run())
        self.bind_all("<F9>", lambda e: self.build_and_run())
        self.bind_all("<Escape>", lambda e: self.stop())

    # -------------------------------------------------------------- state --

    @property
    def busy(self) -> bool:
        return (self._job is not None and self._job.alive) or self.runner.running

    def _set_busy(self, on: bool, what: str = "") -> None:
        state = "disabled" if on else "normal"
        self.btn_check.configure(state=state)
        self.btn_compile.configure(state=state)
        self.btn_build_run.configure(state=state)
        self.btn_options.configure(state=state)
        self.busy_label.configure(text=(f"⏳ {what}…" if on else ""))
        self.configure(cursor="watch" if on else "")
        self.target_panel.set_running(on)

    def _update_title(self) -> None:
        name = self.current_path.name if self.current_path else UNTITLED
        star = "*" if self.editor.dirty else ""
        self.title(f"Resnik IDE — {name}{star}")
        path = str(self.current_path) if self.current_path else UNTITLED
        self.status_file.configure(text=f"{path}{star}")

    def _on_dirty(self, _dirty: bool) -> None:
        self._update_title()

    def _on_cursor(self, line: int, col: int) -> None:
        self.status_pos.configure(text=f"Ln {line}, Col {col}")

    def set_status(self, text: str) -> None:
        self.status_msg.configure(text=text)

    # --------------------------------------------------------------- file --

    def _confirm_discard(self) -> bool:
        """Return True if it is OK to drop the current buffer."""
        if not self.editor.dirty:
            return True
        name = self.current_path.name if self.current_path else UNTITLED
        ans = messagebox.askyesnocancel(
            "Unsaved changes", f"Save changes to {name}?", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_file()
        return True

    def new_file(self) -> None:
        if not self._confirm_discard():
            return
        self.current_path = None
        self.editor.set_text("")
        self.last_rust_path = None
        self.log.log("New file.", "info")
        self._update_title()

    def open_file(self, path: Optional[str] = None) -> bool:
        if not self._confirm_discard():
            return False
        if path is None:
            path = filedialog.askopenfilename(title="Open HAL/S source",
                                              filetypes=HAL_FILETYPES, parent=self)
            if not path:
                return False
        try:
            self.editor.load(path)
        except OSError as exc:
            self.log.log(f"Could not open {path}: {exc}", "error")
            messagebox.showerror("Open failed", str(exc), parent=self)
            return False
        self.current_path = Path(path).resolve()
        self.last_rust_path = None
        self.log.log(f"Opened {self.current_path}", "info")
        self._update_title()
        self.set_status("")
        return True

    def save_file(self) -> bool:
        if self.current_path is None:
            return self.save_file_as()
        return self._save_to(self.current_path)

    def save_file_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            title="Save HAL/S source", defaultextension=".hal",
            filetypes=HAL_FILETYPES, parent=self)
        if not path:
            return False
        self.current_path = Path(path).resolve()
        return self._save_to(self.current_path)

    def _save_to(self, path: Path) -> bool:
        try:
            self.editor.save(str(path))
        except OSError as exc:
            self.log.log(f"Could not save {path}: {exc}", "error")
            messagebox.showerror("Save failed", str(exc), parent=self)
            return False
        self.log.log(f"Saved {path}", "info")
        self._update_title()
        return True

    def _source_for_run(self) -> Optional[Path]:
        """Make sure the buffer is on disk; return the path to compile.

        A named file is saved in place if dirty.  An untitled buffer is
        written to a private temp directory as ``untitled.hal``.
        """
        if self.current_path is not None:
            if self.editor.needs_save and not self._save_to(self.current_path):
                return None
            return self.current_path
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="resnik-ide-")
        tmp = Path(self._tmpdir) / "untitled.hal"
        try:
            self.editor.save(str(tmp))
            self.editor.set_dirty(True)  # untitled stays "unsaved" for the user
        except OSError as exc:
            self.log.log(f"Could not write scratch file: {exc}", "error")
            return None
        return tmp

    # ---------------------------------------------------------------- run --

    # ------------------------------------------------------------ options --

    def current_options(self, source: Path) -> CompileOptions:
        """Compiler arguments for a Compile run, validated by the real CLI
        parser.  Raises :class:`OptionsError` on bad settings."""
        return self.settings.compile.to_compile_options(source)

    def _toolbar_changed(self) -> None:
        """Toolbar emit-exe / target → settings (+ save)."""
        if self._syncing:
            return
        comp = self.settings.compile
        changed = False
        tgt = self.var_target.get()
        if tgt in TARGET_NAMES and tgt != comp.target:
            comp.target = tgt
            changed = True
        if changed:
            self._save_settings()
            self.log.log("Options: " + comp.command_line(
                self.current_path.name if self.current_path else None), "cmd")
        if self.toolchain_status is not None:
            self._show_requirements()

    def _device_changed(self) -> None:
        if self._syncing:
            return
        port, chip = self.var_port.get().strip(), self.var_chip.get().strip()
        if port != self.settings.port or chip != self.settings.chip:
            self.settings.port, self.settings.chip = port, chip
            self._save_settings()

    def _sync_toolbar(self) -> None:
        """Settings → toolbar vars (without triggering a save)."""
        self._syncing = True
        try:
            self.var_target.set(self.settings.compile.target)
        finally:
            self._syncing = False

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.log.log(f"Could not save settings: {exc}", "warn")

    def apply_compile_settings(self, new: IdeCompileSettings) -> None:
        """Adopt ``new`` compiler settings: persist, sync toolbar, log."""
        self.settings.compile = new
        self._sync_toolbar()
        self._save_settings()
        self.log.log("Options: " + new.command_line(
            self.current_path.name if self.current_path else None), "cmd")

    def open_options(self) -> None:
        if self.busy:
            self.log.log("Options cannot change while a job is running.", "warn")
            return
        # Preview with the bare file name: relative paths are resolved against
        # the source's directory, so that is the exact shell equivalent from there.
        dlg = OptionsDialog(self, self.settings.compile,
                            source=self.current_path.name if self.current_path else None)
        new = dlg.show()
        if new is not None and new != self.settings.compile:
            self.apply_compile_settings(new)

    def semantic_check(self) -> None:
        if self.busy:
            return
        source = self._source_for_run()
        if source is None:
            return
        self.editor.clear_error_marks()
        self.log.log(f"── Semantic Check: {source.name} ──", "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        self._start_job(
            "Semantic Check",
            lambda: check(source, reporter=reporter),
            lambda res: self._check_done(res, reporter),
        )

    def compile(self) -> None:
        if self.busy:
            return
        source = self._source_for_run()
        if source is None:
            return
        try:
            opts = self.current_options(source)
        except OptionsError as exc:
            self.log.log(f"✗ Invalid compiler options: {exc}", "error")
            self.log.log("Fix them under Run → Compiler Options… (Ctrl+,).", "warn")
            self.set_status("Compile: invalid options")
            return
        self.editor.clear_error_marks()
        self.log.log(f"── Compile: {source.name} ──", "cmd")
        self.log.log("$ " + opts.command_line(), "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        self._start_job(
            "Compile",
            lambda: generate(opts, reporter),
            lambda res: self._compile_done(res, reporter),
        )

    def build_and_run(self) -> None:
        """Compile, then run the result and stream its output into the log.

        host: ``--emit-exe`` is forced for this run (the saved options are
        not changed) and the executable is run through the process runner.
        Embedded targets: compile (``--emit-cargo`` must be set), then the
        Flash & Run flow for the target.
        """
        if self.busy:
            return
        source = self._source_for_run()
        if source is None:
            return
        try:
            opts = self.current_options(source)
        except OptionsError as exc:
            self.log.log(f"✗ Invalid compiler options: {exc}", "error")
            self.set_status("Build and Run: invalid options")
            return
        if opts.target == "host":
            from dataclasses import replace
            opts = replace(opts, emit_exe=True)
        elif not opts.emit_cargo:
            self.log.log("✗ Build and Run on an embedded target needs a Cargo project: set "
                         "'Cargo project dir (--emit-cargo)' in Options….", "error")
            return
        self.editor.clear_error_marks()
        self.log.log(f"── Build and Run: {source.name} ──", "cmd")
        self.log.log("$ " + opts.command_line(), "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        self._start_job(
            "Build and Run",
            lambda: generate(opts, reporter),
            lambda res: self._build_and_run_done(res, reporter),
        )

    def _build_and_run_done(self, res: GenerateResult, reporter: LogReporter) -> None:
        self._compile_done(res, reporter)
        if not res.ok:
            self.set_status("Build and Run: compile failed")
            return
        if (res.check and res.check.ast is not None and
                self.var_target.get() in ("", "host")):
            if res.executable is None:
                self.log.log("No executable produced (no PROGRAM entry point) — nothing to run.",
                             "warn")
                return
            exe = Path(res.executable)
            self.run_process([str(exe)], cwd=exe.parent, what=f"Run {exe.name}")
        else:
            self.flash_run()

    def _start_job(self, what: str, fn: Callable[[], Any],
                   on_done: Callable[[Any], None]) -> None:
        self._set_busy(True, what)
        self.set_status(f"{what} running…")
        sink = self.log_sink

        def run() -> Any:
            # Anything the compiler prints lands in the log, not the console.
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                try:
                    return fn()
                finally:
                    sink.flush()

        def done(result: Any) -> None:
            self._set_busy(False)
            on_done(result)

        def failed(exc: BaseException) -> None:
            self._set_busy(False)
            self.log.log(f"✗ {what} crashed: {type(exc).__name__}: {exc}", "error")
            import traceback
            for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
                for sub in line.rstrip("\n").splitlines():
                    self.log.log(sub, "raw", timestamp=False)
            self.set_status(f"{what}: internal error")

        self._job = Job(self.dispatcher, run, done, failed, name=what).start()

    def _apply_marks(self, diagnostics) -> None:
        lines = [d.line for d in diagnostics if d.line > 0 and self._is_current(d)]
        self.editor.mark_error_lines(lines)

    def _is_current(self, diag: Diagnostic) -> bool:
        """Does this diagnostic refer to the buffer in the editor?"""
        if not diag.filename:
            return True
        try:
            return Path(diag.filename).resolve() == self._run_path()
        except OSError:
            return False

    def _run_path(self) -> Optional[Path]:
        if self.current_path is not None:
            return self.current_path
        if self._tmpdir:
            return (Path(self._tmpdir) / "untitled.hal").resolve()
        return None

    def _jump_to(self, diag: Diagnostic) -> None:
        if not self._is_current(diag):
            self.log.log(f"(diagnostic is in {diag.filename}, not the open file)", "warn")
            return
        self.editor.goto(diag.line, diag.column)

    def _check_done(self, res: CheckResult, reporter: LogReporter) -> None:
        self.last_result = res
        self._apply_marks(res.diagnostics)
        n = len(res.errors)
        if res.ok:
            self.log.log("✓ Semantic Check passed — no errors.", "ok")
            self.set_status("Semantic Check: OK")
        else:
            self.log.log(f"✗ Semantic Check failed — {n} error(s). "
                         "Click a diagnostic to jump to it.", "error")
            self.set_status(f"Semantic Check: {n} error(s)")

    def _compile_done(self, res: GenerateResult, reporter: LogReporter) -> None:
        self.last_result = res
        self._apply_marks(res.diagnostics)
        if res.rust_path is not None:
            self.last_rust_path = Path(res.rust_path)
            self.log.log(f"View generated Rust: {res.rust_path}", "info",
                         on_click=self.view_rust)
        if res.ok:
            extra = f" → {res.executable}" if res.executable else ""
            self.log.log(f"✓ Compile succeeded{extra}", "ok")
            self.set_status("Compile: OK")
        else:
            n = len(res.diagnostics)
            self.log.log(f"✗ Compile failed — {n} error(s).", "error")
            self.set_status(f"Compile: {n} error(s)")

    # ------------------------------------------------------ toolchain/devices --

    def refresh_toolchain(self) -> None:
        """Probe rustup/cargo/… on a background thread and fill the checklist.

        Read-only, so it does not block the editor or Compile; only the
        panel's Refresh button is disabled while it runs."""
        if self._probe_job is not None and self._probe_job.alive:
            return
        self.target_panel.show_toolchain(None, [])
        self.target_panel.btn_refresh_tc.configure(state="disabled")

        def finish() -> None:
            try:
                self.target_panel.btn_refresh_tc.configure(state="normal")
            except tk.TclError:  # pragma: no cover - window gone
                pass

        def done(status: ToolchainStatus) -> None:
            finish()
            self.toolchain_status = status
            self._show_requirements()
            for ln in status.summary_lines():
                self.log.log("toolchain: " + ln, "info")

        def failed(exc: BaseException) -> None:
            finish()
            self.log.log(f"toolchain probe failed: {exc}", "error")

        self._probe_job = Job(self.dispatcher, toolchain.probe, done, failed,
                              name="toolchain-probe").start()

    @property
    def probing(self) -> bool:
        return self._probe_job is not None and self._probe_job.alive

    def _show_requirements(self) -> None:
        st = self.toolchain_status
        reqs = toolchain.requirements(self.var_target.get() or "host", st) if st else []
        self.target_panel.show_toolchain(st, reqs)
        if st:
            miss = toolchain.missing(reqs)
            self.set_status("Toolchain: OK for " + self.var_target.get()
                            if not miss else f"Toolchain: {len(miss)} missing")

    def refresh_devices(self) -> None:
        ports = devices.list_serial_ports()
        probes = devices.list_probes() if (self.toolchain_status and
                                           self.toolchain_status.probe_rs.present) else []
        self.target_panel.show_ports(ports, probes)
        self.log.log("devices: " + (", ".join(p.device for p in ports) or "no serial ports"), "info")
        for ln in probes[:4]:
            self.log.log("probe-rs: " + ln, "info")

    def install_requirement(self, req: Requirement) -> None:
        if not req.install_argv:
            self.log.log(f"{req.label}: cannot install automatically — {req.detail}", "warn")
            return
        self.run_process(req.install_argv, what=f"Install {req.label}",
                         on_done=lambda _r: self.refresh_toolchain())

    # ------------------------------------------------------------ processes --

    def run_process(self, argv, *, cwd=None, env=None, what: str = "Process",
                    on_done: Optional[Callable[[ProcessResult], None]] = None) -> bool:
        """Stream an external command into the log.  One at a time; Stop cancels."""
        if self.busy:
            self.log.log("Another job is running — Stop it first.", "warn")
            return False
        import shlex
        shown = " ".join(shlex.quote(str(a)) for a in argv)
        self.log.log(f"── {what} ──", "cmd")
        self.log.log(("$ " if not cwd else f"$ (cd {cwd}) ") + shown, "cmd")

        def on_line(line: str) -> None:
            self.log.log(line, "raw", timestamp=False)

        def on_exit(res: ProcessResult) -> None:
            self._set_busy(False)
            if res.error:
                self.log.log(f"✗ {what}: {res.error}", "error")
                self.set_status(f"{what}: failed")
            elif res.cancelled:
                self.log.log(f"■ {what} stopped (exit {res.returncode}, {res.duration:.1f}s)", "warn")
                self.set_status(f"{what}: stopped")
            elif res.ok:
                self.log.log(f"✓ {what} finished (exit 0, {res.duration:.1f}s)", "ok")
                self.set_status(f"{what}: OK")
            else:
                self.log.log(f"✗ {what} failed (exit {res.returncode}, {res.duration:.1f}s)", "error")
                self.set_status(f"{what}: exit {res.returncode}")
            if on_done is not None and res.error is None:
                on_done(res)

        self._set_busy(True, what)
        self.set_status(f"{what} running…")
        started = self.runner.start(argv, cwd=cwd, env=env, on_line=on_line, on_exit=on_exit)
        if not started:
            self._set_busy(False)
        return started

    def stop(self) -> None:
        if self.runner.running:
            self.log.log("Stopping…", "warn")
            self.runner.cancel()

    # -------------------------------------------------------------- actions --

    def _project_dir(self) -> Optional[Path]:
        """The --emit-cargo directory for the current source (None if unset)."""
        comp = self.settings.compile
        if not comp.emit_cargo:
            return None
        base = self.current_path.parent if self.current_path else Path.cwd()
        p = Path(comp.emit_cargo)
        return p if p.is_absolute() else base / p

    def _crate_name(self, proj: Path) -> str:
        from resnik.backend.codegen import RustCodeGenerator
        return RustCodeGenerator._sanitize_crate_name(proj.name)

    def _elf_path(self, proj: Path) -> Path:
        from resnik.backend.target_spec import SPECS
        spec = SPECS.get(self.var_target.get() or "host")
        crate = self._crate_name(proj)
        if spec is not None and spec.triple:
            return proj / "target" / spec.triple / "release" / crate
        return proj / "target" / "release" / crate

    def _need_project(self) -> Optional[Path]:
        proj = self._project_dir()
        if proj is None:
            self.log.log("✗ No Cargo project: set 'Cargo project dir (--emit-cargo)' in "
                         "Options… and Compile first.", "error")
            return None
        if not (proj / "Cargo.toml").exists():
            self.log.log(f"✗ {proj}/Cargo.toml not found — run Compile first (it scaffolds "
                         "the project).", "error")
            return None
        return proj

    def _preflight(self) -> bool:
        """Toolchain requirements for the current target; log what is missing."""
        st = self.toolchain_status
        if st is None:
            self.log.log("Toolchain not probed yet — press Refresh in the Target panel.", "warn")
            return False
        miss = toolchain.missing(toolchain.requirements(self.var_target.get() or "host", st))
        for r in miss:
            hint = " — click Install in the Target panel" if r.installable else f" — {r.detail}"
            self.log.log(f"✗ missing: {r.label}{hint}", "error")
        return not miss

    def build(self) -> None:
        if self.busy:
            return
        proj = self._need_project()
        if proj is None or not self._preflight():
            return
        cargo = self.toolchain_status.cargo.path or "cargo"
        self.run_process([cargo, "build", "--release"], cwd=proj, what="Build")

    def flash_run(self) -> None:
        if self.busy:
            return
        target = self.var_target.get() or "host"
        if target == "host":
            self.run_host()
            return
        proj = self._need_project()
        if proj is None or not self._preflight():
            return
        st = self.toolchain_status
        if target == "esp32-c3":
            argv = [st.cargo.path or "cargo", "run", "--release"]
            port = self.var_port.get().strip()
            if port:
                argv += ["--", "--port", port]
            else:
                self.log.log("No serial port selected — espflash will auto-detect / ask.", "warn")
            self.run_process(argv, cwd=proj, what="Flash & Run (espflash)")
        elif target == "cortex-m":
            chip = self.var_chip.get().strip()
            if not chip:
                self.log.log("✗ Enter the MCU name for probe-rs (e.g. STM32F411CEUx) in the "
                             "Target panel.", "error")
                return
            elf = self._elf_path(proj)
            probe = st.probe_rs.path or "probe-rs"

            def after_build(res: ProcessResult) -> None:
                if res.ok:
                    self.run_process([probe, "run", "--chip", chip, str(elf)], cwd=proj,
                                     what="Flash & Run (probe-rs)")

            self.run_process([st.cargo.path or "cargo", "build", "--release"], cwd=proj,
                             what="Build", on_done=after_build)
        else:
            self.log.log(f"No flash flow for target '{target}'.", "warn")

    def monitor(self) -> None:
        if self.busy:
            return
        target = self.var_target.get() or "host"
        st = self.toolchain_status
        if target == "esp32-c3":
            if st is None or not st.espflash.present:
                self.log.log("✗ espflash not found — install it from the Target panel.", "error")
                return
            argv = [st.espflash.path, "monitor"]
            port = self.var_port.get().strip()
            if port:
                argv += ["--port", port]
            self.run_process(argv, what="Monitor (espflash)")
        elif target == "cortex-m":
            if st is None or not st.probe_rs.present:
                self.log.log("✗ probe-rs not found — install it from the Target panel.", "error")
                return
            proj = self._need_project()
            chip = self.var_chip.get().strip()
            if proj is None or not chip:
                self.log.log("✗ Need a Cargo project and a chip name for probe-rs attach.", "error")
                return
            self.run_process([st.probe_rs.path, "attach", "--chip", chip, str(self._elf_path(proj))],
                             cwd=proj, what="Monitor (probe-rs attach)")
        else:
            self.log.log("Monitor applies to embedded targets; use Run on host instead.", "warn")

    def run_host(self) -> None:
        """Execute the most recent host build and stream its output."""
        if self.busy:
            return
        exe: Optional[Path] = None
        res = self.last_result
        if isinstance(res, GenerateResult) and res.executable:
            exe = Path(res.executable)
        if exe is None or not exe.exists():
            proj = self._project_dir()
            if proj is not None:
                cand = proj / "target" / "release" / self._crate_name(proj)
                if cand.exists():
                    exe = cand
        if exe is None or not exe.exists():
            self.log.log("✗ Nothing to run: Compile with 'Build executable (--emit-exe)' ticked, "
                         "or Build the Cargo project first.", "error")
            return
        self.run_process([str(exe)], cwd=exe.parent, what=f"Run {exe.name}")

    # -------------------------------------------------------------- extras --

    def view_rust(self) -> None:
        if not self.last_rust_path or not self.last_rust_path.exists():
            self.log.log("No generated Rust yet — run Compile first.", "warn")
            return
        win = tk.Toplevel(self)
        win.title(f"Generated Rust — {self.last_rust_path.name}")
        win.geometry("900x650")
        from tkinter.scrolledtext import ScrolledText
        txt = ScrolledText(win, wrap="none", font=self.editor.font)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", self.last_rust_path.read_text(encoding="utf-8"))
        txt.configure(state="disabled")

    def about(self) -> None:
        messagebox.showinfo(
            "About Resnik IDE",
            f"Resnik IDE {__version__}\nResnik HAL/S compiler {compiler_version}\n\n"
            "A minimal editor + log front end over resnik.driver.",
            parent=self,
        )

    def on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                "Job running", "A job is still running. Quit anyway?", parent=self
            ):
                return
            self.runner.cancel()
        if not self._confirm_discard():
            return
        self.shutdown()

    def shutdown(self) -> None:
        """Tear down without prompting (tests and on_close)."""
        try:
            if self.state() == "normal":
                self.settings.geometry = self.geometry()
                self._save_settings()
        except tk.TclError:  # pragma: no cover
            pass
        self.dispatcher.stop()
        if self.runner.running:
            self.runner.cancel()
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        try:
            self.destroy()
        except tk.TclError:  # pragma: no cover - already destroyed
            pass


def main(argv: Optional[list] = None) -> int:
    """Console-script entry point: ``resnik-ide [file.hal]``."""
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        app = ResnikIDE()
    except tk.TclError as exc:
        print(
            "resnik-ide: could not open a display / initialise Tk "
            f"({exc}).\nOn Debian/Ubuntu install the 'python3-tk' package and "
            "make sure DISPLAY is set.",
            file=sys.stderr,
        )
        return 2
    if argv:
        target = argv[0]
        if os.path.exists(target):
            app.open_file(target)
        else:
            app.log.log(f"File not found: {target}", "error")
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
