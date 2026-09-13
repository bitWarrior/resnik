# Resnik HAL/S Compiler 🚀

An open-source compiler for the HAL/S aerospace programming language, translating legacy NASA HAL/S source code to safe, modern Rust.

[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE.md)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Emits Rust](https://img.shields.io/badge/emits-Rust-orange.svg)
![Version 1.0.0](https://img.shields.io/badge/version-1.0.0-green.svg)

**Version:** 1.0.0 (Modern Edition)  
**Status:** Lexer ✓ | Parser ✓ | Backend ✓ | Cross-compilation (ARM Cortex-M stub, ESP32-C3) ✓ | Separate compilation (TEMPLIB) ✓

## Hello, World

A complete HAL/S program is three lines
(`docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal`):

```
HELLO: PROGRAM;
  WRITE(6) 'HELLO, WORLD [HAL/S LIVES!]';
CLOSE HELLO;
```

From a clone with the venv active (see [Installation](#installation)):

```bash
resnik compile docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal --emit-exe
./docs/HAL-S-Source-Code-Examples/hello_word/hello_world
# HELLO, WORLD [HAL/S LIVES!]
```

`resnik compile` alone writes `hello_world.rs`; `--emit-exe` also runs `rustc`
to produce the binary. Prefer a window? `./start_ide.sh` opens the same file in
the [desktop IDE](#ide). The same source flashes to an ESP32-C3 with
`--target esp32-c3 --emit-cargo` — see [Embedded targets](#embedded-targets).

## Status

- **Front end:** lexer and parser (with HAL/S semantic checks) for a working
  subset of USA003088 Appendix B, plus a few Resnik conveniences (`GOTO`,
  `DOWNTO`, infix `XOR`/`MOD`, parameterized `MACRO`, `TEMPLATE`, `RESUME`).
- **Back end:** Rust code generation for hosted (`std`) programs and `no_std`
  firmware (ARM Cortex-M stub, ESP32-C3); `ON ERROR` lowers onto `Result`.
- **Separate compilation:** TEMPLIB-style templates (`--gen-tpl` /
  `--templib`) with cross-unit type checking; `EXTERNAL` FFI to C/assembly.
- **Not yet:** `UPDATE` blocks, `SCHEDULE … AFTER/AT/ON/UNTIL/WHILE`,
  `LOCK`/`LATCHED`, `READALL`/`FILE`, `WRITE` format controls, `SUBBIT`, and
  others — the full keyword-by-keyword table is in
  [docs/LANGUAGE_SUPPORT.md](docs/LANGUAGE_SUPPORT.md). On `no_std` targets,
  `VECTOR`/`MATRIX`, the math built-ins (via `libm`), `COMPOOL` (via
  `critical-section`) and `SCHEDULE`/`TASK` (via a generated cyclic
  executive) work; `WAIT`, `READ` and `CHARACTER` variables still need
  `std` and emit a `compile_error!`.

## Installation

| Requirement | Notes |
| ----------- | ----- |
| OS | **Linux** — the supported, tested deployment. Native Windows/macOS are untested; on Windows use [WSL](https://learn.microsoft.com/windows/wsl/) (unverified on this project). |
| Python | 3.9 or newer, with `venv` and `pip` |
| Git | To clone the repository |
| Rust toolchain (optional) | Only for `--emit-exe`, `--emit-cargo`, embedded targets, or the regression suite. Install from [rustup.rs](https://rustup.rs/): `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` then `. "$HOME/.cargo/env"`. The `HW_INTERFACE` example also needs `cc` (`sudo apt install build-essential`). |
| Tk (IDE only) | `sudo apt install python3-tk` on Debian/Ubuntu; bundled with python.org builds |

You do **not** need Rust to run Resnik or to emit `.rs` files. Python
dependencies (`rply`, `click`, `rich`) are installed by `pip install -e .`:

```bash
# Clone the repository
git clone https://github.com/bitWarrior/resnik
cd resnik

# Create and activate the python venv
python3 -m venv resnik-venv
. resnik-venv/bin/activate

# Install with development dependencies
pip install -e ".[dev]"

# Or just runtime dependencies
pip install -e .
```

Install the Rust toolchain (see the table above) if you will
use `--emit-exe`, `./tests/regression/run_regression.sh`, `--emit-cargo`, or
an embedded `--target`. Skip it if you only need Resnik to write `.rs` files.

## Usage

### Command line

```bash
# Tokenize a HAL/S file  
resnik lex tests/fixtures/valid/sample_hals_code.hal

# Parse to AST
resnik parse tests/fixtures/valid/simple_parser_test.hal

# Complete compilation pipeline
resnik compile tests/fixtures/valid/sample_hals_code.hal

# Get help
resnik --help

# End-to-end HAL/S example regression (needs rustc; see Requirements)
./tests/regression/run_regression.sh
```

### IDE

Resnik ships a minimal desktop IDE (Tk) for editing a HAL/S file, running a
**Semantic Check** (lexer + parser) or a full **Compile**, and watching the
result in a log pane. It lives in [`ide/`](ide/README.md) and is installed
together with the compiler by `pip install -e .`.

```bash
# From the repository root, no need to activate the venv:
./start_ide.sh                                   # empty editor
./start_ide.sh docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal

# Equivalent, with the venv active:
resnik-ide [file.hal]
python -m resnik_ide [file.hal]
```

`start_ide.sh` uses `resnik-venv/bin/python` when present (else `python3` on
PATH) and tells you what to install if Tk (`python3-tk` on Debian/Ubuntu) or
the `resnik_ide` package is missing. Keyboard: **F7** Semantic Check, **F5**
Compile, **F9** Build and Run (compile with `--emit-exe`, then run the program
with its output in the log), **Ctrl+,** Compiler Options, Ctrl+O/S/N
open/save/new. Click a
diagnostic in the log to jump to its line. *Options…* exposes every
`resnik compile` flag with a live equivalent-command line; settings persist
in `~/.config/resnik/ide.json`. The **Target** panel checks the Rust
toolchain for the selected profile (`rustup target add`, `rust-src`,
`espflash` / `probe-rs` — one-click Install), lists serial ports, and runs
**Build / Flash & Run / Monitor / Run on host / Stop** through a streamed,
cancellable process runner. The editor highlights HAL/S syntax using the
compiler's own lexer and remembers recent files, font size and window
layout. See `ide/README.md` and the "Desktop IDE" chapter of the Users
Guide (`docs/Resnik Users Guide/`) for details.

### Embedded targets

Resnik can emit either hosted Rust or bare-metal firmware, selected with the
`--target` flag on `resnik compile`:

| Profile            | Description                                                          |
| ------------------ | ------------------------------------------------------------------- |
| `host` (default)   | Hosted `std` Rust — runs on your local computer.                     |
| `cortex-m`         | `no_std` **stub** for ARM Cortex-M (semihosting `WRITE`; not a finished MCU port). |
| `esp32-c3`         | `no_std` firmware for **ESP32-C3** (RISC-V) via `esp-hal`. USB-serial `WRITE`. |

Once per machine (ESP32-C3; needs stable Rust ≥ 1.88 — stock rustc, not espup):

```bash
rustup target add riscv32imc-unknown-none-elf   # RISC-V code generation
rustup component add rust-src                   # esp-hal builds core from source
cargo install espflash --locked                 # flasher + serial monitor (compiles for a few
                                                # minutes; installs to ~/.cargo/bin — put that on
                                                # PATH: . "$HOME/.cargo/env")
sudo usermod -aG dialout $USER                  # serial-port permission (log out/in once)
```

The ESP32 libraries themselves (`esp-hal`, `esp-println`, `esp-backtrace`,
`esp-bootloader-esp-idf`) are **not** installed by hand: the scaffold's
`Cargo.toml` pins them, and the first `cargo build` downloads and compiles
them (~130 crates, network required, a minute or two; instant afterwards).

Then, per program:

```bash
resnik compile hello_world.hal --target esp32-c3 --emit-cargo ./firmware
cd firmware && cargo run --release      # builds, flashes, opens the serial monitor
```

`--emit-cargo` scaffolds a buildable crate (`Cargo.toml`, `.cargo/config.toml`
with the flash runner, `memory.x` for Cortex-M). The full walkthrough —
Cortex-M stub layout, the ESP32-C3 hello world step by step, what works on
`no_std` — is in [docs/EMBEDDED_TARGETS.md](docs/EMBEDDED_TARGETS.md).

Common trip-ups (details and fixes in
[docs/EMBEDDED_TARGETS.md → Troubleshooting](docs/EMBEDDED_TARGETS.md#troubleshooting)):

| Symptom | Cause |
| ------- | ----- |
| `unresolved import esp_hal` (E0432) and friends from rustc | `--emit-exe` was combined with an embedded `--target`. `--emit-exe` builds a single file with plain rustc, which cannot supply the embedded runtime crates — use `--emit-cargo` and build/flash with cargo. |
| `espflash: command not found` right after `cargo install espflash` | `cargo install` puts binaries in `~/.cargo/bin`, which is not on PATH when rustup/cargo come from the distro. Add it to PATH (`. "$HOME/.cargo/env"`). The IDE finds it there automatically. |
| `espflash` fails with `Failed to initialize input reader` after flashing | Its interactive serial monitor needs a real terminal on stdin. From a script or IDE, pass `--non-interactive` (espflash ≥ 3); the Resnik IDE does this for you. |

### Templates & separate compilation

HAL/S supports separate compilation through *templates* — signature-only
prototypes, analogous to C headers. Resnik mirrors the original compiler's
**template library** (TEMPLIB) workflow: `--gen-tpl` writes a `.tpl` prototype
for each `PROCEDURE`/`FUNCTION`/`COMPOOL` on a successful compile, and a
`D INCLUDE TEMPLATE <name>` directive pulls one in (searched via `--templib`).
`CALL` sites are type-checked against the template — arity and dimensions —
before any code is generated.

```bash
resnik compile nav_update.hal --gen-tpl ./TEMPLIB     # writes ./TEMPLIB/NAV_UPDATE.tpl
resnik compile guidance.hal   --templib ./TEMPLIB     # guidance.hal: D INCLUDE TEMPLATE NAV_UPDATE
```

### Foreign code: `EXTERNAL`

`EXTERNAL` declares a symbol whose definition is genuinely foreign —
hand-written assembly, a C routine, a BSP entry point — and lowers directly to
`extern "C"` (every use is `unsafe`; nothing is type-checked across the
boundary). Link the objects in with `--link-search` / `--link-lib` /
`--link-obj`:

```bash
cc -c i2c.c -o i2c.o
resnik compile hw_interface.hal --emit-exe --link-obj i2c.o
```

Both features, with the generated Rust and the linkage rules, are documented in
[docs/SEPARATE_COMPILATION_AND_FFI.md](docs/SEPARATE_COMPILATION_AND_FFI.md).

### Programmatic usage

```python
from resnik import Lexer, Parser

# Tokenize
lexer = Lexer()
tokens = lexer.tokenize_file("program.hal")

# Parse
parser = Parser()
ast = parser.parse(tokens)

# Check for errors
if parser.errors:
    parser.report_errors()
else:
    print(f"✓ Successfully parsed: {ast.name}")
```

## Documentation

| Document | What it covers |
| -------- | -------------- |
| [Users Guide](docs/Resnik%20Users%20Guide/RESNIK_USER_GUIDE.md) | Lexer, parser, backend, CLI, the desktop IDE, troubleshooting |
| [Desktop IDE](ide/README.md) | Editor, Semantic Check / Compile / Build and Run, Options, Target panel, settings |
| [Language support](docs/LANGUAGE_SUPPORT.md) | Resnik-only words and the Appendix B keywords still to do |
| [Embedded targets](docs/EMBEDDED_TARGETS.md) | Cortex-M stub, ESP32-C3 hello world, `no_std` feature support |
| [Separate compilation & FFI](docs/SEPARATE_COMPILATION_AND_FFI.md) | `TEMPLATE` / TEMPLIB and `EXTERNAL` in detail |
| [Design notes](docs/DESIGN.md) | Why Python for the front end and Rust for the back end |
| [Background](docs/BACKGROUND.md) | About HAL/S, Shuttle flight-software history, the compiler's namesake |
| [Requirements](docs/Resnik%20Requirements/) · [HAL/S specification](docs/HAL-S-Language-Specification/) · [Programmer's Guide](docs/HAL-S-Programmers-Guide/) · [Examples](docs/HAL-S-Source-Code-Examples/) | Reference material |

## Development

```
src/resnik/   lexer/ parser/ backend/ cli.py driver.py templib.py   # the compiler
ide/          resnik_ide/ tests/                                      # the desktop IDE
tests/        test_lexer/ test_parser/ test_backend/ test_driver/ regression/
docs/         specifications, guides, examples
```

```bash
python -m pytest                            # unit tests (no rustc needed)
./tests/regression/run_regression.sh        # compile + run every example (needs rustc)
./tests/regression/run_embedded_build.sh    # cargo-build examples for esp32-c3 (no board)
./tests/regression/run_exec_semantics.sh    # cyclic-executive semantics (fake clock, needs rustc)
```

The full project layout, test-suite details, coverage and regression notes
are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Design notes

Resnik is *implemented in* Python and *emits* Rust — each language is used
where it is strongest: Python for an exploratory, text-heavy compiler front
end that runs at build time, Rust as a memory-safe, `no_std`-capable target
that mirrors HAL/S's typed, real-time design and has a path to safety-critical
qualification. The reasoning in full: [docs/DESIGN.md](docs/DESIGN.md).

## Background

HAL/S (*High-order Assembly Language / Shuttle*) is a real-time aerospace
programming language developed by Intermetrics for NASA in the early 1970s. It
was the primary language for the Space Shuttle's flight software and is one of
the most successful safety-critical languages ever fielded, flying human-rated
avionics for roughly three decades. Designed decades before "memory safety" was
a common term, it emphasized strong typing, explicit real-time semantics, and
verifiability — the same properties that make Rust a fitting modern compilation
target. Beyond the Shuttle, HAL/S flew on NASA's Galileo spacecraft, whose
Attitude and Articulation Control Subsystem software was written in it: the
Galileo spacecraft launched on October 18, 1989, while the first satellites for
the European Galileo Navigation System lifted off on December 28, 2005.

The compiler is named after **Judith Resnik** (1949–1986), electrical engineer,
pilot and NASA astronaut. More on the language, its Shuttle deployments, the
PASS flight software and its history, and the namesake:
[docs/BACKGROUND.md](docs/BACKGROUND.md) and
[docs/Space_Shuttle_Flight_Software_History/](docs/Space_Shuttle_Flight_Software_History/).

## License

This project is licensed under the GNU General Public License v3.0 or later — see the [LICENSE.md](LICENSE.md) file for details.
