"""
Reusable compilation driver.

The pipeline that ``resnik compile`` runs (lex → parse → codegen → templates →
Cargo scaffold / rustc) lives here so that any front end — the Click CLI, the
Tk IDE, a test — can run it in-process and receive *structured* results
instead of scraping terminal output.

Design:

- :func:`check` runs lexer + parser only.  HAL/S semantic checks (symbol
  table, dimension / arity checks, EXIT-outside-loop, …) are performed by the
  parser, so this is the "Semantic Check" entry point.
- :func:`generate` runs the whole ``resnik compile`` pipeline for a
  :class:`CompileOptions`.
- Progress and messages go through a :class:`Reporter`.  The driver decides
  *what* is said; the reporter decides *how* it is shown (Rich panel, Tk log
  window, nothing).  Front ends subclass it.
- Errors are returned, not raised: every phase failure is recorded as
  :class:`Diagnostic` objects on the result and ``ok`` is ``False``.  Only
  genuinely unexpected exceptions propagate.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ContextManager, List, Optional, Sequence

from resnik.backend.target_spec import TARGET_NAMES, TARGET_TRIPLES
from resnik.exceptions import ResnikError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from resnik.backend.codegen import CargoProjectResult, CompileResult
    from resnik.lexer import Lexer, Token
    from resnik.parser import ASTNode, Parser


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class Reporter:
    """Receives progress and messages from the driver.

    The default implementation is silent.  Override the methods you care
    about.  ``phase`` returns a context manager that brackets one pipeline
    phase (the CLI shows a spinner; a GUI may log "Phase 1 …").
    """

    def phase(self, title: str) -> ContextManager[Any]:
        return nullcontext()

    def success(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass

    def diagnostic(self, diag: "Diagnostic") -> None:
        """One lexer / parser / semantic diagnostic (after ``error``)."""
        pass

    def raw(self, text: str) -> None:
        """Verbatim tool output (rustc / cargo stderr)."""
        pass


class NullReporter(Reporter):
    """Explicit name for the silent reporter."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Diagnostic:
    """One front-end diagnostic with a source location.

    ``text`` is the exact string the CLI has always printed for the
    underlying :class:`ResnikError` (``str(error)``), so front ends that want
    the legacy rendering can use it verbatim; ``line`` / ``column`` /
    ``filename`` are there for front ends that want to jump to the location.
    """

    phase: str  # "lexer" | "parser" | "codegen" | "build"
    message: str
    line: int = 0
    column: int = 0
    filename: str = ""
    severity: str = "error"
    text: str = ""

    @classmethod
    def from_error(cls, err: ResnikError, phase: str) -> "Diagnostic":
        return cls(
            phase=phase,
            message=err.message,
            line=err.line,
            column=err.column,
            filename=err.filename,
            text=str(err),
        )

    def __post_init__(self) -> None:
        if not self.text:
            self.text = self.message

    def __str__(self) -> str:
        return self.text


@dataclass
class CheckResult:
    """Outcome of :func:`check` (lexer + parser)."""

    ok: bool
    diagnostics: List[Diagnostic] = field(default_factory=list)
    tokens: List["Token"] = field(default_factory=list)
    ast: Optional["ASTNode"] = None
    lexer: Optional["Lexer"] = None
    parser: Optional["Parser"] = None

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]


