# Design: SCHEDULE / TASK on bare metal — a generated cyclic executive

no_std roadmap item **2b**. Status: **implemented 2026-09-12** (open
questions resolved in §6; semantics harness in
`tests/regression/run_exec_semantics.sh`, embedded build case
`exec_two_rates`).

## 1. Problem

On the host, HAL/S real-time statements lower to OS threads: a cyclic
`SCHEDULE B IN 0.1 REPEAT EVERY 0.5;` becomes a detached
`std::thread::spawn` running `sleep(delay); while !cancel { body;
sleep(period) }` with an `Arc<AtomicBool>` cancel flag; `SCHEDULE B
PRIORITY(3);` spawns with advisory priority; `TERMINATE` flips the cancel
flag; EVENTs are `AtomicBool` statics. None of that exists on a bare
ESP32-C3 / Cortex-M, so today the embedded guard rejects SCHEDULE/TASK
with `compile_error!`.

## 2. Ground truth: what the compiler accepts today

Only these forms parse (everything else in USA003088 —
`AFTER/AT/ON/UNTIL/WHILE/DEPENDENT` schedules, `WAIT <duration>` — is
**unparsed** and out of scope here; see docs/LANGUAGE_SUPPORT.md):

| Statement | AST |
| --------- | --- |
| `SCHEDULE T PRIORITY(p);` | priority only |
| `SCHEDULE T PRIORITY(p) … REPEAT EVERY n;` | priority + period |
| `SCHEDULE T IN d [REPEAT EVERY n];` | delay ± period |
| `TERMINATE [T];` | named or bare |
| `WAIT <event>;` / `WAIT UNTIL <cond>;` | event / condition |

So the executive must implement exactly: delayed start, fixed period,
priority as a selection preference, termination, and event/condition
waits. Nothing more.

## 3. Proposed design

### 3.1 Shape: table-driven cooperative superloop (no preemption)

For an embedded target, codegen emits alongside the existing `hal_rt`:

```rust
mod hal_exec {
    pub struct Slot {
        pub name: &'static str,
        pub body: fn(),
        pub priority: i32,
        pub period_us: u64,          // 0 = one-shot
        pub next_due_us: core::cell::Cell<u64>,
        pub state: core::cell::Cell<State>,  // Idle | Armed | Done
    }
    // static TABLE: [Slot; N]  — one entry per TASK, generated
    pub fn run(now_us: fn() -> u64) -> ! {
        loop {
            // pick the highest-priority Armed slot with next_due <= now
            // run it TO COMPLETION, then next_due += period (or Done)
        }
    }
}
```

- One `Slot` per `TASK` block, generated at compile time (fixed-size
  array, no allocation).
- `SCHEDULE` statements in the PROGRAM body arm a slot (set priority,
  delay→`next_due`, period); `TERMINATE T` sets it back to Idle;
  bare `TERMINATE` idles all.
- When the PROGRAM body falls off the end and any slot is Armed, the
  generated `main` enters `hal_exec::run(now_us)` instead of `loop {}`.
- **Cooperative, run-to-completion**: a task body runs uninterrupted;
  the executive picks again only when it returns. Priority is a
  *selection order among due tasks*, never preemption.

### 3.2 Time base (per target profile, like write_sink today)

| Target | `now_us` source |
| ------ | ---------------- |
| esp32-c3 | `esp_hal::time::Instant::now()` (SystemTimer, 16 MHz — µs precision) |
| cortex-m | DWT cycle counter / SysTick wrap counting — **the stub profile documents its tick source in `memory.x`-style comments**; exact choice at implementation review |
| host | `std::time::Instant` — the SAME generated table runs under std for unit tests (time base is a function pointer) |

The host-runnable table is the testing story: executive logic gets pytest
+ `cargo test`-style coverage without hardware.

### 3.3 Statement lowering on embedded

| HAL/S | Lowering |
| ----- | -------- |
| `SCHEDULE T IN d REPEAT EVERY n;` | `arm(slot, now+d, n, default_prio)` |
| `SCHEDULE T PRIORITY(p) [REPEAT EVERY n];` | `arm(slot, now, n_or_0, p)` |
| `TERMINATE T;` | `idle(slot)` — legal only for dependents (v1: every task is a dependent of the program, so this is a table lookup) |
| `TERMINATE;` (bare) | §8.5 rule 2: self-termination — in a task: idle that task's slot and return; in the program body: terminate the program **and its dependents** (idle all slots, halt) |
| `WAIT <event>;` / `WAIT UNTIL <cond>;` | **rejected on embedded in v1** with a `compile_error!` citing §8.6: WAIT stalls only the issuing process, and a cooperative spin cannot honor that. v2 lowers task-WAIT as a condition-gated slot (return + re-arm with condition). |
| EVENT `SET`/`RESET`/read | `AtomicBool` load/store — note: `riscv32imc` has **no CAS**, only atomic load/store, which is exactly what EVENTs need; executive state uses `Cell` (single context) |

COMPOOL (item 2a's `CsMutex`) is already interrupt-safe and works
unchanged from tasks, the program body, and shim ISRs.

### 3.4 Explicitly documented deviations from PASS semantics

| PASS | This executive (v1) |
| ---- | ------------------- |
| Priority-preemptive process model | Cooperative; a long task delays everything (jitter = longest task body) |
| `WAIT` stalls only the issuer (§8.6) | Not implementable cooperatively — WAIT is a compile_error on embedded until v2 |
| Overrun handling / process queues | A task overrunning its period runs late and prints `[hal_exec] OVERRUN <task>` on the debug console |
| Independent process stacks | All tasks share the main stack (run-to-completion makes this sound) |

These land verbatim in docs/EMBEDDED_TARGETS.md — never silent.

## 4. Non-goals (v1)

RTIC/embassy backends (a later opt-in profile), preemption, the unparsed
SCHEDULE forms, `WAIT <duration>` (would first need grammar), `UPDATE`
blocks, multi-core, tickless idle/sleep modes, watchdog integration.

## 5. Test & acceptance plan

1. Emission tests (mutation-kill): table generated 1:1 with TASK blocks;
   each statement lowers per §3.3; host lowering byte-unchanged.
2. Host-side executive semantics tests: inject a fake `now_us`, assert
   ordering (priority among due), period accumulation (no drift:
   `next_due += period`, not `now + period`), TERMINATE, one-shots.
3. Embedded build tier: add a SCHEDULE example (new
   `docs/HAL-S-Source-Code-Examples/exec_two_rates/`).
4. Hardware acceptance on the XIAO: two tasks at 2 Hz and 0.5 Hz sharing
   a COMPOOL counter; serial timestamps confirm rates within tolerance
   and priority ordering when both come due together.

## 6. Resolved decisions (2026-09-13)

1. **Tick unit**: µs internally (`u64`; SystemTimer native). HAL/S
   second-valued expressions convert at the arming site
   (`(secs * 1_000_000.0) as u64`).
2. **WAIT**: rejected on embedded in v1 (`compile_error!` citing §8.6 —
   WAIT stalls only the issuing process, which a cooperative spin cannot
   honor). v2: condition-gated slots for `WAIT UNTIL` in tasks.
3. **Overrun policy**: debug line `[hal_exec] OVERRUN <task>` on the
   target's console sink.
4. **Bare `TERMINATE;`**: official §8.5 rule 2 — self-termination, with
   dependent cascade: in a task, idle that slot; in the program body,
   idle all slots and halt.
5. **Priority ties**: declaration (table) order.
