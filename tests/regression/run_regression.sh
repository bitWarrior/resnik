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

    echo "Performing cleanup..."
    # Find all generated rust files
    if [[ -n "${GIT_ROOT:-}" ]] && [[ -d "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" ]]; then
        find "$GIT_ROOT/docs/HAL-S-Source-Code-Examples" -type f -name "*.rs" -delete
    fi

    echo "Cleanup complete."
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

echo "Test Case #1 - Hello World"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/hello_word/hello_world.hal --emit-exe

echo "Test Case #2 - Attitude Control"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/attitude_control/attitude_control.hal --emit-exe

echo "Test Case #3 - GNC Correction"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/gnc_correction/gnc_correction.hal --emit-exe

echo "Test Case #4 - Sat Mon"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sat_mon/sat_mon.hal --emit-exe

echo "Test Case #5 - Shuttle State"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/shuttle_state/shuttle_state.hal --emit-exe

echo "Test Case #6 - Sensor Init"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/sensor_init/sensor_init.hal --emit-exe

echo "Test Case #7 - SpaceWire Mon"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/spacewire_mon/spacewire_mon.hal --emit-exe

echo "Test Case #8 - Telemetry Formatter"
"$_RESNIK" compile "$GIT_ROOT"/docs/HAL-S-Source-Code-Examples/telemetry_formatter/telemetry_formatter.hal --emit-exe
