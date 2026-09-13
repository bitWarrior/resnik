# Contributing to Resnik

Thanks for helping with an open-source HAL/S compiler. This page covers the
development setup, the repository layout, and how to run the two test
layers. Install first as described in the [README](README.md#installation)
(use `pip install -e ".[dev]"` for pytest, coverage, xdist and the linters).
Issues and pull requests are welcome on GitHub.

## Project structure
```
resnik/
├── src/resnik/              # Main package (clean imports!)
│   ├── lexer/              # Lexical analyzer
│   │   ├── lexer.py        # Modern lexer implementation
│   │   └── tokens.py       # Token definitions
│   ├── parser/             # Syntactic analyzer
│   │   ├── parser.py       # Modern parser implementation
│   │   ├── ast_nodes.py    # AST node classes
│   │   └── symbol_table.py # Symbol table
│   ├── backend/            # Rust code generator (host + no_std targets)
│   │   ├── codegen.py      # HAL/S → Rust lowering, target profiles, Cargo scaffold
│   │   ├── rust_emitter.py # Rust source emission helpers
│   │   └── type_mapper.py  # HAL/S → Rust type mapping
│   ├── cli.py              # Beautiful CLI with Click + Rich
│   ├── driver.py           # Reusable compile pipeline (used by the CLI and the IDE)
│   ├── templib.py          # Template-library (.tpl) generation for --gen-tpl
│   ├── types.py            # Type definitions
│   └── exceptions.py       # Custom exceptions
│
├── ide/                    # Desktop IDE (Tk) — see ide/README.md
│   ├── resnik_ide/         # resnik-ide package: editor, log pane, driver bridge
│   └── tests/              # IDE tests (Tk tests skip without a display)
├── start_ide.sh            # Launch the IDE (uses resnik-venv if present)
│
├── tests/                  # Test suite
│   ├── test_lexer/         # Lexer unit tests
│   ├── test_parser/        # Parser unit tests
│   ├── test_backend/       # Codegen / type-mapper unit tests
│   ├── test_driver/        # Compile-driver + CLI wrapper tests
│   ├── fixtures/           # HAL/S snippets used by unit tests
│   └── regression/         # End-to-end compile of example .hal programs
│
├── docs/                   # Documentation (MkDocs ready)
├── examples/               # Usage examples
├── pyproject.toml          # Single source of truth!
├── .pre-commit-config.yaml # Automated code quality
└── .github/workflows/      # CI/CD pipelines
```

## Testing

Resnik has two test layers:

| Layer | Command | What it covers | Needs `rustc`? |
| ----- | ------- | -------------- | -------------- |
| Unit tests | `python -m pytest` | Lexer, parser, and Python-side Rust codegen | No |
| Regression | `./tests/regression/run_regression.sh` | Compile example `.hal` files to binaries and run them | Yes |
| Embedded build | `./tests/regression/run_embedded_build.sh` | `cargo build` a curated example list for `esp32-c3` (+ one `cortex-m` case) — proves generated `no_std` firmware compiles; no hardware. Skips cleanly if cargo/targets are missing; first run needs the network for crates.io | Yes (+ rustup targets) |

Run the unit tests on every change. Run the regression suite before a release
or when you touch code generation, the CLI, or an example under
`docs/HAL-S-Source-Code-Examples/`.

### Unit tests

The unit suite is [pytest](https://pytest.org/). It imports `resnik` and checks
tokens, ASTs, and the **text** of generated Rust. It never shells out to
`rustc`, so it is the fast, no-toolchain check for a new clone.

**Once per clone** (same venv as [Installation](README.md#installation)):

```bash
python3 -m venv resnik-venv
. resnik-venv/bin/activate
pip install -e ".[dev]"             # pytest, coverage, xdist, linters
# pip install -e ".[test]"          # pytest + coverage only
```

Python 3.9 or newer is required; see [Installation](README.md#installation).
Use `python -m pytest` so the tests run from the venv even if another
`pytest` is on `PATH`.

**From the repository root:**

```bash
# Entire suite (lexer + parser + backend)
python -m pytest

# Verbose (print each test name)
python -m pytest -v

# One compiler stage
python -m pytest tests/test_lexer
python -m pytest tests/test_parser
python -m pytest tests/test_backend

# One file
python -m pytest tests/test_lexer/test_tokenization.py

# One test (node id from pytest -v)
python -m pytest tests/test_parser/test_parsing.py::TestDoUntil

# Tests whose names match a substring
python -m pytest -k tokenize
```

`pyproject.toml` sets `testpaths = ["tests"]`, so a bare `python -m pytest`
from the repo root is enough. A successful run prints a short summary and
exits `0`. Failures print the assertion and a traceback; fix those before
opening a pull request.

The `dev` extra also installs `pytest-xdist` for parallel runs:

```bash
python -m pytest -n auto
```

| Path | Stage |
| ---- | ----- |
| `tests/test_lexer/` | Tokenization and lexer edge cases |
| `tests/test_parser/` | Grammar, macros, `REPLACE`, symbol table, AST |
| `tests/test_backend/` | HAL/S → Rust lowering and type mapping |
| `tests/fixtures/` | Shared `.hal` snippets |
| `tests/regression/` | Not collected by pytest (shell script; see below) |

### Coverage

```bash
# Terminal report, including uncovered lines
pytest --cov=resnik --cov-report=term-missing

# HTML report (opens as htmlcov/index.html)
pytest --cov=resnik --cov-report=html
```

### Regression tests

`./tests/regression/run_regression.sh` is the end-to-end HAL/S suite. It
compiles each program under `docs/HAL-S-Source-Code-Examples/` with
`--emit-exe`, runs the resulting binary, and also exercises the template
library (`--gen-tpl` / `--templib`) and `EXTERNAL` C-object (`--link-obj`)
pipelines. Unlike the pytest unit tests, this **does** need `rustc`.

**Prerequisites.** See [Installation](README.md#installation) for `rustc` / `cc` and the
script’s sanitized `PATH`. You also need a git checkout (`git rev-parse` finds
the repo root) and the venv from [Installation](README.md#installation) so
`resnik-venv/bin/resnik` exists.

**Run**

```bash
./tests/regression/run_regression.sh
```

You can start it from anywhere inside the repo; it does not depend on the
venv being activated. Each case prints `[✓ PASS]` or `[✗ FAIL]`. A final
summary lists the pass/fail counts and exits `0` only if every case passed.
Generated `.rs` files, binaries, and temporary template/link directories are
removed when the script exits.

### `rustc` visibility for the regression script

The regression script is Bash and expects a git checkout. It resets `PATH` to
a short system list (`/usr/bin`, `/usr/local/bin`, …), so a rustup-only
`rustc` under `~/.cargo/bin` is **not** visible. After rustup, put `rustc`
on that list (a symlink is enough):

```bash
sudo ln -sf "$HOME/.cargo/bin/rustc" /usr/local/bin/rustc
```