@dataclass
class CompileOptions:
    """Every knob of ``resnik compile``, as data.

    Field names mirror the Click option names so the CLI can build this
    object directly from its parameters.
    """

    input_file: Path
    tokens: Optional[Path] = None
    ast: Optional[Path] = None
    rust: Optional[Path] = None
    emit_exe: bool = False
    opt_level: str = "2"
    link_search: List[Path] = field(default_factory=list)
    link_lib: List[str] = field(default_factory=list)
    link_obj: List[Path] = field(default_factory=list)
    emit_cargo: Optional[Path] = None
    build: bool = False
    target: str = "host"
    gen_tpl: Optional[Path] = None
    templib: List[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.input_file = Path(self.input_file)
        if self.target not in TARGET_NAMES:
            raise ValueError(
                f"Unknown target '{self.target}'. Expected one of {TARGET_NAMES}."
            )

    @property
    def rust_path(self) -> Path:
        """Where the generated Rust goes (``--rust`` or ``<input>.rs``)."""
        return Path(self.rust) if self.rust else self.input_file.with_suffix(".rs")

    @property
    def cross_triple(self) -> Optional[str]:
        """Target triple implied by the profile (``None`` = host)."""
        return TARGET_TRIPLES.get(self.target)

    # Path-valued fields, in one place so tools can resolve / display them.
    _PATH_FIELDS = ("tokens", "ast", "rust", "emit_cargo", "gen_tpl")
    _PATH_LIST_FIELDS = ("link_search", "link_obj", "templib")

    def to_cli_args(self) -> List[str]:
        """Arguments for ``resnik compile`` that reproduce these options.

        The inverse of :func:`resnik.cli.parse_compile_args`; defaults are
        omitted so the command stays short.  Does not include the leading
        ``resnik compile`` words.
        """
        args: List[str] = [str(self.input_file)]
        if self.tokens:
            args += ["--tokens", str(self.tokens)]
        if self.ast:
            args += ["--ast", str(self.ast)]
        if self.rust:
            args += ["--rust", str(self.rust)]
        if self.emit_exe:
            args.append("--emit-exe")
        if self.opt_level != "2":
            args += ["--opt-level", str(self.opt_level)]
        for d in self.link_search:
            args += ["--link-search", str(d)]
        for lib in self.link_lib:
            args += ["--link-lib", str(lib)]
        for obj in self.link_obj:
            args += ["--link-obj", str(obj)]
        if self.emit_cargo:
            args += ["--emit-cargo", str(self.emit_cargo)]
        if self.build:
            args.append("--build")
        if self.target != "host":
            args += ["--target", self.target]
        if self.gen_tpl:
            args += ["--gen-tpl", str(self.gen_tpl)]
        for t in self.templib:
            args += ["--templib", str(t)]
        return args

    def command_line(self) -> str:
        """Shell-quoted ``resnik compile …`` line for display / copy-paste."""
        import shlex

        return "resnik compile " + " ".join(shlex.quote(a) for a in self.to_cli_args())

    def resolved_relative_to(self, base: Path) -> "CompileOptions":
        """Copy with every *relative* path field anchored at ``base``.

        The CLI interprets relative paths against the shell's cwd; a GUI has
        no meaningful cwd, so it anchors them at the source file's directory.
        """
        from dataclasses import replace

        base = Path(base)

        def fix(p):
            p = Path(p)
            return p if p.is_absolute() else base / p

        kwargs = {}
        for name in self._PATH_FIELDS:
            val = getattr(self, name)
            if val:
                kwargs[name] = fix(val)
        for name in self._PATH_LIST_FIELDS:
            kwargs[name] = [fix(v) for v in getattr(self, name)]
        return replace(self, **kwargs)


@dataclass
class GenerateResult:
    """Outcome of :func:`generate`."""

    ok: bool
    check: CheckResult
    diagnostics: List[Diagnostic] = field(default_factory=list)
    rust_path: Optional[Path] = None
    rust_code: Optional[str] = None
    templates: List[str] = field(default_factory=list)
    cargo: Optional["CargoProjectResult"] = None
    exe: Optional["CompileResult"] = None

    @property
    def executable(self) -> Optional[str]:
        if self.exe is not None and self.exe.executable:
            return self.exe.executable
        if self.cargo is not None and self.cargo.executable:
            return self.cargo.executable
        return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def check(
    input_file: Path,
    templib: Sequence[Path] = (),
    reporter: Optional[Reporter] = None,
    *,
    tokens_out: Optional[Path] = None,
    ast_out: Optional[Path] = None,
) -> CheckResult:
    """Lex and parse ``input_file``; report every front-end diagnostic.

    This is the "Semantic Check": HAL/S semantic analysis is part of the
    parser, so a clean parse means a semantically valid unit.  No Rust is
    generated.
    """
    from resnik.lexer import Lexer
    from resnik.parser import Parser

    rep = reporter or NullReporter()
    result = CheckResult(ok=False)

    # Phase 1: Lexer
    with rep.phase("Phase 1: Lexical Analysis"):
        lexer = Lexer()
        lexer.template_dirs = [str(d) for d in templib]
        token_list = lexer.tokenize_file(str(input_file))
    result.lexer = lexer
    result.tokens = token_list

    if lexer.errors:
        result.diagnostics = [Diagnostic.from_error(e, "lexer") for e in lexer.errors]
        rep.error(f"Lexer found {len(lexer.errors)} error(s)")
        for diag in result.diagnostics:
            rep.diagnostic(diag)
        return result

    rep.success(f"Tokenized {len(token_list)} tokens")

    if tokens_out:
        lexer.export_json(str(tokens_out))
        rep.info(f"Tokens exported to {tokens_out}")

    # Phase 2: Parser (syntax + semantic checks)
    with rep.phase("Phase 2: Syntactic Analysis"):
        parser = Parser()
        ast_result = parser.parse(token_list)
    result.parser = parser
    result.ast = ast_result

    if parser.errors:
        result.diagnostics = [
            Diagnostic.from_error(e, "parser") for e in parser.errors
        ]
        rep.error(f"Parser found {len(parser.errors)} error(s)")
        for diag in result.diagnostics:
            rep.diagnostic(diag)
        return result

    if ast_result is None:
        result.diagnostics = [Diagnostic("parser", "Failed to generate AST")]
        rep.error("Failed to generate AST")
        return result

    rep.success("Generated Abstract Syntax Tree")
    if ast_out:
        parser.export_ast_json(str(ast_out))
        rep.info(f"AST exported to {ast_out}")

    result.ok = True
    return result


def generate(
    opts: CompileOptions,
    reporter: Optional[Reporter] = None,
) -> GenerateResult:
    """Run the full ``resnik compile`` pipeline described by ``opts``.

    Phases: check (lex + parse) → Rust codegen → ``--gen-tpl`` templates →
    ``--emit-cargo`` scaffold (+ ``--build``) → ``--emit-exe`` rustc.  The
    first failing phase stops the pipeline; ``result.ok`` tells the caller.
    ``rustc`` / ``cargo`` are invoked via the backend helpers (blocking).
    """
    from resnik.backend.codegen import RustCodeGenerator

    rep = reporter or NullReporter()

    if opts.target != "host":
        rep.info(f"Target profile: {opts.target} (no_std bare-metal)")

    chk = check(
        opts.input_file,
        opts.templib,
        rep,
        tokens_out=opts.tokens,
        ast_out=opts.ast,
    )
    result = GenerateResult(ok=False, check=chk, diagnostics=list(chk.diagnostics))
    if not chk.ok:
        return result

    # Phase 3: Code Generation
    with rep.phase("Phase 3: Code Generation"):
        generator = RustCodeGenerator(target=opts.target)
        rust_code = generator.generate(chk.ast)
        rust_path = opts.rust_path
        generator.write_to_file(str(rust_path), rust_code)
    result.rust_code = rust_code
    result.rust_path = rust_path
    rep.success(f"Generated Rust source: {rust_path}")

    # Template library (TEMPLIB) generation.
    if opts.gen_tpl:
        from resnik.templib import write_templates

        written = write_templates(chk.ast, opts.gen_tpl)
        result.templates = list(written)
        if written:
            rep.success(f"Generated {len(written)} template(s) into {opts.gen_tpl}:")
            for path in written:
                rep.info(f"  {path}")
        else:
            rep.info("No reusable units (PROCEDURE/FUNCTION/COMPOOL) to template.")

    cross_triple = opts.cross_triple

    # Phase 4: Cargo project (optional)
    if opts.emit_cargo:
        with rep.phase("Phase 4: Cargo Project"):
            try:
                cargo_result = generator.emit_cargo_project(
                    rust_code,
                    str(opts.emit_cargo),
                    opt_level=opts.opt_level,
                    build=opts.build,
                    target=cross_triple,
                )
            except RuntimeError as exc:
                result.diagnostics.append(Diagnostic("build", str(exc)))
                rep.error(str(exc))
                return result
        result.cargo = cargo_result
        rep.success(f"Scaffolded Cargo project: {cargo_result.project_dir}")
        rep.info(f"Cargo.toml:  {cargo_result.cargo_toml}")
        rep.info(f"src/main.rs: {cargo_result.main_rs}")
        if cargo_result.built:
            if cargo_result.success:
                rep.success(f"Built executable: {cargo_result.executable}")
            else:
                result.diagnostics.append(Diagnostic("build", "cargo build failed"))
                rep.error("cargo build failed")
                if cargo_result.stderr:
                    rep.raw(cargo_result.stderr)
                return result

    # Phase 4: Native compilation (optional)
    if opts.emit_exe:
        if opts.target != "host":
            rep.info(
                "--emit-exe builds a single file with rustc; for a "
                "bare-metal target prefer --emit-cargo (it scaffolds the "
                "runtime, linker script, and target config)."
            )
        with rep.phase("Phase 4: Native Compilation"):
            try:
                exe_result = generator.compile_to_executable(
                    rust_code,
                    str(rust_path.with_suffix("")),
                    opt_level=opts.opt_level,
                    target=cross_triple,
                    link_search=[str(d) for d in opts.link_search],
                    link_libs=list(opts.link_lib),
                    link_objs=[str(o) for o in opts.link_obj],
                )
            except RuntimeError as exc:
                result.diagnostics.append(Diagnostic("build", str(exc)))
                rep.error(str(exc))
                return result
        result.exe = exe_result
        if exe_result.success:
            if exe_result.library:
                rep.success(
                    "No PROGRAM entry point (TEMPLATE/prototype unit); "
                    "generated Rust validated as a library — no executable "
                    "produced."
                )
            else:
                rep.success(f"Built executable: {exe_result.executable}")
        else:
            result.diagnostics.append(
                Diagnostic("build", "rustc failed to compile the generated code")
            )
            rep.error("rustc failed to compile the generated code")
            if exe_result.stderr:
                rep.raw(exe_result.stderr)
            return result

    result.ok = True
    return result
