# Resnik HAL/S Compiler 🚀

An open-source compiler for the HAL/S aerospace programming language, translating legacy NASA HAL/S source code to safe, modern Rust.

**Version:** 1.0.0 (Modern Edition)  
**Status:** Lexer ✓ | Parser ✓ | Backend ✓ | Cross-compilation (ARM Cortex-M) ✓

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

This project is licensed under the GPL v3 license - see the [LICENSE](LICENSE) file for details.

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
```

### Cross-Compilation (Embedded Targets)

Resnik can emit either hosted Rust or bare-metal firmware, selected with the
`--target` flag on `resnik compile`:

| Profile            | Description                                                          |
| ------------------ | ------------------------------------------------------------------- |
| `host` (default)   | Hosted `std` Rust — runs on your local computer.                     |
| `cortex-m`         | `no_std` bare-metal firmware for ARM Cortex-M microcontrollers.     |

The `cortex-m` profile emits a `#![no_std]` / `#![no_main]` crate with a
`#[cortex_m_rt::entry]` reset entry point, a halting panic handler, and routes
HAL/S `WRITE` output to semihosting (swappable for RTT or UART downstream).

```bash
# Host Rust  
resnik compile program.hal

# Bare-metal firmware + a buildable Cargo project
resnik compile program.hal --target cortex-m --emit-cargo ./firmware
```

With `--emit-cargo`, the `cortex-m` profile scaffolds a complete, buildable
project:

```
firmware/
├── Cargo.toml            # cortex-m, cortex-m-rt, panic-halt, cortex-m-semihosting
├── .cargo/config.toml    # target triple, linker args, probe-rs/QEMU runner
├── memory.x              # MCU flash/RAM layout — edit for your chip
├── build.rs              # puts memory.x on the linker search path
└── src/main.rs           # generated firmware
```

To build and flash (on a machine with a Rust toolchain):

```bash
rustup target add thumbv7em-none-eabihf
resnik compile program.hal --target cortex-m --emit-cargo ./firmware
cd firmware
# 1. Edit memory.x to match your MCU's flash/RAM map.
# 2. Uncomment a runner in .cargo/config.toml
#    (probe-rs for hardware, QEMU for emulation).
cargo build --release        # cross-compiles for thumbv7em-none-eabihf
cargo run   --release        # flash / run via the configured runner
```

**Feature support on `no_std` targets.** Core control flow, arithmetic, `BIT`
operations, `IF`/`DO`, procedures, and the `ON ERROR` fault model all lower
cleanly to bare metal — the fault model unwinds via `Result` and halts via
`panic!` on an unhandled fault. Features whose lowering still depends on `std`
— `VECTOR`/`MATRIX` and math intrinsics, `SCHEDULE`/`TASK`, and `COMPOOL` —
emit a `compile_error!` on `cortex-m` with a clear message rather than failing
silently. These are on the embedded roadmap.

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
│   ├── types.py            # Type definitions
│   └── exceptions.py       # Custom exceptions
│
├── tests/                  # Professional test suite
│   ├── test_lexer/         # Lexer tests
│   ├── test_parser/        # Parser tests
│   ├── integration/        # End-to-end tests
│   └── fixtures/           # Test data
│   └── regression/         # Regression test
│
├── docs/                   # Documentation (MkDocs ready)
├── examples/               # Usage examples
├── pyproject.toml          # Single source of truth!
├── .pre-commit-config.yaml # Automated code quality
└── .github/workflows/      # CI/CD pipelines
```

## Testing

### Testing Resnik

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=resnik

# Run specific test file
pytest tests/test_lexer/test_tokenization.py

# Run with verbose output
pytest -v

# Run only fast tests
pytest -m "not slow"
```

## Compilers Namesake

Judith Resnik (April 5, 1949 – January 28, 1986) was an American electrical engineer, pilot, and NASA astronaut who became a trailblazer in space exploration.
Born in Akron, Ohio, to Jewish parents of Russian immigrant background—her father an optometrist and her mother a legal secretary—Resnik showed exceptional talent in math and science from a young age. She graduated as valedictorian from Firestone High School and earned a perfect math SAT score. She received her B.S. in electrical engineering from Carnegie Mellon University (then Carnegie Tech) in 1970 and later a Ph.D. in electrical engineering from the University of Maryland in 1977.
Her early career included work at RCA in missile and radar systems, biomedical engineering research at the National Institutes of Health, and engineering roles at Xerox. A licensed pilot, she was selected by NASA in January 1978 as part of Astronaut Group 8, the first class to include women.
Resnik became the second American woman in space (after Sally Ride) and the first Jewish woman of any nationality to fly in space. On her first mission, STS-41-D aboard Space Shuttle Discovery (August–September 1984), she served as a mission specialist, operating the robotic arm (Canadarm) and helping deploy satellites, logging about 145 hours in orbit total.
Tragically, she was killed along with the entire crew during her second mission, STS-51-L aboard Space Shuttle Challenger, when the vehicle exploded 73 seconds after launch on January 28, 1986.
Resnik was remembered as brilliant, determined, and multifaceted—she was also a classical pianist who enjoyed running, bicycling, and flying. Her legacy endures through memorials, including a lunar crater named after her, and she remains an inspiration for women and minorities in STEM and spaceflight.
