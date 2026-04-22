#!/usr/bin/env bash
#
# Usage:
#   ./run_all_tests_gdb.sh [OPTIONS]
#
# Options:
#   --build <dir>       cmake build directory (default: CC_V1X90-Debug)
#   --playlist <file>   run tests defined in a playlist file instead of the
#                       built-in full suite (see playlist.conf for format)
#   --repeat <N>        run the full suite / playlist N times (default: 1)
#   --shuffle           randomize test order each round

DEFAULT_BUILD="/media/nielsruehr/F2EC62F5EC62B38F/projects/reichhardt/SMC4/ak1/rst_smart_command/build/CC_V1X90-Debug"
BUILD_DIR="$DEFAULT_BUILD"
PLAYLIST_FILE=""
LOG_FILE="gdb_all_tests_log.txt"
REPEAT=1
SHUFFLE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)    BUILD_DIR="$2";    shift 2 ;;
        --playlist) PLAYLIST_FILE="$2"; shift 2 ;;
        --repeat)   REPEAT="$2";       shift 2 ;;
        --shuffle)  SHUFFLE=1;         shift   ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── built-in test lists ────────────────────────────────────────────────────────

FAST_TESTS=(
    rst_tfr_rst_smc_core_mediator
    rst_tfr_rst_smc_database
    rst_tfr_rst_smc_utils_container_difference
    rst_tfr_rst_smc_fmis_device_internal
    rst_tfr_rst_smc_isobus_isoxml
    rst_tfr_rst_smc_logging
    rst_tfr_rst_smc_settings
    rst_tfr_rst_smc_settings_config
    rst_tfr_rst_smc_storage
    rst_tfr_rst_smc_utils_nholthaus_units
    rst_tfr_rst_smc_zmq
    rst_tfr_rst_smc_platform_can
    rst_tfr_rst_smc_platform_datetime_iso8601
    rst_tfr_rst_smc_isobus
    rst_tfr_rst_smc_vt_server
    rst_tfr_rst_smc_osb
    rst_tfr_rst_smc_mics
    rst_tfr_rst_smc_integrations_exception_isobus
    rst_tfr_rst_smc_isobus_diagnostic
    rst_tfr_rst_smc_isobus_isolib
    rst_tfr_rst_smc_isobus_isb
    rst_tfr_rst_smc_client_isobus_isb
    rst_tfr_rst_smc_mics_fmis_isobus
)

LONG_TESTS=(
    rst_tlr_rst_smc_fmis_device_interfaces
    rst_tlr_rst_smc_fmis_task
    rst_tlr_rst_smc_fmis_imp_exp
    rst_tlr_rst_smc_isobus_isolib
    rst_tlr_rst_smc_isobus
    rst_tlr_rst_smc_osb_tc_server
    rst_tlr_rst_smc_mics_fmis_isobus
)

# ── entry format: "name|subdir|builddir|group" ─────────────────────────────────

ALL_TESTS=()
ALL_GROUPS=()   # parallel to ALL_TESTS, used for display only

load_builtin() {
    for t in "${FAST_TESTS[@]}"; do
        ALL_TESTS+=("$t|fast_running|$BUILD_DIR|fast_running")
    done
    for t in "${LONG_TESTS[@]}"; do
        ALL_TESTS+=("$t|long_running|$BUILD_DIR|long_running")
    done
}

# ── playlist parser ────────────────────────────────────────────────────────────
# Playlist file format:
#
#   # comment
#   [Group Name]           <- section header, used as label in output
#   build = /path/to/build <- build dir for all tests in this section
#   fast_running = rst_tfr_rst_smc_osb rst_tfr_rst_smc_mics
#   long_running = rst_tlr_rst_smc_osb_tc_server

