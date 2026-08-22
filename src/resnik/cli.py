"""
Modern CLI for the Resnik HAL/S compiler.

Provides beautiful terminal output using Rich and intuitive commands using Click.
"""

import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table
from rich.tree import Tree

from resnik import __version__
from resnik.lexer import Lexer
from resnik.parser import Parser
from resnik.backend.target_spec import TARGET_NAMES
from resnik.driver import CompileOptions, Diagnostic, Reporter, generate
from resnik.exceptions import ResnikError

console = Console()


def print_error(message: str) -> None:
    """Print error message in red."""
    console.print(f"[bold red]✗[/bold red] {message}")


def print_success(message: str) -> None:
    """Print success message in green."""
    console.print(f"[bold green]✓[/bold green] {message}")


def print_info(message: str) -> None:
    """Print info message."""
    console.print(f"[blue]ℹ[/blue] {message}")


@click.group()
@click.version_option(version=__version__, prog_name="Resnik")
def main() -> None:
    """
    Resnik HAL/S Compiler - Modern aerospace language compiler.

    Transform HAL/S code from the Space Shuttle era into modern, safe Rust.
    """
    pass


@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o", "--output", type=click.Path(path_type=Path), help="Output JSON file for tokens"
)
@click.option("-v", "--verbose", is_flag=True, help="Show detailed token information")
@click.option("--stats", is_flag=True, help="Show tokenization statistics")
def lex(
    input_file: Path, output: Optional[Path], verbose: bool, stats: bool
) -> None:
    """Tokenize a HAL/S source file."""
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Tokenizing...", total=None)

            lexer = Lexer()
            tokens = lexer.tokenize_file(str(input_file))

            progress.update(task, completed=True)

        if lexer.errors:
            print_error(f"Found {len(lexer.errors)} lexical error(s)")
            for error in lexer.errors:
                console.print(f"  [red]{error}[/red]")
            sys.exit(1)

        print_success(f"Tokenized {len(tokens)} tokens from {input_file.name}")

        if verbose:
            table = Table(title="Tokens", show_header=True, header_style="bold magenta")
            table.add_column("Type", style="cyan")
            table.add_column("Lexeme", style="green")
            table.add_column("Line:Col", style="yellow")

            for token in tokens[:50]:  # Show first 50 tokens
                table.add_row(
                    token.type, token.lexeme, f"{token.line}:{token.column}"
                )

            if len(tokens) > 50:
                table.add_row("...", "...", "...", style="dim")

            console.print(table)

        if stats:
            stats_table = Table(title="Statistics", show_header=False)
            stats_table.add_column("Metric", style="bold")
            stats_table.add_column("Value", style="cyan")

            stats_table.add_row("Total Tokens", str(len(tokens)))
            stats_table.add_row(
                "Lines Processed", str(max((t.line for t in tokens), default=0))
            )

            # Count token types
            from collections import Counter

            type_counts = Counter(t.type for t in tokens)
            stats_table.add_row("Unique Token Types", str(len(type_counts)))

            console.print(stats_table)

        if output:
            lexer.export_json(str(output))
            print_success(f"Tokens exported to {output}")

    except ResnikError as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)


@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o", "--output", type=click.Path(path_type=Path), help="Output JSON file for AST"
)
@click.option("-v", "--verbose", is_flag=True, help="Show AST structure")
@click.option("--tree", is_flag=True, help="Display AST as a tree")
def parse(
    input_file: Path, output: Optional[Path], verbose: bool, tree: bool
) -> None:
    """Parse a HAL/S source file to generate an AST."""
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Parsing...", total=None)

            parser = Parser()
            ast = parser.parse_file(str(input_file))

            progress.update(task, completed=True)

        if parser.errors:
            print_error(f"Found {len(parser.errors)} parsing error(s)")
            for error in parser.errors:
                console.print(f"  [red]{error}[/red]")
            sys.exit(1)

        if ast:
            print_success(f"Successfully parsed {input_file.name}")

            if verbose:
                console.print(Panel(f"[bold]AST Root:[/bold] {ast.node_type.value}"))
                if hasattr(ast, "name"):
                    console.print(f"  Program Name: [cyan]{ast.name}[/cyan]")
                if hasattr(ast, "declarations"):
                    console.print(
                        f"  Declarations: [yellow]{len(ast.declarations)}[/yellow]"
                    )
                if hasattr(ast, "statements"):
                    console.print(
                        f"  Statements: [yellow]{len(ast.statements)}[/yellow]"
                    )

            if output:
                parser.export_ast_json(str(output))
                print_success(f"AST exported to {output}")
        else:
            print_error("Failed to generate AST")
            sys.exit(1)

    except ResnikError as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option("--tokens", type=click.Path(path_type=Path), help="Export tokens to file")
