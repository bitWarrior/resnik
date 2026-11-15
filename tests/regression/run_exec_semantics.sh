#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Steven Doran

# Host-side semantics check for the hal_exec cyclic executive
# (docs/NO_STD_ROADMAP.md item 2b, docs/CYCLIC_EXECUTIVE_DESIGN.md §5).
#
# Extracts the exact `mod hal_exec` text the code generator emits, wraps it
# in two host harnesses with an injected fake microsecond clock, and runs
# them under rustc:
#
#   Scenario A — priority ordering, declaration-order tie-break, one-shot
#                slots, self-TERMINATE from a body, no-drift periods.
#   Scenario B — overrun detection reports the right task and the
#                executive keeps running the other tasks afterwards.
#
# Skips cleanly (exit 0 with a notice) when rustc is not installed.

set -Eeuo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME}/.cargo/bin"
IFS=$'\n\t'

if ! GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null); then
    echo "Error: run from within the git repository." >&2
    exit 1
fi
readonly _PY="$GIT_ROOT/resnik-venv/bin/python"
if [[ ! -x "$_PY" ]]; then
    echo "Error: $_PY not found — create the venv per README Installation." >&2
    exit 1
fi
if ! command -v rustc >/dev/null 2>&1; then
    echo "SKIP: rustc not found — install Rust via rustup to run this tier."
    exit 0
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/resnik-exec-semantics.XXXXXX")"
cleanup() { trap - SIGINT SIGTERM ERR EXIT; rm -rf "$WORK"; }
trap cleanup SIGINT SIGTERM ERR EXIT

# The module under test, verbatim from the code generator.
"$_PY" - >"$WORK/hal_exec.rs" <<'PY'
from resnik.backend.codegen import RustCodeGenerator
print(RustCodeGenerator._HAL_EXEC)
PY

cat >"$WORK/scenario_a.rs" <<'RS'
// Scenario A: priority, declaration ties, one-shot, self-terminate, no drift.
include!("hal_exec.rs");

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

// Fake clock: every look at the clock moves time forward 100 µs, so the
// executive's idle spin marches time instead of hanging.
static NOW: AtomicU64 = AtomicU64::new(0);
fn now_us() -> u64 { NOW.fetch_add(100, Ordering::SeqCst) + 100 }
fn busy(us: u64) { NOW.fetch_add(us, Ordering::SeqCst); }

