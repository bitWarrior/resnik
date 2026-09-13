# no_std Roadmap — closing the embedded gaps

Plan for resolving the four constraints that limit HAL/S programs on
embedded targets (ESP32-C3 / Cortex-M), found while building the XIAO
INS-over-Bluetooth project. Status (2026-09-12): **items 0 and 4 implemented** (embedded build tier +
scaffold-once with `src/shim.rs`); item 3 implemented (volatile EXTERNAL + CHARACTER FFI check); items 1, 2 open.

Ordering is by leverage: item 4 unblocks project work immediately and is
small; 3 is small; 1 is medium; 2 is the big one and is split.

Every item ships with the standard proof: emission tests in pytest
(mutation-kill verified pre-fix), the regression suite, plus a **new
build-only embedded regression tier** (item 0) and a hardware smoke test on
the XIAO ESP32-C3 where behavior changes.

---

## Item 0 (enabler) — build-only embedded regression tier — ✓ done
(`tests/regression/run_embedded_build.sh`: 15 esp32-c3 cases + 1 cortex-m)

There is no automated check that generated firmware *compiles* for embedded
targets; the guard rails are emission-string tests only.

- Add `tests/regression/run_embedded_build.sh`: for each example in a
  curated list, `resnik compile --target esp32-c3 --emit-cargo` into a temp
  dir and `cargo build --release` (no flashing, no board). Also one
  `cortex-m` build.
- First run needs the network for crates.io; later runs hit the cargo
  cache. Document that; skip cleanly when `cargo`/targets are missing.
- Size: small. Non-goals: running on hardware in CI; Xtensa.

## Item 4 — scaffold-once — ✓ done (create-if-absent scaffold, `kept existing`
reporting, `--force-scaffold`, `src/shim.rs` + `mod shim;`)

**Today:** `emit_cargo_project` unconditionally rewrites `Cargo.toml`,
`.cargo/config.toml`, `rust-toolchain.toml`, `memory.x`, `build.rs`
(codegen.py:3860–4026), so hand-added dependencies (a BLE/IMU shim) are
lost on every compile.

- Only `src/main.rs` is generated output; write it always. Every other
  scaffold file becomes **create-if-absent** (report "kept existing
  Cargo.toml" via the reporter). Add `--force-scaffold` to regenerate.
- Give user code a stable home: scaffold once creates `src/shim.rs`
  (documented stub, `#![allow(dead_code)]`, never overwritten) and the
  generated embedded `main.rs` always emits `mod shim;`. HAL/S `EXTERNAL`
  symbols are satisfied by `#[no_mangle] pub extern "C" fn …` in the shim —
  no C toolchain needed.
- CLI/IDE surface unchanged (`--emit-cargo` becomes idempotent-safe);
  update docs/EMBEDDED_TARGETS.md and the README trip-ups table.
- Tests: scaffold → hand-edit Cargo.toml → compile again → edits preserved,
  main.rs regenerated, shim.rs untouched; `--force-scaffold` regenerates;
  host scaffold unaffected.
- Size: small. Non-goals: Cargo.toml *merging* (absent-or-keep only);
  workspace layouts.

## Item 3 — `EXTERNAL` correctness — ✓ done (volatile `static mut` access —
writes previously did not even compile; CHARACTER-over-FFI rejected at
semantic analysis)

**Today:** reads/writes of `DECLARE X … EXTERNAL` lower to plain static
access (codegen.py:2559, `_extern_vars`), so a register or shim-updated
value can be cached by the optimizer — the README already flags this.

- Lower every EXTERNAL-variable access through
  `core::ptr::read_volatile(core::ptr::addr_of!(X))` /
  `write_volatile` (always — HAL/S has no volatile attribute, EXTERNAL is
  the contract that "something else writes this"; cost is negligible).
- Define the supported FFI type matrix (INTEGER→i32, SCALAR→f32/f64,
  BOOLEAN→bool, BIT(n)→uN) and emit a **semantic-analysis error** when an
  EXTERNAL signature uses an unsupported type (CHARACTER, VECTOR/MATRIX on
  embedded) instead of generating code that fails cryptically in rustc.
- Tests: emission asserts `read_volatile`/`write_volatile` (mutation-kill:
  stash → plain access reappears); diagnostics for a `CHARACTER EXTERNAL`;
  hardware smoke: shim increments a counter variable, HAL/S loop observes
  it change.
- Size: small-medium. Non-goals: memory-mapped-address syntax (`AT 0x…`),
  DMA fences/ordering guarantees beyond volatile.

## Item 1 — VECTOR/MATRIX and math intrinsics on no_std

