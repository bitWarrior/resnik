#!/usr/bin/env bash

# Build-only embedded regression (docs/NO_STD_ROADMAP.md item 0).
#
# Compiles a curated list of HAL/S examples for --target esp32-c3 (plus one
# cortex-m case) and runs `cargo build --release` on each scaffold. No
# hardware, no flashing — this proves the generated no_std firmware
# *compiles*. The first run downloads crates from crates.io (network);
# cargo's global registry cache makes later runs much faster.
#
# Skips cleanly (exit 0 with a notice) when cargo or the rustup targets are
# not installed, so it is safe in minimal environments.

set -Eeuo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME}/.cargo/bin"
IFS=$'\n\t'

if ! GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null); then
    echo "Error: run from within the git repository." >&2
    exit 1
fi
readonly _RESNIK="$GIT_ROOT/resnik-venv/bin/resnik"
if [[ ! -x "$_RESNIK" ]]; then
    echo "Error: $_RESNIK not found — create the venv per README Installation." >&2
    exit 1
fi

# ---- toolchain availability (skip, don't fail) -------------------------------
if ! command -v cargo >/dev/null 2>&1; then
    echo "SKIP: cargo not found — install Rust via rustup to run this tier."
    exit 0
fi
INSTALLED_TARGETS="$(rustup target list --installed 2>/dev/null || true)"
have_target() { grep -qx "$1" <<<"$INSTALLED_TARGETS"; }
if ! have_target riscv32imc-unknown-none-elf; then
    echo "SKIP: rustup target riscv32imc-unknown-none-elf not installed" \
         "(rustup target add riscv32imc-unknown-none-elf)."
    exit 0
fi

# ---- work area ----------------------------------------------------------------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/resnik-embedded-build.XXXXXX")"
cleanup() { trap - SIGINT SIGTERM ERR EXIT; rm -rf "$WORK"; }
trap cleanup SIGINT SIGTERM ERR EXIT
# One shared target dir so esp-hal & friends compile once for the whole run.
export CARGO_TERM_COLOR=never

EXAMPLES_DIR="$GIT_ROOT/docs/HAL-S-Source-Code-Examples"

# Curated: inside today's no_std subset (no READ or CHARACTER
# variables — the CHARACTER/alloc gap).
# COMPOOL builds via the critical-section CsMutex (item 2a).
# VECTOR/MATRIX + math intrinsics build via the libm prelude (item 1).
# SCHEDULE/TASK, TERMINATE and CANCEL build via the hal_exec cyclic
# executive (item 2b); EVENT lowers to core atomics.
ESP32_CASES=(
    hello_word/hello_world
    exec_two_rates/exec_two_rates
    exec_priority_chain/exec_priority_chain
    exec_event_latch/exec_event_latch
    exec_terminate_forms/exec_terminate_forms
    exec_cancel_cycle/exec_cancel_cycle
    kalman_filter/kalman_filter
    linear_algebra_test/linear_algebra_test
    tensor_math/tensor_math
    attitude_math/attitude_math
    euler/euler
    orbital_kinematics/orbital_kinematics
    matrix_gen/matrix_gen
    compool_fault/compool_fault
    bit_manipulation/bit_manipulation
    integer_arith/integer_arith
    until_loops/until_loops
    goto_token/goto_token
    dangling_else_trap/dangling_else_trap
    branch_inference/branch_inference
    telemetry_shift/telemetry_shift
    ring_buffer/ring_buffer
    cmd_decoder/cmd_decoder
    scope_test/scope_test
    for_semantics/for_semantics
    stack_bubbling/stack_bubbling
    same_scope_fault/same_scope_fault
    bit_layout/bit_layout
)

PASS=0
FAIL=0

build_case() { # $1 = target profile, $2 = example path (relative, no .hal)
    local profile="$1" rel="$2"
    local name; name="$(basename "$rel")"
    local hal="$EXAMPLES_DIR/$rel.hal"
    local proj="$WORK/${profile}-${name}"
    local log="$proj.log"
    echo "----------------------------------------------------------------"
    echo "[$profile] $rel"
    if ! "$_RESNIK" compile "$hal" --target "$profile" \
            --emit-cargo "$proj" --rust "$WORK/${profile}-${name}.rs" \
            >"$log" 2>&1; then
        echo "[✗ FAIL] resnik compile failed — $log"
        FAIL=$((FAIL + 1)); return
    fi
    if (cd "$proj" && CARGO_TARGET_DIR="$WORK/target-$profile" \
            cargo build --release >>"$log" 2>&1); then
        echo "[✓ PASS] cargo build --release"
        PASS=$((PASS + 1))
    else
        echo "[✗ FAIL] cargo build failed:"
        tail -n 12 "$log" | sed 's/^/    /'
        FAIL=$((FAIL + 1))
    fi
}

for rel in "${ESP32_CASES[@]}"; do
    build_case esp32-c3 "$rel"
done

# One Cortex-M shape check (semihosting stub profile).
if have_target thumbv7em-none-eabihf; then
    build_case cortex-m hello_word/hello_world
else
    echo "NOTE: thumbv7em-none-eabihf not installed — skipping the cortex-m case."
fi

echo "=================================="
echo "  EMBEDDED BUILD SUMMARY"
echo "=================================="
echo "Passed: $PASS"
echo "Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