load_playlist() {
    local file="$1"
    local group="" bdir="" line key val

    if [ ! -f "$file" ]; then
        echo "Error: playlist file not found: $file"
        exit 1
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        # strip inline comments and leading/trailing whitespace
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "$line" ]] && continue

        if [[ "$line" =~ ^\[(.+)\]$ ]]; then
            group="${BASH_REMATCH[1]}"
            bdir=""
            continue
        fi

        if [[ "$line" =~ ^([^=]+)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            key="${key%"${key##*[![:space:]]}"}"   # rtrim key
            val="${val#"${val%%[![:space:]]*}"}"   # ltrim val

            if [ "$key" = "build" ]; then
                bdir="$val"
                continue
            fi

            # key is a subdir name (fast_running / long_running / …)
            local subdir="$key"
            if [ -z "$bdir" ]; then
                echo "Warning: no build dir set before tests in group [$group], skipping"
                continue
            fi
            for t in $val; do
                ALL_TESTS+=("$t|$subdir|$bdir|$group")
            done
        fi
    done < "$file"
}

if [ -n "$PLAYLIST_FILE" ]; then
    load_playlist "$PLAYLIST_FILE"
else
    load_builtin
fi

if [ ${#ALL_TESTS[@]} -eq 0 ]; then
    echo "Error: no tests to run."
    exit 1
fi

# ── counters & summary arrays ──────────────────────────────────────────────────

PASS=0
FAIL=0
SKIP=0

SUMMARY_NAME=()
SUMMARY_GROUP=()
SUMMARY_STATUS=()
SUMMARY_SECS=()
SUMMARY_ROUND=()

# ── Ctrl+C handling ────────────────────────────────────────────────────────────

CURRENT_GDB_PID=""
LAST_SIGINT=0
GDB_INTERRUPTED=0

handle_sigint() {
    local now
    now=$(date +%s)
    if (( now - LAST_SIGINT <= 2 )); then
        echo ""
        echo "Exiting."
        [ -n "$CURRENT_GDB_PID" ] && kill "$CURRENT_GDB_PID" 2>/dev/null
        exit 130
    fi
    LAST_SIGINT=$now
    GDB_INTERRUPTED=1
    echo ""
    echo "  [!] Ctrl+C — press again within 2s to exit, or wait to skip to the next test"
    [ -n "$CURRENT_GDB_PID" ] && kill "$CURRENT_GDB_PID" 2>/dev/null
}

trap handle_sigint SIGINT

> "$LOG_FILE"

# ── helpers ────────────────────────────────────────────────────────────────────

fmt_duration() {
    local secs=$1
    if   (( secs >= 3600 )); then printf "%dh%02dm%02ds" $(( secs/3600 )) $(( (secs%3600)/60 )) $(( secs%60 ))
    elif (( secs >=   60 )); then printf "%dm%02ds" $(( secs/60 )) $(( secs%60 ))
    else                          printf "%ds" "$secs"
    fi
}

shuffle_array() {
    local -n arr=$1
    local i j tmp n=${#arr[@]}
    for (( i=n-1; i>0; i-- )); do
        j=$(( RANDOM % (i+1) ))
        tmp="${arr[$i]}"; arr[$i]="${arr[$j]}"; arr[$j]="$tmp"
    done
}

run_test() {
    local name="$1"
    local subdir="$2"
    local round="$3"
    local bdir="$4"
    local group="$5"
    local binary="$bdir/test/$subdir/$name/$name"

    local limit
    case "$subdir" in
        fast_running) limit=60  ;;
        long_running) limit=180 ;;
        *)            limit=180 ;;
    esac

    if [ ! -f "$binary" ]; then
        echo "  [SKIP] binary not found: $binary"
        printf "=== [round %d] %s: SKIP (binary not found) ===\n" "$round" "$name" >> "$LOG_FILE"
        SUMMARY_NAME+=("$name")
        SUMMARY_GROUP+=("$group")
        SUMMARY_STATUS+=("SKIP")
        SUMMARY_SECS+=("-")
        SUMMARY_ROUND+=("$round")
        (( SKIP++ ))
        return
    fi

    echo -n "  Running $name (limit $(fmt_duration $limit)) ... "
    local t_start t_end elapsed
    t_start=$(date +%s)
    GDB_INTERRUPTED=0
    timeout "$limit" gdb -batch \
        -ex "run" \
        -ex "bt" \
        -ex "quit" \
        "$binary" >> "$LOG_FILE" 2>&1 &
    CURRENT_GDB_PID=$!
    wait "$CURRENT_GDB_PID"
    local exit_code=$?
    CURRENT_GDB_PID=""
    t_end=$(date +%s)
    elapsed=$(( t_end - t_start ))
    local dur
    dur=$(fmt_duration "$elapsed")

    if [ $GDB_INTERRUPTED -eq 1 ]; then
        echo "INTERRUPTED ($dur)"
        printf "=== [round %d] %s: INTERRUPTED (%s) ===\n" "$round" "$name" "$dur" >> "$LOG_FILE"
        SUMMARY_STATUS+=("INTR")
        (( SKIP++ ))
    elif [ $exit_code -eq 124 ]; then
        echo "TIMEOUT ($dur >= $(fmt_duration $limit))"
        printf "=== [round %d] %s: TIMEOUT (%s, limit %s) ===\n" \
            "$round" "$name" "$dur" "$(fmt_duration $limit)" >> "$LOG_FILE"
        SUMMARY_STATUS+=("TIMEOUT")
        (( FAIL++ ))
    elif [ $exit_code -eq 0 ]; then
        echo "OK  ($dur)"
        printf "=== [round %d] %s: PASS (%s) ===\n" "$round" "$name" "$dur" >> "$LOG_FILE"
        SUMMARY_STATUS+=("PASS")
        (( PASS++ ))
    else
        echo "FAIL (exit $exit_code, $dur)"
        printf "=== [round %d] %s: FAIL (exit %d, %s) ===\n" "$round" "$name" "$exit_code" "$dur" >> "$LOG_FILE"
        SUMMARY_STATUS+=("FAIL")
        (( FAIL++ ))
    fi
    SUMMARY_NAME+=("$name")
    SUMMARY_GROUP+=("$group")
    SUMMARY_SECS+=("$elapsed")
    SUMMARY_ROUND+=("$round")
    echo "" >> "$LOG_FILE"
}

# ── main ───────────────────────────────────────────────────────────────────────

if [ -n "$PLAYLIST_FILE" ]; then
    echo "Playlist  : $PLAYLIST_FILE"
else
    echo "Build dir : $BUILD_DIR"
fi
echo "Log       : $LOG_FILE"
echo "Repeat    : $REPEAT"
echo "Shuffle   : $([ $SHUFFLE -eq 1 ] && echo yes || echo no)"
echo ""

TOTAL_START=$(date +%s)

for (( round=1; round<=REPEAT; round++ )); do
    round_tests=("${ALL_TESTS[@]}")
    [ $SHUFFLE -eq 1 ] && shuffle_array round_tests

    (( REPEAT > 1 )) && echo "══ Round $round / $REPEAT ══════════════════════════════════════"

    prev_group=""
    for entry in "${round_tests[@]}"; do
        IFS='|' read -r name subdir bdir group <<< "$entry"

        if [ "$group" != "$prev_group" ] && [ $SHUFFLE -eq 0 ]; then
            echo "  --- $group ---"
            prev_group="$group"
        fi

        run_test "$name" "$subdir" "$round" "$bdir" "$group"
    done
    echo ""
done

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$(( TOTAL_END - TOTAL_START ))

# ── summary table ──────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════════════"
if (( REPEAT > 1 )); then
    printf "  %-3s  %-28s  %-45s  %-7s  %s\n" "Run" "Group" "Test" "Status" "Time"
    echo "  ──────────────────────────────────────────────────────────────────────"
    for i in "${!SUMMARY_NAME[@]}"; do
        secs="${SUMMARY_SECS[$i]}"
        dur=$( [ "$secs" = "-" ] && echo "-" || fmt_duration "$secs" )
        printf "  %-3s  %-28s  %-45s  %-7s  %s\n" \
            "#${SUMMARY_ROUND[$i]}" "${SUMMARY_GROUP[$i]}" "${SUMMARY_NAME[$i]}" \
            "${SUMMARY_STATUS[$i]}" "$dur"
    done
else
    printf "  %-28s  %-45s  %-7s  %s\n" "Group" "Test" "Status" "Time"
    echo "  ──────────────────────────────────────────────────────────────────────"
    for i in "${!SUMMARY_NAME[@]}"; do
        secs="${SUMMARY_SECS[$i]}"
        dur=$( [ "$secs" = "-" ] && echo "-" || fmt_duration "$secs" )
        printf "  %-28s  %-45s  %-7s  %s\n" \
            "${SUMMARY_GROUP[$i]}" "${SUMMARY_NAME[$i]}" "${SUMMARY_STATUS[$i]}" "$dur"
    done
fi
echo "  ──────────────────────────────────────────────────────────────────────"
printf "  Total: %-63s  %d passed, %d failed, %d skipped/interrupted\n" \
    "$(fmt_duration "$TOTAL_ELAPSED")" "$PASS" "$FAIL" "$SKIP"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "Full output in $LOG_FILE"
