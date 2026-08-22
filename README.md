# Resnik HAL/S Compiler 🚀

An open-source compiler for the HAL/S aerospace programming language, translating legacy NASA HAL/S source code to safe, modern Rust.

**Version:** 1.0.0 (Modern Edition)  
**Status:** Lexer ✓ | Parser ✓ | Backend ✓ | Cross-compilation (ARM Cortex-M stub, ESP32-C3) ✓ | Separate compilation (TEMPLIB) ✓

## About HAL/S

HAL/S (*High-order Assembly Language / Shuttle*) is a real-time aerospace
programming language developed by Intermetrics for NASA in the early 1970s. It
was the primary language for the Space Shuttle's flight software and is one of
the most successful safety-critical languages ever fielded, flying human-rated
avionics for roughly three decades.

HAL/S was purpose-built for guidance, navigation, and control software, and its
design reflects that domain:

- **Aerospace-native types.** Beyond `INTEGER`, `BOOLEAN`, and `CHARACTER`, the
  language has first-class `SCALAR` (floating point), `VECTOR`, `MATRIX`, and
  `BIT` (bit-string) types, so linear-algebra-heavy flight math reads naturally.
- **Built-in vector/matrix arithmetic.** Dot and cross products, matrix
  multiplication, transpose, and inverse are language-level operations rather
  than library calls.
- **Real-time scheduling.** `SCHEDULE`, `TASK`, `UPDATE`, `WAIT`, and
  `PRIORITY`, with `EVERY`/`AFTER`/`AT` timing, express the cyclic and
  priority-driven execution model of a flight computer.
- **Shared data pools.** `COMPOOL` blocks define shared, inter-process data —
  the coordination mechanism between independently scheduled processes.
- **Structured error handling.** `ON ERROR` handlers respond to numbered runtime
  faults, supporting the fault detection and recovery that human-rated systems
  demand.
- **Register-level control.** `BIT` types and `$` bit-subscripting give direct,
  explicit access to hardware bits and status registers.
- **A distinctive multi-line source form.** HAL/S source could be written across
  stacked lines — a main line with exponent and subscript annotation lines above
  and below — so code resembled the mathematical notation it implemented. (A
  conventional single-line form is also supported, and is what Resnik targets.)

Designed decades before "memory safety" was a common term, HAL/S emphasized
strong typing, explicit real-time semantics, and verifiability — the same
properties that make Rust a fitting modern compilation target.

## HAL/S keywords

USA003088 Appendix B is the official HAL/S keyword list. Resnik implements a
working subset and a few convenience spellings of its own.

### Resnik-only words

Resnik also reserves and implements the words below, which are **not**
Appendix B keywords. The lexer treats them as reserved, so they cannot be used
as identifiers.

