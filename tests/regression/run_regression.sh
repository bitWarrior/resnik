#!/usr/bin/env bash

# -e: Exit immediately if a command exits with a non-zero status.
# -E: Ensure 'trap' inherits the ERR signal (works with -e).
# -u: Treat unset variables as an error and exit immediately.
# -o pipefail: Set exit status of a pipeline to the last non-zero exit code.
set -Eeuo pipefail

# ==============================================================================
# 2. ENVIRONMENT SANITIZATION
# ==============================================================================
# Hardcode the PATH to prevent PATH-injection attacks. Do not trust the 
# environment the script was launched from.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Clear the internal field separator to prevent strange word-splitting issues
# with user input.
IFS=$'\n\t'

# Determine Git Root
if ! GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null); then
    echo "Error: This script must be run from within a Git repository." >&2
    exit 1
fi

# Determine Resnik path
readonly _RESNIK="$GIT_ROOT/resnik-venv/bin/resnik"

# Verify Resnik exists and is executable
if [[ ! -x "$_RESNIK" ]]; then
    echo "Error: Resnik executable not found or not executable." >&2
    echo "Path checked: $_RESNIK" >&2
    echo "Did you forget to build the virtual environment?" >&2
    exit 1
fi

# ==============================================================================
# 3. CLEANUP 
# ==============================================================================
cleanup() {
    # Suppress errors during cleanup
    trap - SIGINT SIGTERM ERR EXIT

    # Find all generated rust files
    # Check if GIT_ROOT is set and the directory exists to prevent catastrophic deletions
    if [[ -n "${GIT_ROOT:-}" ]] && [[ -d "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" ]]; then
        
        # 1. Remove the generated intermediate Rust (*.rs) files
        find "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" -type f -name "*.rs" -delete
        
        # 2. Dynamically find and remove the compiled executables
        # We find every .hal file, null-terminate the output (-print0) to handle spaces,
        # and feed it into a while loop.
        while IFS= read -r -d '' hal_file; do
            
            # Use Bash parameter expansion to strip '.hal' from the end of the file path.
            # Example: /path/to/hello_world.hal becomes /path/to/hello_world
            exe_file="${hal_file%.hal}"
            
            # Check if that exact file exists, and if so, delete it
            if [[ -f "$exe_file" ]]; then
                rm -f "$exe_file"
            fi
            
        done < <(find "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" -type f -name "*.hal" -print0)        
    fi
}
trap cleanup SIGINT SIGTERM ERR EXIT

# ==============================================================================
# 4. PRIVILEGE CHECK 
# ==============================================================================
if [[ $EUID -eq 0 ]]; then
    echo "Warning: Running as root. Dropping privileges..."
    exec su -s /bin/bash nobody "$0" "$@"
    # The script restarts here as the unprivileged user
fi

# ==============================================================================
# 5. IMMUTABLE VARIABLES
# ==============================================================================
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
readonly SCRIPT_NAME="$(basename "$0")"

# ==============================================================================
# 6. Run Regression Test
# ==============================================================================

# Initialize Test Tracking Variables
PASSED=0
FAILED=0
declare -a FAILED_TESTS=()

# Helper function to run tests, catch failures, and prevent script exit
run_test() {
    local test_name="$1"
    shift # The rest of the arguments are the compilation command

    local hal_file=""
    local exe_file=""

    # Dynamically find the .hal file in the argument list
    for arg in "$@"; do
        if [[ "$arg" == *.hal ]]; then
            hal_file="$arg"
            # Strip the '.hal' extension to get the executable path
            exe_file="${hal_file%.hal}"
            break
        fi
    done

    # Step 1: Run the compilation command
    if "$@"; then
        # Step 2: Verify the executable was created and is runnable
        if [[ -n "$exe_file" ]] && [[ -x "$exe_file" ]]; then
            
            # Step 3: Execute the compiled binary
            echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
            if "$exe_file"; then
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                echo "[✓ PASS] $test_name"
                PASSED=$((PASSED + 1))
            else
                echo "+++++++++++++++++++++++++++++++++++++++++++++++++++++++++"
                echo "[✗ FAIL] $test_name (Execution Failed)"
                FAILED=$((FAILED + 1))
                FAILED_TESTS+=("$test_name (Execution Failed)")
            fi
        else
            echo "[✗ FAIL] $test_name (Executable not found or not executable)"
            FAILED=$((FAILED + 1))
            FAILED_TESTS+=("$test_name (Missing Exe)")
        fi
    else
        echo "[✗ FAIL] $test_name (Compilation Failed)"
        FAILED=$((FAILED + 1))
        FAILED_TESTS+=("$test_name (Compilation Failed)")
    fi
    echo ""
}

# Banner
cat << "EOF"
 _____  ______  _____  _   _  _____  _    _ 
