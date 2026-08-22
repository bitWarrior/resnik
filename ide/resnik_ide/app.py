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
from resnik_ide.reporter import LogReporter

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

        # Compiler arguments (Phase 2 grows this into a full options dialog).
        self.var_emit_exe = tk.BooleanVar(value=False)
        self.var_target = tk.StringVar(value="host")

        self.dispatcher = Dispatcher(self)
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
        self.log.log("Ready. File → Open… to load a .hal file, or start typing.", "info")
        self._update_title()

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
        m_run.add_separator()
        m_run.add_checkbutton(label="Build executable (--emit-exe)",
                              variable=self.var_emit_exe)
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
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Checkbutton(bar, text="Build executable (--emit-exe)",
                        variable=self.var_emit_exe).pack(side="left")
        ttk.Label(bar, text="Target:").pack(side="left", padx=(16, 4))
        ttk.Label(bar, textvariable=self.var_target).pack(side="left")
        self.busy_label = ttk.Label(bar, text="", foreground="#5a32a3")
        self.busy_label.pack(side="right")

    def _build_panes(self) -> None:
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(side="top", fill="both", expand=True)
        self.editor = Editor(paned, on_dirty=self._on_dirty, on_cursor=self._on_cursor)
        self.log = LogView(paned)
        paned.add(self.editor, weight=3)
        paned.add(self.log, weight=1)
        self._paned = paned

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

    # -------------------------------------------------------------- state --

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.alive

    def _set_busy(self, on: bool, what: str = "") -> None:
        state = "disabled" if on else "normal"
        self.btn_check.configure(state=state)
        self.btn_compile.configure(state=state)
        self.busy_label.configure(text=(f"⏳ {what}…" if on else ""))
        self.configure(cursor="watch" if on else "")

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

    def current_options(self, source: Path) -> CompileOptions:
        """Compiler arguments for a Compile run (Phase 2: full dialog)."""
        return CompileOptions(
            input_file=source,
            emit_exe=bool(self.var_emit_exe.get()),
            target=self.var_target.get() or "host",
        )

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
        opts = self.current_options(source)
        self.editor.clear_error_marks()
        self.log.log(f"── Compile: {source.name} ──", "cmd")
        self.log.log(self._describe(opts), "cmd")
        reporter = LogReporter(self.log_sink, on_diagnostic=self._jump_to)
        self._start_job(
            "Compile",
            lambda: generate(opts, reporter),
            lambda res: self._compile_done(res, reporter),
        )

    @staticmethod
    def _describe(opts: CompileOptions) -> str:
        parts = ["resnik compile", str(opts.input_file)]
        if opts.target != "host":
            parts += ["--target", opts.target]
        if opts.emit_exe:
            parts.append("--emit-exe")
        return "$ " + " ".join(parts)

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
                "Job running", "A compile is still running. Quit anyway?", parent=self
            ):
                return
        if not self._confirm_discard():
            return
        self.shutdown()

    def shutdown(self) -> None:
        """Tear down without prompting (tests and on_close)."""
        self.dispatcher.stop()
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
