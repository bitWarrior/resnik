# Design notes: why Python in front, Rust behind

## Why Python for the Lexer / Parser?

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
  (see [Embedded targets](EMBEDDED_TARGETS.md)). A single
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
