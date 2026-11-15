# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |
| < 1.0   | No        |

Only the latest release on `main` receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately by either route:

- GitHub: the repository's **Security** tab → **Report a vulnerability**
  (private vulnerability reporting).
- Email: <contact.bitWarrior@proton.me> with `[resnik security]` in the
  subject.

Include the Resnik version (`resnik --version`), the target
(`std`, `cortex-m`, `esp32-c3`), a minimal HAL/S source or command line that
reproduces the problem, and what you expected to happen.

Resnik is maintained by one person on a best-effort basis. You can expect an
acknowledgement within 7 days and a status update once the report has been
assessed. Please allow time for a fix before disclosing publicly; credit is
given in the changelog unless you ask otherwise.

## Scope

In scope:

- The compiler (`src/resnik`) accepting crafted HAL/S source, template
  libraries (`--templib`) or command-line arguments and, as a result,
  writing files outside the requested output location, executing commands
  other than the documented toolchain invocations, or emitting Rust that
  contains `unsafe` behaviour the HAL/S source did not ask for.
- The IDE (`ide/resnik_ide`) doing the same when opening a file or project.
- This repository's CI and release process.

Out of scope:

- Vulnerabilities in `rustc`, `cargo`, `espflash`, `probe-rs` or other
  third-party tools and crates — report those upstream.
- Crashes or incorrect diagnostics on malformed input with no security
  impact — these are ordinary bugs; please open a normal issue.

## What Resnik executes

Know this before running Resnik on source you did not write:

- `resnik compile --emit-exe` runs `rustc` on the generated Rust, and the
  IDE's Run / Flash & Run actions additionally run `cargo` and `espflash`
  and then **execute or flash the resulting program**. None of this is
  sandboxed.
- `EXTERNAL` declarations link against C or assembly objects that you
  supply; that code runs with your privileges.
- Cargo projects produced by `--emit-cargo` download crates from crates.io
  when built.

Treat compiling and running untrusted HAL/S the way you would treat running
any untrusted program: do it in a container or virtual machine.

## No warranty

Resnik and the Rust code it generates are provided without warranty of any
kind (GPL-3.0-or-later, sections 15 and 16). Resnik is not qualified for
flight, safety-critical or life-critical use.