static LOG: Mutex<Vec<(&'static str, u64)>> = Mutex::new(Vec::new());
fn log(name: &'static str) {
    LOG.lock().unwrap().push((name, NOW.load(Ordering::SeqCst)));
}
fn overrun(name: &'static str) { log("OVR"); eprintln!("unexpected overrun: {name}"); }

static A_RUNS: AtomicU64 = AtomicU64::new(0);
fn a_body() { // self-TERMINATEs on its third cycle
    log("A"); busy(500);
    if A_RUNS.fetch_add(1, Ordering::SeqCst) + 1 == 3 { hal_exec::idle(&SLOT_A); }
}
fn b_body() { log("B"); busy(500); }
fn c_body() { log("C"); busy(500); }
fn d_body() { log("D"); busy(500); }
fn fin_body() {
    let log = LOG.lock().unwrap();
    let names: Vec<&str> = log.iter().map(|e| e.0).collect();
    // Priority first, then the declaration-order tie (A before B at prio 2).
    assert_eq!(&names[..3], &["C", "A", "B"], "start order: {names:?}");
    assert_eq!(names.iter().filter(|n| **n == "A").count(), 3, "A self-terminates after 3");
    assert_eq!(names.iter().filter(|n| **n == "D").count(), 1, "D is one-shot");
    assert_eq!(names.iter().filter(|n| **n == "OVR").count(), 0, "no overruns here");
    // No drift: B's k-th run stays on the absolute 10 ms grid.
    let b: Vec<u64> = log.iter().filter(|e| e.0 == "B").map(|e| e.1).collect();
    assert!(b.len() >= 15, "expected >=15 B cycles, got {}", b.len());
    for (k, t) in b.iter().enumerate() {
        let nominal = b[0] + 10_000 * k as u64;
        let err = t.abs_diff(nominal);
        assert!(err < 2_500, "B[{k}] drifted {err} µs off the grid");
    }
    println!("EXEC-SEMANTICS-A-OK");
    std::process::exit(0);
}

static SLOT_C: hal_exec::Slot = hal_exec::Slot::new("C", c_body);
static SLOT_A: hal_exec::Slot = hal_exec::Slot::new("A", a_body);
static SLOT_B: hal_exec::Slot = hal_exec::Slot::new("B", b_body);
static SLOT_D: hal_exec::Slot = hal_exec::Slot::new("D", d_body);
static SLOT_FIN: hal_exec::Slot = hal_exec::Slot::new("FIN", fin_body);
static TABLE: [&hal_exec::Slot; 5] = [&SLOT_C, &SLOT_A, &SLOT_B, &SLOT_D, &SLOT_FIN];

fn main() {
    let t0 = now_us();
    hal_exec::arm(&SLOT_C, t0, 0, 20_000, 5);     // highest priority
    hal_exec::arm(&SLOT_A, t0, 0, 10_000, 2);     // ties with B, earlier slot
    hal_exec::arm(&SLOT_B, t0, 0, 10_000, 2);
    hal_exec::arm(&SLOT_D, t0, 5_000, 0, 1);      // one-shot
    hal_exec::arm(&SLOT_FIN, t0, 200_000, 0, 9);  // checks + exit
    hal_exec::run(&TABLE, now_us, overrun);
}
RS

cat >"$WORK/scenario_b.rs" <<'RS'
// Scenario B: an overrunning task is reported by name and the executive
// keeps servicing the other tasks.
include!("hal_exec.rs");

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

static NOW: AtomicU64 = AtomicU64::new(0);
fn now_us() -> u64 { NOW.fetch_add(100, Ordering::SeqCst) + 100 }
fn busy(us: u64) { NOW.fetch_add(us, Ordering::SeqCst); }

static B_RUNS: AtomicU64 = AtomicU64::new(0);
static OVERRUNS: Mutex<Vec<&'static str>> = Mutex::new(Vec::new());
fn overrun(name: &'static str) { OVERRUNS.lock().unwrap().push(name); }

fn b_body() { B_RUNS.fetch_add(1, Ordering::SeqCst); busy(500); }
fn slow_body() { busy(31_000); } // 31 ms of work on a 30 ms period: overruns
fn fin_body() {
    let ovr = OVERRUNS.lock().unwrap();
    // The debug line means "this task missed its deadline": SLOW is blamed
    // first (its body outran its own period), and B's catch-up runs after
    // SLOW's long frame are late too, so they are reported as well.
    assert!(!ovr.is_empty(), "SLOW never reported an overrun");
    assert_eq!(ovr[0], "SLOW", "first blame must be SLOW: {ovr:?}");
    assert!(ovr.iter().filter(|n| **n == "SLOW").count() >= 2,
            "SLOW overruns every cycle: {ovr:?}");
    let b = B_RUNS.load(Ordering::SeqCst);
    assert!(b >= 8, "B starved after the overrun: only {b} runs");
    println!("EXEC-SEMANTICS-B-OK");
    std::process::exit(0);
}

static SLOT_B: hal_exec::Slot = hal_exec::Slot::new("B", b_body);
static SLOT_SLOW: hal_exec::Slot = hal_exec::Slot::new("SLOW", slow_body);
static SLOT_FIN: hal_exec::Slot = hal_exec::Slot::new("FIN", fin_body);
static TABLE: [&hal_exec::Slot; 3] = [&SLOT_B, &SLOT_SLOW, &SLOT_FIN];

fn main() {
    let t0 = now_us();
    hal_exec::arm(&SLOT_B, t0, 0, 10_000, 2);
    hal_exec::arm(&SLOT_SLOW, t0, 0, 30_000, 1);
    hal_exec::arm(&SLOT_FIN, t0, 100_000, 0, 9);
    hal_exec::run(&TABLE, now_us, overrun);
}
RS

PASS=0
FAIL=0
for scen in a b; do
    echo "----------------------------------------------------------------"
    echo "[hal_exec] scenario $scen"
    bin="$WORK/scenario_$scen"
    if ! rustc -O --edition 2021 "$WORK/scenario_$scen.rs" -o "$bin" \
            2>"$WORK/scenario_$scen.build.log"; then
        echo "[✗ FAIL] rustc failed:"
        tail -n 12 "$WORK/scenario_$scen.build.log" | sed 's/^/    /'
        FAIL=$((FAIL + 1)); continue
    fi
    if out="$(timeout 30 "$bin" 2>&1)" \
            && grep -q "EXEC-SEMANTICS-$(tr a-z A-Z <<<"$scen")-OK" <<<"$out"; then
        echo "[✓ PASS] $out"
        PASS=$((PASS + 1))
    else
        echo "[✗ FAIL] run output:"
        sed 's/^/    /' <<<"$out"
        FAIL=$((FAIL + 1))
    fi
done

echo "=================================="
echo "  EXEC SEMANTICS SUMMARY"
echo "=================================="
echo "Passed: $PASS"
echo "Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