**Today:** the generated `hal_rt` prelude (codegen.py:549) implements
`Vector<const N>([f32; N])` / matrices with core-only arrays — but float
math uses inherent std methods (`.sqrt()`, SIN/COS/… mapping at
codegen.py:856), `_uses_runtime` (codegen.py:457) trips the
`compile_error!` guard for any of it on embedded.

- Route float math through **`libm`** (pure-Rust, no_std) when the target
  is embedded: emit a `FloatExt` trait in the prelude (`fn sqrt(self) ->
  f32 { libm::sqrtf(self) }` …) so call sites stay `(x).sqrt()` and the
  host output is byte-identical; add `libm` to the embedded scaffold
  Cargo.toml (and the single-file `--emit-exe` path simply keeps std).
- Audit the prelude for remaining std leakage on the embedded path
  (`eprintln!` in stubs, any String/Vec formatting in vector WRITE);
  replace with `core::fmt` (esp-println/semihosting already take core fmt).
  Known instance found during item-0 curation: CHARACTER handling emits
  `format!` (needs alloc) — `string_trunc` fails to build on esp32-c3; add
  CHARACTER-on-no_std to this audit (or reject with a clear semantic error
  per item 3's type-matrix approach).
- Drop VECTOR/MATRIX + intrinsics from `_emit_embedded_guard`; keep the
  guard for SCHEDULE/TASK/COMPOOL until item 2.
- Tests: emission (libm-backed trait present iff embedded; host unchanged
  — regression suite proves it); embedded build tier compiles
  `kalman_filter`, `linear_algebra_test`, `tensor_math`; hardware smoke:
  vector math result printed on the XIAO matches the host run digit-for-digit.
- Size: medium. Non-goals: f64 on C3 beyond what f32 covers today; SIMD;
  fixed-point.

## Item 2 — SCHEDULE / TASK / COMPOOL on bare metal

**Today:** SCHEDULE/TASK lower to `std::thread`, COMPOOL to
`std::sync::Mutex` — both guarded off on embedded. Two phases, because the
first is mechanical and the second needs a design decision.

**2a — COMPOOL via critical sections (medium).** Lower compool globals to
`static X: critical_section::Mutex<RefCell<T>>` with the target's
critical-section provider (esp-hal and cortex-m both ship one); accessors
become `critical_section::with(|cs| …)`. Same observable semantics as the
host Mutex for a single-core superloop. Tests: emission + embedded build +
hardware smoke (two "processes" — see 2b — sharing a compool).

**2b — cyclic-executive scheduler (large, own design doc first).** HAL/S's
real-time model on a chip with no OS:
- Generate a **superloop executive**: a static task table (fn pointer,
  period, priority, next-due tick) from `SCHEDULE T EVERY n PRIORITY(p)`;
  the main loop picks the highest-priority due task each tick — cooperative,
  run-to-completion, no preemption.
- Time base per target spec: esp-hal SystemTimer on C3, SysTick on
  Cortex-M; `WAIT n` becomes a timer wait inside the current task
  (documented as blocking the executive in v1).
- Scope the accepted subset explicitly: `SCHEDULE … EVERY/PRIORITY`,
  `TERMINATE`, `WAIT`; **defer** `AFTER/AT/ON/UNTIL/WHILE` variants (they
  are unparsed today anyway — see docs/LANGUAGE_SUPPORT.md), signal/event
  waits, and preemption (an RTIC/embassy backend is a separate,
  opt-in profile later).
- Deviations from PASS semantics (cooperative vs priority-preemptive) get a
  documented table — never silent.
- Tests: executive unit tests host-side (the same generated table can run
  under std), embedded builds, hardware demo: two SCHEDULEd tasks at
  different rates sharing a COMPOOL, timing verified from serial output.

---

## Suggested sequence and sizing

| Step | Item | Size | Unblocks |
| ---- | ---- | ---- | -------- |
| 1 | 0 build-only embedded regression | S | proof for everything below |
| 2 | 4 scaffold-once + shim.rs | S | the XIAO BLE/INS project immediately |
| 3 | 3 volatile EXTERNAL + FFI type checks | S–M | trustworthy shims |
| 4 | 1 libm-backed VECTOR/MATRIX | M | real INS math in HAL/S |
| 5 | 2a COMPOOL via critical-section | M | shared state between loops |
| 6 | 2b cyclic executive (design doc → impl) | L | SCHEDULE/TASK on hardware |

Steps 1–4 are each a normal work-item with the usual pytest +
mutation-kill + regression + hardware-smoke proof; 2b starts with a design
review before any code.