|  __ \|  ____|/ ____|| \ | ||_   _|| |  / /
| |__) | |__  | (___  |  \| |  | |  | | / / 
|  _  /|  __|  \___ \ | . ` |  | |  | |/ /  
| | \ \| |____ ____) || |\  | _| |_ | |\ \  
|_|  \_\______|_____/ |_| \_||_____||_| \_\ 
                                            
   [ R E G R E S S I O N   T E S T S ]      
                                            
EOF

echo "----------------------------------"
echo "Test Case #1 - Hello World"
echo "----------------------------------"
echo "[+] Verify Resnik can compile a very simple hal source code example."
run_test "TC1 - Hello World" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal --emit-exe

echo "----------------------------------"
echo "Test Case #2 - Attitude Control"
echo "----------------------------------"
echo "[+] Verify Resnik correctly handles assignments between calculated linear algebra nodes and statically declared dummy vectors."
run_test "TC2 - Attitude Control" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_control/attitude_control.hal --emit-exe

echo "----------------------------------"
echo "Test Case #3 - Attitude Error"
echo "----------------------------------"
echo "[+] Verify Resnik can handle single expression to multiple variables simultaneously - combined intrinsic geometric functions into expression evaluation tree."
run_test "TC3 - Attitude Error" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_err/attitude_err.hal --emit-exe

echo "----------------------------------"
echo "Test Case #4 - Branch Inference"
echo "----------------------------------"
echo "[+] Verify Resnik ability to process multi-branch control flow using HAL/S DO CASE for state machine logic."
run_test "TC4 - Branch Inference" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/branch_inference/branch_inference.hal --emit-exe

echo "----------------------------------"
echo "Test Case #5 - GNC Correction"
echo "----------------------------------"
echo "[+] Verify Resnik's ability to process asynchronous tasks"
run_test "TC5 - GNC Correction" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/gnc_correction/gnc_correction.hal --emit-exe

echo "----------------------------------"
echo "Test Case #6 - JSTAR Telemetry"
echo "----------------------------------"
echo "[+] Verify Resnik ability to process HAL/S includes native error-trapping constructs (ON ERROR block) on divide-by-zero error."
run_test "TC6 - JSTAR Telemetry" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/jstar_telemetry/jstar_telemetry.hal --emit-exe

echo "----------------------------------"
echo "Test Case #7 - NAV Filter"
echo "----------------------------------"
echo "[+] Verify Resnik's compatability of HAL/S unique indexing semantics for geometric types using advanced matrix partitioning and slicing."
run_test "TC7 - NAV Filter" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/nav_filter/nav_filter.hal --emit-exe

echo "----------------------------------"
echo "Test Case #8 - Sat Mon"
echo "----------------------------------"
echo "[+] Verify Resnik's parsing with the REPLACE keyword before or during AST generation and Bit Slicing"
run_test "TC8 - Sat Mon" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sat_mon/sat_mon.hal --emit-exe

echo "----------------------------------"
echo "Test Case #9 - Shuttle State"
echo "----------------------------------"
echo "[+] Verify Resnik does not perform dyanmic memory allocation - all global data shared between different tasks and programs must be statically allocated in a COMPOOL."
run_test "TC9 - Shuttle State" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/shuttle_state/shuttle_state.hal --emit-exe

echo "----------------------------------"
echo "Test Case #10 - Sensor Init"
echo "----------------------------------"
echo "[+] Verify Resnik's ability to process standard DO FOR loop and dynamic 1-dimensional subscripting."
run_test "TC10 - Sensor Init" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sensor_init/sensor_init.hal --emit-exe

echo "----------------------------------"
echo "Test Case #11 - SpaceWire Mon"
echo "----------------------------------"
echo "[+] Verify Resnik's processing of HAL/S process synchronization using EVENT variables and WAIT commands."
run_test "TC11 - SpaceWire Mon" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spacewire_mon/spacewire_mon.hal --emit-exe

echo "----------------------------------"
echo "Test Case #12 - Spw Security"
echo "----------------------------------"
echo "[+] Verify Resnik's compliance adherence to operator precedence parsing for complex boolean and bitwise expressions." 
run_test "TC12 - Spw Security" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spw_security/spw_security.hal --emit-exe

echo "----------------------------------"
echo "Test Case #13 - Star Tracker"
echo "----------------------------------"
echo "[+] Verify Resnik's ability to to handle deeply nested data types—specifically, arrays where each element is a complex structure."
run_test "TC13 - Star Tracker" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/star_tracker/star_tracker.hal --emit-exe

echo "----------------------------------"
echo "Test Case #14 - Telemetry App"
echo "----------------------------------"
echo "[+] Verify Resnik parser's ability to process structure templates, instantiate structure variables, and handle multi-element assignments."
run_test "TC14 - Telemetry App" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/telemetry_app/telemetry_app.hal --emit-exe

echo "----------------------------------"
echo "Test Case #15 - Telemetry Formatter"
echo "----------------------------------"
echo "[+] Verify Resnik's string declarations, literal assignments, and the concatenation operator"
run_test "TC15 - Telemetry Formatter" "$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/telemetry_formatter/telemetry_formatter.hal --emit-exe

# ==============================================================================
# 7. SUMMARY
# ==============================================================================
echo ""
echo "=================================="
echo "    REGRESSION TEST SUMMARY       "
echo "=================================="
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [[ $FAILED -gt 0 ]]; then
    echo ""
    echo "Failing Tests:"
    for test in "${FAILED_TESTS[@]}"; do
        echo "  - $test"
    done
    # Exit with an error code since regression failed
    exit 1 
else
    echo ""
    echo "All executed tests passed successfully!"
    exit 0
fi