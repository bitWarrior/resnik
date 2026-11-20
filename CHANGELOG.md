# Changelog

All notable changes to Resnik are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Testing

- CI has a `rust` job with a pinned Rust toolchain and a virtual display.
  It runs the rustc-dependent and IDE (Tk) tests, failing if any is skipped,
  and the host regression scripts `run_regression.sh` and
  `run_exec_semantics.sh`.
- The regression scripts accept `RESNIK_BIN` / `RESNIK_PYTHON` in place of
  `resnik-venv`, and `RESNIK_REQUIRE_RUST=1` makes a missing `rustc` an error.

## [1.0.2]

### Documentation

- The README shows the Resnik logo, loaded from an absolute URL so that it
  also appears on the PyPI project page. No compiler or IDE behaviour
  changes from 1.0.1.

## [1.0.1]

First release on PyPI. No compiler or IDE behaviour changes from 1.0.0.

### Packaging

- Publish to PyPI from a GitHub Actions workflow using trusted publishing.
- The source distribution no longer bundles the two third-party HAL/S
  reference PDFs (see [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)); it
  shrinks from 23 MB to under 1 MB. The PDFs remain in the repository.
- The source distribution now includes `CHANGELOG.md` and `SECURITY.md`.

### Fixed

- `resnik.backend` was missing its package `__init__` in a fresh clone, so
  `from resnik.backend import RustCodeGenerator` failed.
- Two IDE tests no longer depend on a display or on the developer's
  `~/.cargo/bin`.

### Removed

- The pre-release maintainer checklist `OPEN-SOURCE-RELEASE.md`.

## [1.0.0]

First public release.

### Compiler

- Lexer and parser, with HAL/S semantic checks, for a working subset of the
  USA003088 Appendix B keyword set. The keyword-by-keyword status is in
  [docs/LANGUAGE_SUPPORT.md](docs/LANGUAGE_SUPPORT.md).
- Resnik extensions to the language: one-token `GOTO`, `DOWNTO`, optional
  `OF` in array declarations, infix `XOR` / `MOD`, parameterized
  `MACRO` / `MEND`, `TEMPLATE`, and `RESUME` in `ON ERROR` handlers.
- Rust back end for hosted (`std`) programs; `ON ERROR` lowers onto
  `Result`. `--emit-exe` builds the binary with `rustc`.
- Separate compilation with TEMPLIB-style templates (`--gen-tpl`,
  `--templib`) and cross-unit type checking.
- `EXTERNAL` foreign-function interface to C and assembly.

### Embedded targets

- `--target esp32-c3`: `no_std` firmware as a Cargo project
  (`--emit-cargo`), verified on hardware. `VECTOR` / `MATRIX` and the math
  built-ins (via `libm`), `COMPOOL` (via `critical-section`) and
  `SCHEDULE` / `TASK` (via a generated cyclic executive) are supported.
- `--target cortex-m`: crate scaffold with semihosting `WRITE`. This target
  is a stub and has not been verified on hardware.
- `WAIT`, `READ` and `CHARACTER` variables are not available on `no_std`
  targets and produce a `compile_error!`.

### IDE

- Desktop IDE (`resnik-ide`, `./start_ide.sh`) with HAL/S and Rust
  highlighting, toolchain and device probing, and Compile / Run /
  Flash & Run.

### Not yet implemented

- `UPDATE` blocks, `SCHEDULE … AFTER/AT/ON/UNTIL/WHILE`, `LOCK` /
  `LATCHED`, `READALL` / `FILE`, `WRITE` format controls, `SUBBIT` and
  others listed in [docs/LANGUAGE_SUPPORT.md](docs/LANGUAGE_SUPPORT.md).

[Unreleased]: https://github.com/bitWarrior/resnik/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/bitWarrior/resnik/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/bitWarrior/resnik/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/bitWarrior/resnik/releases/tag/v1.0.0
