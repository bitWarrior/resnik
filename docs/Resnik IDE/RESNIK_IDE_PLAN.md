# Resnik IDE — Implementation Plan

A minimal desktop IDE for the Resnik HAL/S compiler.

**Status (2026-08-21):** Phase 0 (driver extraction) and Phase 1 (minimal
IDE) are implemented — see `ide/README.md`. Phases 2–4 are open.

## 1. Requirements (as given)

| # | Requirement | Plan section |
| - | ----------- | ------------ |
| R1 | Load, edit, save HAL/S source | §5 Phase 1 — Editor |
| R2 | "Semantic Check" and "Compile" buttons | §5 Phase 0 + 1 — Driver + buttons |
| R3 | Log window capturing GUI output | §5 Phase 1 — Log pane |
| R4 | Set compiler arguments | §5 Phase 2 — Options |
| R5 | rustup/cargo integration: install targets, load & execute on connected hardware | §5 Phase 3 — Toolchain & Devices |

(Button label: "Semantic Check" — the request spelled it "Symantic"; the
standard term is used throughout.)

## 2. Decisions

| Decision | Choice | Why |
| -------- | ------ | --- |
| GUI toolkit | **tkinter / ttk** (stdlib) | Already importable in `resnik-venv` (Tk 8.6); zero new runtime dependency; enough for a single-file editor + log + buttons. PySide6/Qt is a later option if the IDE outgrows Tk. Only system requirement: `python3-tk` on Debian/Ubuntu. |
| Where it lives | Repo-root `ide/` directory: package `ide/resnik_ide/`, tests `ide/tests/`, console script `resnik-ide` | Keeps the IDE visibly separate from the compiler tree while still shipping in the same wheel (`pyproject.toml` lists both packages). Imports the compiler in-process. |
| How it runs the compiler | **In-process** for lex/parse/codegen via a new `resnik.driver` module; **subprocess (streamed)** for every external tool (`rustc`, `cargo`, `rustup`, `espflash`, `probe-rs`) | In-process gives structured diagnostics (line/col → click-to-jump) and no Rich/ANSI noise. External tools are long-running (a serial monitor never exits) so they must stream to the log and be cancellable. |
| Concurrency | Worker `threading.Thread` + `queue.Queue` drained by `root.after(50, …)` | Tk is single-threaded; never touch widgets off the main thread. |
| Settings | JSON at `~/.config/resnik/ide.json` (stdlib `pathlib`; `%APPDATA%` on Windows) | Compiler args, last file, MRU, window geometry, selected port/target. |
| Hardware flow | Reuse the `--emit-cargo` scaffold and its `.cargo/config.toml` runner; IDE runs `cargo build` / `cargo run` in the scaffolded project | Flash/monitor logic already lives in the generated crate (`espflash flash --monitor`, `probe-rs run --chip …`). The IDE adds toolchain checks, port selection, streaming and Stop. |

## 3. Non-goals (v1)

- Multiple open files / tabs, project tree, split views.
- Autocomplete, LSP, go-to-definition, refactoring, debugger.
- Editing `memory.x` / `Cargo.toml` inside the IDE (an "Open project folder" button is enough).
- Xtensa ESP32/S2/S3 (`espup`) — same limitation as the compiler today.
- Windows COM-port enumeration beyond a text field (Linux/macOS glob + `espflash`/`probe-rs` listing only).
- Themes, plugins, i18n.

## 4. Architecture

```
src/resnik/driver.py     # Phase 0 ✓ reusable pipeline (used by cli.py AND the IDE)
tests/test_driver/       # Phase 0 ✓ driver + CLI-wrapper tests
ide/
├── README.md
├── resnik_ide/
│   ├── app.py           # Phase 1 ✓ main(); ResnikIDE(tk.Tk): menu, toolbar, panes, jobs
│   ├── editor.py        # Phase 1 ✓ Editor: Text + line-number gutter + error marks + goto
│   ├── logview.py       # Phase 1 ✓ LogView (tags, clickable lines) + thread-safe LogSink
│   ├── reporter.py      # Phase 1 ✓ LogReporter: driver.Reporter → log lines
│   ├── dispatch.py      # Phase 1 ✓ Dispatcher (main-thread queue) + Job (worker thread)
│   ├── options.py       # Phase 2   CompileOptions ⇄ Options dialog ⇄ JSON; to_cli_args()
│   ├── settings.py      # Phase 2   load/save ide.json
│   ├── runner.py        # Phase 3   ProcessRunner: streamed Popen, cancel (process group), NO_COLOR env
│   ├── toolchain.py     # Phase 3   rustup/cargo/espflash/probe-rs discovery + install commands
│   └── devices.py       # Phase 3   serial port discovery (glob + espflash/probe-rs list)
└── tests/               # IDE tests; Tk tests skip themselves when no display is available
```