@click.option("--ast", type=click.Path(path_type=Path), help="Export AST to file")
@click.option(
    "--rust",
    type=click.Path(path_type=Path),
    help="Output Rust file (default: <input>.rs)",
)
@click.option(
    "--emit-exe",
    is_flag=True,
    help="Compile the generated Rust to a native executable (requires rustc)",
)
@click.option(
    "--opt-level",
    default="2",
    show_default=True,
    help="rustc optimization level for --emit-exe (0-3, s, z)",
)
@click.option(
    "-L", "--link-search",
    type=click.Path(path_type=Path),
    multiple=True,
    help="With --emit-exe: add a linker search directory (rustc -L). "
         "Repeatable. For resolving EXTERNAL symbols.",
)
@click.option(
    "-l", "--link-lib",
    multiple=True,
    help="With --emit-exe: link a native library by name (rustc -l). "
         "Repeatable.",
)
@click.option(
    "--link-obj",
    type=click.Path(path_type=Path),
    multiple=True,
    help="With --emit-exe: add a foreign object/archive to the link "
         "(e.g. i2c.o for an EXTERNAL routine). Repeatable.",
)
@click.option(
    "--emit-cargo",
    type=click.Path(path_type=Path),
    help="Scaffold a Cargo project at this directory",
)
@click.option(
    "--build",
    is_flag=True,
    help="With --emit-cargo, run 'cargo build --release' on the project",
)
@click.option(
    "--target",
    type=click.Choice(list(TARGET_NAMES)),
    default="host",
    show_default=True,
    help="Code-generation target profile: 'host' (std), 'cortex-m' (ARM "
         "no_std stub), or 'esp32-c3' (RISC-V no_std via esp-hal). Use with "
         "--emit-cargo for embedded builds. Profiles are defined in "
         "resnik.backend.target_spec.",
)
@click.option(
    "--gen-tpl",
    type=click.Path(path_type=Path),
    default=None,
    help="Auto-generate TEMPLATE prototypes for each reusable unit "
         "(PROCEDURE/FUNCTION/COMPOOL) into this template library directory "
         "(TEMPLIB), mimicking the original HAL/S compiler.",
)
@click.option(
    "--templib",
    type=click.Path(path_type=Path),
    multiple=True,
    help="Template-library directory searched by 'D INCLUDE TEMPLATE <name>' "
         "for <name>.tpl (repeatable). The source file's own directory is "
         "always searched.",
)
def compile(
    input_file: Path,
    tokens: Optional[Path],
    ast: Optional[Path],
    rust: Optional[Path],
    emit_exe: bool,
    opt_level: str,
    link_search: tuple,
    link_lib: tuple,
    link_obj: tuple,
    emit_cargo: Optional[Path],
    build: bool,
    target: str,
    gen_tpl: Optional[Path],
    templib: tuple,
) -> None:
    """Run the complete compilation pipeline."""
    console.print(
        Panel.fit(
            "[bold cyan]Resnik - A Groovy HAL/S Compiler[/bold cyan]",
            subtitle=f"v{__version__}",
        )
    )

    options = CompileOptions(
        input_file=input_file,
        tokens=tokens,
        ast=ast,
        rust=rust,
        emit_exe=emit_exe,
        opt_level=opt_level,
        link_search=list(link_search),
        link_lib=list(link_lib),
        link_obj=list(link_obj),
        emit_cargo=emit_cargo,
        build=build,
        target=target,
        gen_tpl=gen_tpl,
        templib=list(templib),
    )

    try:
        result = generate(options, RichReporter())
        if not result.ok:
            sys.exit(1)
        console.print("\n[bold green]✓ Compilation completed successfully![/bold green]")

    except ResnikError as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


class RichReporter(Reporter):
    """Render driver progress with Rich: spinners per phase, ✓/ℹ/✗ lines."""

    def phase(self, title: str):
        return _PhaseSpinner(title)

    def success(self, message: str) -> None:
        print_success(message)

    def info(self, message: str) -> None:
        print_info(message)

    def error(self, message: str) -> None:
        print_error(message)

    def diagnostic(self, diag: Diagnostic) -> None:
        console.print(f"  {diag}")

    def raw(self, text: str) -> None:
        console.print(text)


class _PhaseSpinner:
    """Context manager: a Rich spinner labelled with the phase title."""

    def __init__(self, title: str) -> None:
        self._title = title
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        )

    def __enter__(self) -> "_PhaseSpinner":
        self._progress.__enter__()
        self._task = self._progress.add_task(f"[cyan]{self._title}", total=None)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._progress.update(self._task, completed=True)
        self._progress.__exit__(exc_type, exc, tb)


@main.command()
def info() -> None:
    """Display information about the Resnik compiler."""
    info_panel = f"""
[bold cyan]Resnik HAL/S Compiler[/bold cyan]
Version: {__version__}

A modern compiler for the HAL/S aerospace programming language.
Translates legacy Space Shuttle code to safe, modern Rust.

[bold]Components:[/bold]
  ✓ Lexer    - Tokenization (Production Ready)
  ✓ Parser   - AST Generation (Foundation)
  → Backend  - Rust Code Generation (Planned)

[bold]Repository:[/bold] https://github.com/resnik-compiler/resnik
[bold]Documentation:[/bold] https://resnik.readthedocs.io
    """
    console.print(Panel(info_panel, title="[bold]Resnik Compiler[/bold]"))


# Convenience functions for programmatic access
def lex_command() -> None:
    """Entry point for resnik-lex command."""
    sys.argv = ["resnik", "lex"] + sys.argv[1:]
    main()


def parse_command() -> None:
    """Entry point for resnik-parse command."""
    sys.argv = ["resnik", "parse"] + sys.argv[1:]
    main()


if __name__ == "__main__":
    main()
