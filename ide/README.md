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

## What it does (Phase 1)

| Control | Action |
| ------- | ------ |
| File → New / Open… / Save / Save As… | Edit `.hal` / `.hs` files; unsaved-changes guard on close/open. |
| **Semantic Check** (F7) | Saves, then runs lexer + parser in-process via `resnik.driver.check`. HAL/S semantic analysis lives in the parser, so a clean parse is a semantically valid unit. Errors are listed in the log — click one to jump to its line; the line is highlighted in the editor until the next edit. |
| **Compile** (F5) | Saves, then runs the full `resnik compile` pipeline via `resnik.driver.generate` with the current options (`--target`, `--emit-exe`). Writes `<file>.rs` next to the source; with *Build executable* ticked, runs `rustc`. The log gets a "View generated Rust" link. |
| Log pane | Timestamped transcript of everything the IDE does, including anything the compiler prints. Right-click: Clear / Copy all / Save log…. |
| Status bar | File path (`*` when dirty), caret `Ln, Col`, last result. |

An untitled buffer is checked/compiled from a private temp directory
(`untitled.hal`), so you can paste and check without saving first.

## Layout

```
ide/
├── README.md
├── resnik_ide/
│   ├── app.py        # ResnikIDE(tk.Tk): menu, toolbar, panes, jobs, main()
│   ├── editor.py     # Editor: tk.Text + line-number gutter + error marks + goto
│   ├── logview.py    # LogView widget + thread-safe LogSink
│   ├── reporter.py   # LogReporter: resnik.driver.Reporter → log lines
│   └── dispatch.py   # Dispatcher (main-thread queue) + Job (worker thread)
└── tests/            # pytest; Tk tests skip themselves when no display is available
```

The compiler side of this — `src/resnik/driver.py` — is the reusable
pipeline (`check()` / `generate()` / `CompileOptions` / `Reporter`) that both
the CLI (`resnik compile`) and the IDE call. See `docs/Resnik IDE/RESNIK_IDE_PLAN.md`
for the roadmap (compiler-arguments dialog, toolchain/hardware panel, …).