Data flow for the two buttons:

```
[Semantic Check] → save buffer → driver.check(path, templib) ─► Diagnostics ─► log + jump-to-line
[Compile]        → save buffer → driver.generate(opts)       ─► .rs (+ cargo scaffold, + .tpl)
                              └─► if opts.emit_exe / opts.build: ProcessRunner(rustc | cargo build) ─► streamed log
```

### 4.1 `resnik.driver` (the enabling refactor)

Today `cli.compile()` (`src/resnik/cli.py:273-480`) owns the pipeline, prints
with Rich, and calls `sys.exit`. The GUI cannot reuse that. Extract:

```python
@dataclass
class Diagnostic:  phase: str; message: str; line: int; column: int; filename: str; severity: str = "error"

@dataclass
class CheckResult: ok: bool; token_count: int; diagnostics: list[Diagnostic]; ast: Optional[ASTNode]

@dataclass
class CompileOptions:  # mirrors every `resnik compile` flag
    input_file: Path; rust: Optional[Path]; tokens: Optional[Path]; ast: Optional[Path]
    target: str = "host"; emit_exe: bool = False; opt_level: str = "2"
    link_search: list[Path]; link_lib: list[str]; link_obj: list[Path]
    emit_cargo: Optional[Path]; build: bool = False
    gen_tpl: Optional[Path]; templib: list[Path]

def check(input_file, templib=()) -> CheckResult          # lexer + parser (semantic checks are in the parser)
def generate(opts, log: Callable[[str], None]) -> GenerateResult   # check + codegen + write .rs + gen-tpl + cargo scaffold
```

- `cli.compile` becomes a thin Rich-formatting wrapper over `driver`.
  Behaviour must be identical: verify with `python -m pytest` and the
  regression suite `./tests/regression/run_regression.sh` (compiles/runs ~60 examples).
- `Diagnostic` is built from `ResnikError.line/column/filename`
  (`exceptions.py:8-25`), so the GUI gets structured locations without
  parsing text.
- `generate()` does **not** invoke rustc/cargo itself when called from the
  IDE; it returns the paths, and the IDE streams the external step through
  `ProcessRunner` (so the user sees rustc output live and can cancel). The
  CLI keeps using `compile_to_executable` / `emit_cargo_project(build=True)`
  as now.
- Open question to verify in Phase 0: line numbers reported for source that
  went through `D INCLUDE TEMPLATE` / `MACRO` / `REPLACE` expansion — do they
  map back to the user's file? If not, jump-to-line will be off for those
  files; log it and decide whether to fix in the lexer.

### 4.2 `ProcessRunner`

- `run(argv, cwd, env, on_line, on_exit)`; `Popen(..., stdout=PIPE, stderr=STDOUT, text=True, bufsize=1, stdin=DEVNULL, start_new_session=True)`.
- Reader thread pushes `(stream, line)` into the log queue; `cancel()` does `os.killpg(pgid, SIGTERM)` then `SIGKILL` after a grace period (Windows: `CREATE_NEW_PROCESS_GROUP` + `taskkill /T`).
- Env: `CARGO_TERM_COLOR=never`, `NO_COLOR=1`, `RUST_BACKTRACE=1`; strip residual ANSI with a regex.
- Only one external process at a time; toolbar buttons disable while running; a **Stop** button is always enabled during a run (needed for the serial monitor, which never exits).
- Every command is echoed to the log first (`$ cargo build --release`) so the log is a reproducible transcript.

### 4.3 Toolchain & devices (R5)

