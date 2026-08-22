# Resnik IDE

A minimal Tk desktop front end for the Resnik HAL/S compiler. It lives in
`ide/resnik_ide/` and ships with the compiler (`pip install -e .` from the
repository root installs both; the console script is `resnik-ide`).

```bash
./start_ide.sh [program.hal]    # from the repo root; picks resnik-venv automatically

# or, with the venv active:
pip install -e .                # once, to register the resnik-ide script
resnik-ide [program.hal]        # open a file (or nothing for an empty editor)
python -m resnik_ide            # same thing without the script
```

Requires Tk (`python3-tk` on Debian/Ubuntu; bundled with python.org builds).

## What it does (Phases 1–3)

| Control | Action |
| ------- | ------ |
| File → New / Open… / Save / Save As… | Edit `.hal` / `.hs` files; unsaved-changes guard on close/open. |
| **Semantic Check** (F7) | Saves, then runs lexer + parser in-process via `resnik.driver.check`. HAL/S semantic analysis lives in the parser, so a clean parse is a semantically valid unit. Errors are listed in the log — click one to jump to its line; the line is highlighted in the editor until the next edit. |
| **Compile** (F5) | Saves, then runs the full `resnik compile` pipeline via `resnik.driver.generate` with the current options (everything in *Options…*, including `--emit-exe` if you set it there). Writes `<file>.rs` next to the source. The log gets a "View generated Rust" link. |
| **Build and Run** (F9) | Host target: compiles with `--emit-exe` forced for this run (saved options are untouched), then runs the executable through the process runner — its stdout/stderr stream into the log, and the exit code is reported. Embedded targets: compiles (needs `--emit-cargo` set) and then hands off to **Flash & Run**. |
| **Options…** (Ctrl+,) / Run → Compiler Options… | Dialog with every `resnik compile` flag — *Target & Build* (`--target`, `--emit-exe`, `--opt-level`, `--emit-cargo`, `--build`), *Output* (`--rust`, `--tokens`, `--ast`), *Linking* (`-L`, `-l`, `--link-obj`), *Templates* (`--gen-tpl`, `--templib`) and *Extra* (free-form arguments, validated by the real CLI parser when you run). A live **Equivalent command** line shows the exact `resnik compile …` invocation (Copy button). Relative paths are resolved against the source file's directory. The toolbar's target combobox is a shortcut to the same setting. |
| Settings | Saved automatically to `~/.config/resnik/ide.json` (`$XDG_CONFIG_HOME`, `%APPDATA%` on Windows, or `$RESNIK_IDE_CONFIG` to override) and restored on the next start, along with the window geometry. *Reset to defaults* in the dialog clears them. |
| **Target panel** (right) | *Toolchain* checklist for the selected profile — rustc/cargo/rustup, the target triple (`rustup target add …`), `rust-src`, `espflash` / `probe-rs` — each unmet row has an **Install** button that streams the install command into the log; *Device* — serial port combobox (`/dev/ttyACM*`, `/dev/ttyUSB*`, macOS `cu.*`, or `pyserial` if installed) and the probe-rs chip name; *Actions* — **Build** (`cargo build --release` in the `--emit-cargo` project), **Flash & Run**, **Monitor**, **Run on host**, **Stop**. Port and chip persist in `ide.json`. |
| External processes | Everything that is not the in-process compiler (rustc via `--emit-exe` excepted) runs through a streamed, cancellable runner: the command line is echoed (`$ …`), stdout/stderr are interleaved live, colours/progress bars are disabled (`NO_COLOR`, `CARGO_TERM_COLOR=never`), and **Stop** kills the whole process group (needed for the serial monitor, which never exits). One process at a time. |
| Log pane | Timestamped transcript of everything the IDE does, including anything the compiler prints. Right-click: Clear / Copy all / Save log…. |
| Status bar | File path (`*` when dirty), caret `Ln, Col`, last result. |

An untitled buffer is checked/compiled from a private temp directory
(`untitled.hal`), so you can paste and check without saving first.

### Hardware workflow

| Target | Flash & Run | Monitor | Notes |
| ------ | ----------- | ------- | ----- |
| `host` | **Build and Run** (toolbar) compiles with `--emit-exe` and runs the binary; *Run on host* re-runs the last built executable (or `target/release/<crate>` of the Cargo project) | — | output streams into the log |
| `esp32-c3` | `cargo run --release [-- --port <port>]` in the `--emit-cargo` project (its `.cargo/config.toml` runner is `espflash flash --monitor`) | `espflash monitor [--port <port>]` | needs `riscv32imc-unknown-none-elf`, `rust-src`, `espflash` — all installable from the panel |
| `cortex-m` | `cargo build --release`, then `probe-rs run --chip <chip> <elf>` | `probe-rs attach --chip <chip> <elf>` | edit the scaffold's `memory.x` for your MCU first; needs `thumbv7em-none-eabihf` and `probe-rs` |

Pre-flight: Flash/Build refuse to start while a requirement row is ✗ and say
which one. Set the Cargo project dir under *Options… → Target & Build →
--emit-cargo* and Compile once so the project exists.

**Tested vs. not tested.** The runner, toolchain probing/parsing, requirement
logic, pre-flight, and the panel are covered by tests against fake
processes/tool output, and the host Run path is exercised end-to-end with
`rustc`. The `espflash` / `probe-rs` invocations themselves have **not** been
run against real hardware in this tree (no board / tools on the development
machine) — the commands mirror README §"Hello World on ESP32-C3". One known
open question: the IDE runs tools without a TTY (stdin is `/dev/null`); if
your `espflash` version refuses to open its interactive monitor in that
situation, it needs `--non-interactive` appended to the Monitor / runner
command — please report the exact message and version so that can be wired
in.

## Layout

```
ide/
├── README.md
├── resnik_ide/
│   ├── app.py        # ResnikIDE(tk.Tk): menu, toolbar, panes, jobs, main()
│   ├── editor.py     # Editor: tk.Text + line-number gutter + error marks + goto
│   ├── logview.py    # LogView widget + thread-safe LogSink
│   ├── reporter.py   # LogReporter: resnik.driver.Reporter → log lines
│   ├── dispatch.py   # Dispatcher (main-thread queue) + Job (worker thread)
│   ├── options.py    # IdeCompileSettings: compiler args ⇄ JSON ⇄ CLI argv (via resnik.cli.parse_compile_args)
│   ├── options_dialog.py  # OptionsDialog (ttk Notebook) with live equivalent-command preview
│   ├── settings.py   # ide.json load/save (atomic, tolerant)
│   ├── runner.py     # ProcessRunner: streamed Popen, process-group cancel, quiet env
│   ├── toolchain.py  # rustup/cargo/rustc/espflash/probe-rs probe; per-target requirement rows
│   ├── devices.py    # serial-port / debug-probe discovery
│   └── target_panel.py    # Target panel widget (checklist, device, actions)
└── tests/            # pytest; Tk tests skip themselves when no display is available
```

The compiler side of this — `src/resnik/driver.py` — is the reusable
pipeline (`check()` / `generate()` / `CompileOptions` / `Reporter`) that both
the CLI (`resnik compile`) and the IDE call. See `docs/Resnik IDE/RESNIK_IDE_PLAN.md`
for the roadmap (compiler-arguments dialog, toolchain/hardware panel, …).
