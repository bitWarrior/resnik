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
from resnik.backend.codegen import RustCodeGenerator
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
    type=click.Choice(["host", "cortex-m"]),
    default="host",
    show_default=True,
    help="Code-generation target profile: 'host' (std) or 'cortex-m' (no_std "
         "bare-metal firmware). Use with --emit-cargo for embedded builds.",
)
def compile(
    input_file: Path,
    tokens: Optional[Path],
    ast: Optional[Path],
    rust: Optional[Path],
    emit_exe: bool,
    opt_level: str,
    emit_cargo: Optional[Path],
    build: bool,
    target: str,
) -> None:
    """Run the complete compilation pipeline."""
    console.print(
        Panel.fit(
            "[bold cyan]Resnik - A Groovy HAL/S Compiler[/bold cyan]",
            subtitle=f"v{__version__}",
        )
    )
    if target != "host":
        print_info(f"Target profile: {target} (no_std bare-metal)")

    try:
        # Phase 1: Lexer
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Phase 1: Lexical Analysis", total=None)

            lexer = Lexer()
            token_list = lexer.tokenize_file(str(input_file))

            progress.update(task, completed=True)

        if lexer.errors:
            print_error(f"Lexer found {len(lexer.errors)} error(s)")
            for error in lexer.errors:
                console.print(f"  {error}")
            sys.exit(1)

        print_success(f"Tokenized {len(token_list)} tokens")

        if tokens:
            lexer.export_json(str(tokens))
            print_info(f"Tokens exported to {tokens}")

        # Phase 2: Parser
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Phase 2: Syntactic Analysis", total=None)

            parser = Parser()
            ast_result = parser.parse(token_list)

            progress.update(task, completed=True)

        if parser.errors:
            print_error(f"Parser found {len(parser.errors)} error(s)")
            for error in parser.errors:
                console.print(f"  {error}")
            sys.exit(1)

        if ast_result:
            print_success("Generated Abstract Syntax Tree")

            if ast:
                parser.export_ast_json(str(ast))
                print_info(f"AST exported to {ast}")
        else:
            print_error("Failed to generate AST")
            sys.exit(1)

        # Phase 3: Code Generation
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Phase 3: Code Generation", total=None)

            generator = RustCodeGenerator(target=target)
            rust_code = generator.generate(ast_result)
            rust_path = rust if rust else input_file.with_suffix(".rs")
            generator.write_to_file(str(rust_path), rust_code)

            progress.update(task, completed=True)

        print_success(f"Generated Rust source: {rust_path}")

        # Cross-compilation triple implied by the target profile (None = host).
        cross_triple = RustCodeGenerator.TARGET_TRIPLES.get(target)

        # Phase 4: Cargo project (optional)
        if emit_cargo:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(
                    "[cyan]Phase 4: Cargo Project", total=None
                )

                try:
                    cargo_result = generator.emit_cargo_project(
                        rust_code,
                        str(emit_cargo),
                        opt_level=opt_level,
                        build=build,
                        target=cross_triple,
                    )
                except RuntimeError as exc:
                    progress.update(task, completed=True)
                    print_error(str(exc))
                    sys.exit(1)

                progress.update(task, completed=True)

            print_success(f"Scaffolded Cargo project: {cargo_result.project_dir}")
            print_info(f"Cargo.toml:  {cargo_result.cargo_toml}")
            print_info(f"src/main.rs: {cargo_result.main_rs}")
            if cargo_result.built:
                if cargo_result.success:
                    print_success(f"Built executable: {cargo_result.executable}")
                else:
                    print_error("cargo build failed")
                    if cargo_result.stderr:
                        console.print(cargo_result.stderr)
                    sys.exit(1)

        # Phase 4: Native compilation (optional)
        if emit_exe:
            if target != "host":
                print_info(
                    "--emit-exe builds a single file with rustc; for a "
                    "bare-metal target prefer --emit-cargo (it scaffolds the "
                    "linker script and cortex-m-rt runtime).")
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(
                    "[cyan]Phase 4: Native Compilation", total=None
                )

                try:
                    result = generator.compile_to_executable(
                        rust_code,
                        str(rust_path.with_suffix("")),
                        opt_level=opt_level,
                        target=cross_triple,
                    )
                except RuntimeError as exc:
                    progress.update(task, completed=True)
                    print_error(str(exc))
                    sys.exit(1)

                progress.update(task, completed=True)

            if result.success:
                print_success(f"Built executable: {result.executable}")
            else:
                print_error("rustc failed to compile the generated code")
                if result.stderr:
                    console.print(result.stderr)
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