`toolchain.py`
- `probe()` → `ToolchainStatus`: rustup/cargo/rustc versions (`rustup show`, `cargo --version`), installed targets (`rustup target list --installed`), `rust-src` component, presence of `espflash`, `probe-rs` on PATH.
- Per target profile (from `resnik.backend.target_spec.SPECS`) a **requirements list**: `host` → rustc; `cortex-m` → triple `thumbv7em-none-eabihf` + `probe-rs` (or QEMU); `esp32-c3` → triple `riscv32imc-unknown-none-elf` + `rust-src` + `espflash`.
- Actions (all via `ProcessRunner`): `rustup target add <triple>`, `rustup component add rust-src`, `cargo install espflash`, `cargo install probe-rs-tools`, `rustup update stable`.

`devices.py`
- Port list: glob `/dev/ttyACM*`, `/dev/ttyUSB*`, `/dev/cu.usb*`; if `pyserial` happens to be installed use `serial.tools.list_ports`; plus `espflash board-info` / `probe-rs list` output when those tools exist. "Refresh" button; free-text override.

Hardware workflow in the UI (a **Target** panel beside the Options):

1. Target profile dropdown (host / cortex-m / esp32-c3) — same value as compiler arg `--target`.
2. Pre-flight: green/red rows for each requirement with an "Install" button next to any red one.
3. Port dropdown + Refresh; for cortex-m a "chip" field (`probe-rs run --chip …`).
4. Buttons: **Build** (`cargo build --release` in the emit-cargo dir), **Flash & Run** (`cargo run --release`, which invokes the scaffold's runner; for esp32-c3 pass the port via `--port` / `ESPFLASH_PORT` — confirm against the installed `espflash` version), **Monitor** (`espflash monitor --port …`, or `probe-rs attach`), **Stop**.
5. `host` target: **Run** executes the built binary and streams its stdout to the log.

## 5. Phases

Each phase ends green on `python -m pytest` and leaves the CLI untouched
(regression suite where codegen/CLI is affected).

### Phase 0 — Driver extraction (prerequisite, no UI) — ✓ done
- Add `src/resnik/driver.py` (§4.1); rewrite `cli.compile` on top of it.
- Tests: `tests/test_ide/test_driver.py` — `check()` on a valid fixture → `ok`, on a semantic-error fixture (e.g. duplicate declare, VECTOR(4)→VECTOR(3) arity) → `Diagnostic` with correct line; `generate()` writes `.rs`, honours `--rust`, `--gen-tpl`, `--emit-cargo` (no build).
- Gate: pytest + `run_regression.sh` both pass with identical output.

### Phase 1 — Minimal IDE (R1, R2, R3) — ✓ done (syntax highlighting deferred to Phase 4 as planned; `--emit-exe` runs rustc blocking inside the driver until Phase 3's ProcessRunner streams it)
- `resnik-ide` entry point (`pyproject.toml [project.scripts]`), `python -m resnik.ide` also works.
- Window: menu (File: New/Open/Save/Save As/Recent/Exit; Run: Semantic Check/Compile/Stop; Help: About), toolbar with the two buttons, **vertical PanedWindow**: editor top, log bottom; status bar (file path, dirty `*`, Ln/Col, target).
- Editor: `Text` with undo, monospace font, line-number gutter, tab → spaces, dirty tracking, unsaved-changes prompt on close/open, `.hal` file dialogs, drag-free (no DnD in v1).
- Log: read-only, timestamped lines, tags `info/cmd/ok/warn/error`, context menu Clear / Copy / Save log…; everything the GUI does is logged (file opened, check started, N tokens, N errors, command lines, exit codes). Worker `sys.stdout/err` are also redirected into the log as a safety net.
- Semantic Check: saves (untitled → temp file in a temp dir), calls `driver.check`, logs each diagnostic as a clickable line → moves the cursor and highlights the line; error lines get a red gutter/background tag until the next edit.
- Compile: `driver.generate(opts)` with current options; then optional rustc (`--emit-exe`) via `ProcessRunner`; "Open generated Rust" button shows the `.rs` in a read-only viewer window.
- Gate: open `docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal`, Semantic Check → 0 errors; introduce an error → jump-to-line works; Compile with `--emit-exe` → executable built, log shows rustc transcript.

### Phase 2 — Compiler arguments (R4)
- `CompileOptions` dialog (ttk Notebook: *Output* — rust/tokens/ast paths; *Build* — emit-exe, opt-level, emit-cargo dir, build; *Target* — target profile; *Linking* — -L / -l / --link-obj lists; *Templates* — gen-tpl, templib list; *Extra* — raw extra args appended verbatim).
- Live "Equivalent command" line: `resnik compile foo.hal --target esp32-c3 --emit-cargo ./firmware` (from `CompileOptions.to_cli_args()`), so anything done in the IDE is reproducible from the shell.
- Persist in `ide.json`; restore on start; "Reset to defaults".
- Tests: options ⇄ JSON round-trip; `to_cli_args()` matches Click's parsing (feed it back through `click.testing.CliRunner` in dry mode / or compare against `driver` parse).

### Phase 3 — Toolchain & hardware (R5)
- `runner.py`, `toolchain.py`, `devices.py` (§4.2–4.3), Target panel, Build / Flash & Run / Monitor / Stop / Run(host).
- Pre-flight checks block Flash with a clear log message ("target riscv32imc-unknown-none-elf not installed — click Install") instead of a cryptic cargo error.
- Tests: `ProcessRunner` against `python -c` scripts (streaming order, exit code, cancel of a sleeping process kills the group); `toolchain.parse_installed_targets()` on canned `rustup` output; `devices.find_ports()` on a fake `/dev` tree. Hardware steps are manual: ESP32-C3 DevKit hello-world per README §"Hello World on ESP32-C3".

### Phase 4 — Polish & packaging
- Syntax highlighting driven by the real `Lexer` (keywords/strings/comments/numbers as Text tags, re-run debounced on edit; cheap because `Lexer.tokenize()` is already fast for these file sizes).
- Recent files, font size +/-, "Open project folder", remember window geometry, keyboard shortcuts (F7 check, F5 compile, Ctrl+S/O/N, Esc stop).
- Optional: "Semantic Check (target-aware)" that also dry-runs codegen so `compile_error!`-producing features on `no_std` targets (VECTOR/MATRIX, SCHEDULE/TASK, COMPOOL) are reported before cargo runs.
- Docs: README "IDE" section, `docs/Resnik Users Guide` chapter with screenshots, `python3-tk` note; `misc/REQUIREMENTS_TRACEABILITY.md` rows for R1–R5.
- CI: headless tests only; Tk smoke test (`ResnikIDE()` constructs, buttons exist) gated on `DISPLAY`/`xvfb-run`, marked `slow`.

## 6. Risks / things to verify early

| Risk | Mitigation |
| ---- | ---------- |
| Line numbers after preprocessor expansion (templates, MACRO, REPLACE) don't match the editor | Check in Phase 0; fall back to "no jump" for diagnostics without a reliable line. |
| `cargo run --release` with `espflash --monitor` never exits and may want a TTY | `stdin=DEVNULL`, process-group kill via Stop; if monitor misbehaves without a TTY, run `espflash flash` then `espflash monitor` as two steps. |
| Rich console output inside the driver | Driver takes a plain `log(str)` callback; Rich lives only in `cli.py`. |
| Tk not installed (`No module named _tkinter`) | Clear error at `resnik-ide` startup naming the `python3-tk` package. |
| Cross-platform process kill | `start_new_session`/`killpg` on POSIX, `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F` on Windows. |
| Semantic errors are raised from inside the parser | `check()` collects `parser.errors` + `lexer.errors` exactly as `cli.compile` does today; no new analysis pass is needed for v1. |

## 7. Rough size

| Phase | New code (approx.) | Notes |
| ----- | ------------------ | ----- |
| 0 | ~250 lines + tests | Mostly moving code out of `cli.py` |
| 1 | ~600 lines | Editor, log, wiring |
| 2 | ~300 lines | Dialog + persistence |
| 3 | ~500 lines | Runner, toolchain, devices, panel |
| 4 | ~300 lines + docs | Highlighting, polish |

Suggested first milestone: Phases 0–1 (a usable check/compile editor),
then 2, then 3 once an ESP32-C3 board is on hand for manual verification.