| Word | Official HAL/S | What Resnik accepts |
| ---- | -------------- | ------------------- |
| `GOTO` | Two-word `GO TO` only | One-token `GOTO <label>;` (same AST as `GO TO`) |
| `DOWNTO` | `DO FOR I = N TO 1 BY -1` | `DO FOR I = N DOWNTO 1;` |
| `OF` | `ARRAY(n) INTEGER` | Optional `OF`: `ARRAY(n) OF INTEGER` |
| `MACRO` / `MEND` | Percent-macros (`%name`) | Parameterized `MACRO name(params); ... MEND;` preprocessor |
| `TEMPLATE` | Not an Appendix B keyword | Signature-only prototype on `PROCEDURE` / `FUNCTION` / `COMPOOL` (see [Templates & Separate Compilation](#templates--separate-compilation)) |
| `XOR` | Built-in function `XOR(a, b)` | Infix `A XOR B` as well as the function |
| `MOD` | Built-in function `MOD(a, b)` | Infix `A MOD B` as well as the function |
| `RESUME` | Error-handler cancel is `OFF ERROR` | Continues after the faulting statement in an `ON ERROR` handler |

The official spellings remain valid: `GO TO`, `ARRAY(n) T`, `XOR(A, B)`, and
`MOD(A, B)` all parse.

### Future work

These Appendix B keywords are not yet accepted as real language (no parser
production, or only a different construct uses the same word). Several are
already reserved by the lexer, so they also cannot be used as identifiers.
`HEX` / `BIN` / `OCT` literal prefixes and the `REPLACE` preprocessor are
implemented and are not listed here.

| Area | Keywords | Official construct still to do |
| ---- | -------- | ------------------------------ |
| Blocks and real-time | `UPDATE` | `UPDATE` block (shared-data mutual exclusion) and `UPDATE PRIORITY(...)` |
| | `AFTER`, `AT`, `DEPENDENT` | `SCHEDULE T AFTER S`, `SCHEDULE T AT <time>`, `SCHEDULE ... DEPENDENT` |
| | `ON` | `SCHEDULE ... ON <event>` (`ON ERROR` already parses) |
| | `UNTIL` | `SCHEDULE ... UNTIL ...` (`WAIT UNTIL`, `DO UNTIL`, and `DO FOR ... UNTIL` already parse) |
| | `WHILE` | `SCHEDULE ... WHILE <cond>` (`DO WHILE` already parses) |
| | `REENTRANT`, `EXCLUSIVE` | Procedure / function attributes |
| Declarations | `LATCHED` | `DECLARE E EVENT LATCHED;` |
| | `LOCK` | `DECLARE X INTEGER LOCK;` and lock groups |
| | `ACCESS`, `REMOTE`, `TEMPORARY` | Compool access control, remote data, temporary declarations |
| | `RIGID` | Structure layout (no field reordering); `ALIGNED` / `DENSE` already parse |
| | `SEND` | Process / event send |
| I/O | `READALL`, `FILE` | `READALL(channel) ...`, file/channel binding |
| | `SKIP`, `TAB`, `COLUMN`, `LINE`, `PAGE` | `WRITE` format controls (`WRITE(6) SKIP(2), TAB(10), X`) |
| Error handling | `OFF`, `IGNORE` | `OFF ERROR`, `ON ERROR IGNORE` |
| Bit / character | `SUBBIT` | Bit-view pseudo-variable (`SUBBIT(X)`, `SUBBIT 5 TO 8 (K)`) |
| | `CHAR` | Repeated character literal (`CHAR 5 'AB'`) |
| Literals | `TRUE`, `FALSE` | Reserved boolean literals. Resnik currently special-cases identifiers named `TRUE` / `FALSE` but does not reserve them, so `DECLARE TRUE INTEGER;` is accepted |
| | `DEC` | Radix conversions `CHARACTER @DEC(...)` / `BIT @DEC(...)` (`DEC 'digits'` bit literals and infix `CAT` already parse) |
| Other | `SYSTEM` | System / process attribute |

## Why Python or the Lexer / Parser?

Resnik is *implemented in* Python and *emits* Rust — each language is used where
it is strongest. Rust is the right **target** for safety-critical firmware;
Python is the right **implementation language** for a compiler front end, where
the work is exploratory text processing and the compiler itself runs at build
time rather than on the flight computer:

- **Fast iteration on grammar and AST.** There is no build step between editing
  a token rule, grammar production, or AST node and running it, which suits the
  experimental nature of front-end design — the lexer, parser, and AST in this
  project were grown incrementally against a large test suite.
- **A mature parsing ecosystem.** Resnik's lexer and parser are built on
  [`rply`](https://github.com/alex/rply), a pure-Python `PLY`-style LR parser
  generator, one of several well-trodden Python options (PLY, Lark, ANTLR) for
  tokenization and LR/LALR parsing.
- **Expressive, readable front-end code.** Python `@dataclass` types model
  tokens, type specs, and AST nodes concisely, and dictionaries back the symbol
  table and scopes — the front end reads close to the grammar it implements.
- **First-class text processing.** Regex-driven tokenization, solid Unicode
  handling (HAL/S operators like `¬=`), and easy string manipulation make the
  preprocessing passes — comment-card stripping, `REPLACE`, and parameterized
  `MACRO` expansion — straightforward to write and test.
- **Performance where it doesn't cost anything.** The front end runs once over
  source at compile time, so Python's runtime speed is never on the critical
  path — and it has zero effect on the generated program, which is native Rust.
- **Batteries-included tooling.** `pytest` powers a comprehensive regression
  suite, JSON serialization of tokens and the AST (`to_dict()`) aids tooling and
  debugging, and [`Click`](https://click.palletsprojects.com/) +
  [`Rich`](https://rich.readthedocs.io/) provide the CLI.
- **A low barrier to contribution.** Python's readability keeps the grammar,
  AST, and backend approachable for contributors — important for an open-source
  compiler meant to be extended.

## Why Rust for the Backend Language?

HAL/S was designed in the 1970s to be a safe, strongly-typed, real-time language
for human-rated avionics. Rust pursues those same goals with fifty years of
additional language-design progress, which makes it a natural modern target for
HAL/S code:

- **Memory safety without a garbage collector.** Rust's ownership and borrow
  checking eliminate buffer overflows, use-after-free, null-pointer
  dereferences, and data races *at compile time* — entire bug classes behind
  historical flight-software failures — while emitting native code with no
  runtime garbage collector and no unpredictable pauses.

- **Deterministic, real-time behavior.** Zero-cost abstractions, no GC, and
  `panic = abort` builds give predictable timing and bounded memory behavior,
  well-suited to the cyclic executives and control loops of HAL/S's Digital
  Autopilot heritage.

- **Bare-metal `no_std` support.** The same generated source scales from a
  hosted executable down to microcontroller firmware with no operating system
  (see [Cross-Compilation](#cross-compilation-embedded-targets)). A single
  target triple separates a desktop build from ARM Cortex-M, RISC-V, or AVR
  flight hardware.

- **An expressive type system that mirrors HAL/S.** HAL/S's typed declarations
  map directly onto Rust types (`INTEGER` → `i32`, `SCALAR` → `f32`/`f64`,
  `BIT(n)` → unsigned registers, `VECTOR`/`MATRIX` → sized numeric types), and
  Rust's `Result` plus exhaustive `match` model HAL/S error handling — Resnik
  lowers `ON ERROR` fault bubbling directly onto `Result` propagation.

- **Fearless concurrency.** The type system rejects data races at compile time,
  giving a sound foundation for HAL/S real-time constructs such as `SCHEDULE`
  and `TASK`.

- **Safe hardware access.** HAL/S is a register-oriented language; its `BIT`
  types and `$` bit-subscripting map cleanly onto Rust's `volatile` register
  access and peripheral-access crates, keeping direct hardware manipulation
  memory-safe.

- **First-class tooling.** `cargo`, a built-in test harness, and rustc's precise
  diagnostics come for free, and Rust's target-triple model plus a mature
  embedded ecosystem (`cortex-m`, `embedded-hal`, `probe-rs`) make retargeting
  new hardware straightforward.

- **A path to safety-critical qualification.** Rust's static guarantees move
  much of the verification effort to compile time, shrinking the surface that
  must be reviewed for high-assurance software. Qualified Rust toolchains such
  as [Ferrocene](https://ferrocene.dev/) are independently qualified against
  functional-safety standards (ISO 26262, IEC 61508, IEC 62304) and provide
  tooling to support aerospace DO-178C (DAL C) certification efforts.

## License

This project is licensed under the GNU General Public License v3.0 or later — see the [LICENSE.md](LICENSE.md) file for details.

## Requirements

**To install and run the compiler** (`resnik lex`, `resnik parse`, and
`resnik compile` emitting Rust source):

| Requirement | Notes |
| ----------- | ----- |
| OS | **Linux** — this is the supported, tested deployment |
| Python | 3.9 or newer, with `venv` and `pip` (`requires-python` in `pyproject.toml`) |
| Git | To clone the repository |

Native Windows and macOS are untested. On Windows, the realistic option is
[WSL](https://learn.microsoft.com/windows/wsl/) (Windows Subsystem for Linux):
it is a Linux environment, so the install, Bash regression script, and
`/dev/tty*` serial paths below are the ones that would apply. WSL has not been
verified on this project.

Python dependencies (`rply`, `click`, `rich`) are installed by
`pip install -e .` in the next section. You do not need Rust to emit `.rs`
files.

**Also needed for some commands:**

| You want to… | Also install |
| ------------ | ------------ |
| `resnik compile … --emit-exe`, or `./tests/regression/run_regression.sh` | `rustc` (and `cc` for the `HW_INTERFACE` example) |
| `--target cortex-m` / `--target esp32-c3` and flash | See [Cross-Compilation](#cross-compilation-embedded-targets) |

The regression script is Bash and expects a git checkout. It resets `PATH` to
a short system list (`/usr/bin`, `/usr/local/bin`, …), so a rustup-only
`rustc` under `~/.cargo/bin` is not visible. Install a system `rustc` or place
the binary in one of those directories.

## Installation

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

### Usage

```bash
# Tokenize a HAL/S file  
resnik lex tests/fixtures/valid/sample_hals_code.hal

# Parse to AST
resnik parse tests/fixtures/valid/simple_parser_test.hal

# Complete compilation pipeline
resnik compile tests/fixtures/valid/sample_hals_code.hal

# Get help
resnik --help

# End-to-end HAL/S example regression (needs rustc; see Testing)
./tests/regression/run_regression.sh
```

### Cross-Compilation (Embedded Targets)

Resnik can emit either hosted Rust or bare-metal firmware, selected with the
`--target` flag on `resnik compile`:

| Profile            | Description                                                          |
| ------------------ | ------------------------------------------------------------------- |
| `host` (default)   | Hosted `std` Rust — runs on your local computer.                     |
| `cortex-m`         | `no_std` **stub** for ARM Cortex-M (semihosting `WRITE`; not a finished MCU port). |
| `esp32-c3`         | `no_std` firmware for **ESP32-C3** (RISC-V) via `esp-hal`. USB-serial `WRITE`. |

This is **not** classic Xtensa ESP32 (`ESP32` / S2 / S3). Those chips need the
`espup` toolchain; they are a later profile. ESP32-C3 uses stock `rustc`.
Do not treat ESP-IDF / `std` as this target.

**Feature support on `no_std` targets.** Core control flow, arithmetic, `BIT`
operations, `IF`/`DO`, procedures, and the `ON ERROR` fault model all lower
cleanly to bare metal — the fault model unwinds via `Result` and panics on an
unhandled fault (`panic-halt` on Cortex-M, `esp-backtrace` on ESP32-C3 so the
fault is visible on the serial console). Features whose lowering still depends
on `std` — `VECTOR`/`MATRIX` and math intrinsics, `SCHEDULE`/`TASK`, and
`COMPOOL` — emit a `compile_error!` rather than failing silently. These are on
the embedded roadmap.

#### Cortex-M stub

The `cortex-m` profile emits a `#![no_std]` / `#![no_main]` crate with a
`#[cortex_m_rt::entry]` reset entry point, a halting panic handler, and routes
HAL/S `WRITE` output to semihosting (swappable for RTT or UART downstream).
It is a crate *shape*, not a complete board support package.

```bash
rustup target add thumbv7em-none-eabihf
resnik compile program.hal --target cortex-m --emit-cargo ./firmware
cd firmware
# 1. Edit memory.x to match your MCU's flash/RAM map.
# 2. Uncomment a runner in .cargo/config.toml
#    (probe-rs for hardware, QEMU for emulation).
cargo build --release
cargo run   --release
```

With `--emit-cargo`, the `cortex-m` profile scaffolds:

```
firmware/
├── Cargo.toml            # cortex-m, cortex-m-rt, panic-halt, cortex-m-semihosting
├── .cargo/config.toml    # target triple, linker args, probe-rs/QEMU runner
├── memory.x              # MCU flash/RAM layout — edit for your chip
├── build.rs              # puts memory.x on the linker search path
└── src/main.rs           # generated firmware
```

#### Hello World on ESP32-C3

This is the path from a HAL/S source file to a running chip. The example
program is `docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal`.

**1. Write the HAL/S program.** A complete hello world is three lines:

```
HELLO: PROGRAM;
  WRITE(6) 'HELLO, WORLD [HAL/S LIVES!]';
CLOSE HELLO;
```

`WRITE(6)` is standard output. On `esp32-c3` it becomes `esp_println::println!`
on the DevKit USB serial / JTAG console. Stay inside the `no_std` subset
(no `VECTOR` / `MATRIX`, `SCHEDULE` / `TASK`, or `COMPOOL`).

You can copy that file or save the same text as `hello_world.hal` in the
current directory.

**2. Install the RISC-V target and flasher** (once per machine). You need a
recent stable Rust (esp-hal 1.1 wants **1.88+**), stock `rustc` — not `espup`:

```bash
rustup update stable
rustup target add riscv32imc-unknown-none-elf
rustup component add rust-src
cargo install espflash
```

Plug an **ESP32-C3-DevKit** (or any C3 board with USB-Serial/JTAG) into USB.
It usually appears as `/dev/ttyACM0` on Linux.

**3. Compile HAL/S to a Cargo firmware project:**

```bash
# from this repository:
resnik compile docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal \
  --target esp32-c3 --emit-cargo ./firmware

# or from a copy named hello_world.hal in the current directory:
resnik compile hello_world.hal --target esp32-c3 --emit-cargo ./firmware
```

`--emit-cargo` writes a buildable crate (no STM32 `memory.x`; flash layout is
esp-hal's `linkall.x`):

```
firmware/
├── Cargo.toml            # esp-hal 1.1 (esp32c3), esp-backtrace, esp-println, esp-bootloader-esp-idf
├── rust-toolchain.toml   # stable + rust-src + riscv32imc-unknown-none-elf
├── .cargo/config.toml    # RISC-V triple, linkall.x, runner = espflash flash --monitor
└── src/main.rs           # generated firmware (#[esp_hal::main], USB-serial WRITE)
```

**4. Build, load onto the chip, and run.** From the scaffolded project:

```bash
cd firmware
cargo run --release
```

`cargo run` builds for `riscv32imc-unknown-none-elf`, then the configured
runner flashes and opens a serial monitor:

```text
espflash flash --monitor
```

Equivalent, if you prefer to flash the ELF yourself:

```bash
cargo build --release
espflash flash --monitor --chip esp32c3 \
  target/riscv32imc-unknown-none-elf/release/firmware
```

**5. What you should see.** After reset, the USB serial console prints:

```text
HELLO, WORLD [HAL/S LIVES!]
```

The program then parks in `loop {}` (bare-metal `main` never returns). Reset
the board to print again.

If `espflash` cannot open the port: unplug USB, hold **BOOT**, plug back in,
release **BOOT**. Some boards also need a tap on **RESET** while **BOOT** is
held. Pass `--port /dev/ttyACM0` if several serial devices are present.

Host check of the same source (no board required):

```bash
resnik compile hello_world.hal --emit-exe
./hello_world
```

### Templates & Separate Compilation

HAL/S supports separate compilation through *templates* — signature-only
prototypes, analogous to C headers. A block marked `TEMPLATE` declares a unit's
name, parameters, and data layout for type checking, but generates no executable
code and allocates no memory:

```
NAV_UPDATE: PROCEDURE(POS, VEL) ASSIGN(STATE_VEC) TEMPLATE;
  DECLARE POS VECTOR(3);
  DECLARE VEL VECTOR(3);
  DECLARE STATE_VEC VECTOR(6);
CLOSE NAV_UPDATE;
```

Resnik mirrors the original HAL/S compiler's **template library** (TEMPLIB)
workflow, so you never hand-write a prototype.

**Generate templates.** On a successful compile, `--gen-tpl` auto-generates a
prototype for each reusable unit (`PROCEDURE`, `FUNCTION`, `COMPOOL`) into a
template-library directory:

```bash
resnik compile nav_update.hal --gen-tpl ./TEMPLIB
# writes ./TEMPLIB/NAV_UPDATE.tpl
```

**Include templates.** A `D INCLUDE TEMPLATE <name>` directive — with `D` in
column 1, following the HAL/S directive-card convention — pulls a stored
template from the library and injects its signature *before the rest of the file
is parsed*. Point the preprocessor at the library with `--templib` (repeatable;
the source file's own directory is always searched):

```bash
resnik compile guidance.hal --templib ./TEMPLIB
```

```
D INCLUDE TEMPLATE NAV_UPDATE
GUIDANCE: PROGRAM;
  DECLARE POSITION VECTOR(3);
  DECLARE VELOCITY VECTOR(3);
  DECLARE STATE VECTOR(6);
  CALL NAV_UPDATE(POSITION, VELOCITY) ASSIGN(STATE);
CLOSE GUIDANCE;
```

**Cross-unit type checking.** Once a template's signature is registered, `CALL`
sites are validated against it — input arity, output (`ASSIGN`) arity, and type
*dimensions*. Passing a `VECTOR(4)` where the prototype requires a `VECTOR(3)`
is rejected at semantic analysis, before any code is generated, even though the
called unit's body lives in another file.

**Linkage.** A `TEMPLATE` that is actually called is forward-declared as an
`extern "C"` binding in the generated Rust, and the call is wrapped in `unsafe`,
leaving the separately compiled body to be resolved by the linker:

```rust
extern "C" { fn nav_update(pos: Vector<3>, vel: Vector<3>, state_vec: &mut Vector<6>); }
...
unsafe { nav_update(position, velocity, &mut state); }
```

### Foreign Code: `EXTERNAL`

Where a `TEMPLATE` is a prototype for another HAL/S unit, `EXTERNAL` declares a
symbol whose definition is genuinely foreign — hand-written assembly, a C
routine, a BSP entry point, or a linker-provided value. It is Resnik's
straightforward Foreign Function Interface: `EXTERNAL` procedures and variables
lower directly to `extern "C"` and — unlike `TEMPLATE` — are **not** type-checked
across the boundary or banked in the template library. You are trusting the
linker.

```
HW_INTERFACE: PROGRAM;
  /* external variable -- defined in another object file; no storage here */
  DECLARE RAW_TEMP SCALAR EXTERNAL;

  /* external routine -- a prototype for code compiled outside HAL/S */
  READ_I2C_BUS: PROCEDURE(BUS_ID) EXTERNAL;
    DECLARE BUS_ID INTEGER;
  CLOSE READ_I2C_BUS;

  CALL READ_I2C_BUS(2);
  IF RAW_TEMP > 85.0 THEN DO; ... END;
CLOSE HW_INTERFACE;
```

lowers to:

```rust
extern "C" { fn read_i2c_bus(bus_id: i32); }
extern "C" { static raw_temp: f32; }
...
unsafe { read_i2c_bus(2); }
if (unsafe { raw_temp }) > 85.0 { ... }
```

Every use is `unsafe`, and the external variable allocates no local storage.
Symbol names follow HAL/S case-insensitivity: `READ_I2C_BUS` links against the C
symbol `read_i2c_bus`.

**Linking.** rustc compiles the `extern "C"` declarations into *undefined*
symbols; the linker must be handed their definitions. When building an
executable with `--emit-exe`, pass the foreign objects/libraries through:

| Flag | rustc | Purpose |
| ---- | ----- | ------- |
| `-L, --link-search DIR` | `-L DIR`           | linker search directory |
| `-l, --link-lib NAME`   | `-l NAME`          | link a native library   |
| `--link-obj FILE`       | `-C link-arg=FILE` | add an object / archive |

```bash
cd docs/HAL-S-Source-Code-Examples/hw_interface
cc -c i2c.c -o i2c.o
resnik compile hw_interface.hal --emit-exe --link-obj i2c.o
```

For larger builds, prefer `--emit-cargo` and let a `build.rs` (with the `cc`
crate) compile and link the foreign sources — the idiomatic path for BSP objects
and linker scripts.

> **Note:** an external hardware register whose value changes underneath the
> program is currently read as a plain (non-volatile) `static`; volatile-correct
> reads for memory-mapped registers are on the roadmap.

### Programmatic Usage

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

### Programming Language Triva
The "HAL" name was chosen to honor Hal Laning, a pioneer in aerospace computing at MIT. The "S" was added to stand for "Shuttle"

### HAL/S Deployments

- Space Shuttle: Flight Software (FSW) for the Space Shuttle's onboard computers, including the Primary Avionics Software System (PASS) and the Backup Flight Software (BFS).

- Galileo: Deployed Flight Software (FSW) for the Attitude and Articulation Control Subsystem (AACS),

## Project Structure
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
│   ├── templib.py          # Template-library (.tpl) generation for --gen-tpl
│   ├── types.py            # Type definitions
│   └── exceptions.py       # Custom exceptions
│
├── tests/                  # Test suite
│   ├── test_lexer/         # Lexer unit tests
│   ├── test_parser/        # Parser unit tests
│   ├── test_backend/       # Codegen / type-mapper unit tests
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

Run the unit tests on every change. Run the regression suite before a release
or when you touch code generation, the CLI, or an example under
`docs/HAL-S-Source-Code-Examples/`.

### Unit tests

The unit suite is [pytest](https://pytest.org/). It imports `resnik` and checks
tokens, ASTs, and the **text** of generated Rust. It never shells out to
`rustc`, so it is the fast, no-toolchain check for a new clone.

**Once per clone** (same venv as [Installation](#installation)):

```bash
python3 -m venv resnik-venv
. resnik-venv/bin/activate
pip install -e ".[dev]"             # pytest, coverage, xdist, linters
# pip install -e ".[test]"          # pytest + coverage only
```

Python 3.9 or newer is required; see [Requirements](#requirements).
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

**Prerequisites.** See [Requirements](#requirements) for `rustc` / `cc` and the
script’s sanitized `PATH`. You also need a git checkout (`git rev-parse` finds
the repo root) and the venv from [Installation](#installation) so
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

## Space Shuttle Flight Software History

The software Resnik's HAL/S heritage traces back to is the **Primary Avionics
Software System (PASS)** — the onboard flight software of the Space Shuttle, and
one of the most rigorously engineered programs of its era. Developed by IBM's
Federal Systems Division, PASS ran the orbiter's guidance, navigation, and
control in HAL/S, with its operating system (the Flight Control Operating
System) and low-level routines in assembly.

It flew on a set of five **General Purpose Computers (GPCs)** — IBM AP-101
machines from the System/4 Pi family, close cousins of the System/360
mainframe. Four ran PASS as a synchronized, voting redundant set, cross-checking
each other hundreds of times per second and voting out any computer that
disagreed. The fifth ran the **Backup Flight System (BFS)** — deliberately
developed by a separate contractor (Rockwell) so that a single software fault
could not take down primary and backup at once. That "diverse redundancy"
philosophy — guard against common-mode software failure, not just hardware
failure — is a recurring theme in safety-critical avionics. Because the AP-101's
memory couldn't hold everything at once, PASS was segmented into eight
phase/function memory loads streamed in from a tape-based mass-memory unit as
the mission moved through ascent, orbit, and entry. The AP-101S upgrade (first
flown on STS-37 in 1991) swapped core memory for semiconductor memory and
roughly doubled capacity.

Its most famous moment came before the program ever left the ground: the STS-1
launch scrub of April 10, 1981, when the backup computer refused to synchronize
with the four primaries — Jack Garman's *"Bug Heard 'Round the World."* PASS
flew for three decades, retiring with the Shuttle in 2011.

**Further reading** (in [`docs/Space_Shuttle_Flight_Software_History/`](docs/Space_Shuttle_Flight_Software_History/)):

- [The 1977 Approach and Landing Tests](docs/Space_Shuttle_Flight_Software_History/Enterprise-1977-Approach_And_Glide_Test.md)
  — the Enterprise glide trials that proved PASS digital fly-by-wire.
- [STS-1: "The Bug Heard 'Round the World"](docs/Space_Shuttle_Flight_Software_History/STS-1-The-Bug-Heard-Round-The-World.md)
  — the ~1-in-67 timing bug that scrubbed the first launch.
- [STS-9: Common-Mode Hardware Failure](docs/Space_Shuttle_Flight_Software_History/STS-9-Incident-Common_Mode_Hardware_Failure.md)
  — two GPCs lost to a loose-solder fault during entry, and redundancy
  management against a shared hardware defect.
- [PASS Operational Sequences (OPS)](docs/Space_Shuttle_Flight_Software_History/PASS-Operational_Sequences.md)
  — the OPS memory configurations that reconfigured the GPCs for each flight
  phase (ascent, orbit, entry).
- [PASS Crew Input Processing](docs/Space_Shuttle_Flight_Software_History/PASS-Crew-Input-Processing.md)
  — how the crew drove the system through 32-key keypads and the
  `OPS`/`SPEC`/`ITEM` display language.

## Compilers Namesake

Judith Resnik (April 5, 1949 – January 28, 1986) was an American electrical engineer, pilot, and NASA astronaut who became a trailblazer in space exploration.
Born in Akron, Ohio, to Jewish parents of Russian immigrant background—her father an optometrist and her mother a legal secretary—Resnik showed exceptional talent in math and science from a young age. She graduated as valedictorian from Firestone High School and earned a perfect math SAT score. She received her B.S. in electrical engineering from Carnegie Mellon University (then Carnegie Tech) in 1970 and later a Ph.D. in electrical engineering from the University of Maryland in 1977.
Her early career included work at RCA in missile and radar systems, biomedical engineering research at the National Institutes of Health, and engineering roles at Xerox. A licensed pilot, she was selected by NASA in January 1978 as part of Astronaut Group 8, the first class to include women.
Resnik became the second American woman in space (after Sally Ride) and the first Jewish woman of any nationality to fly in space. On her first mission, STS-41-D aboard Space Shuttle Discovery (August–September 1984), she served as a mission specialist, operating the robotic arm (Canadarm) and helping deploy satellites, logging about 145 hours in orbit total.
Tragically, she was killed along with the entire crew during her second mission, STS-51-L aboard Space Shuttle Challenger, when the vehicle exploded 73 seconds after launch on January 28, 1986.
Resnik was remembered as brilliant, determined, and multifaceted—she was also a classical pianist who enjoyed running, bicycling, and flying. Her legacy endures through memorials, including a lunar crater named after her, and she remains an inspiration for women and minorities in STEM and spaceflight.
