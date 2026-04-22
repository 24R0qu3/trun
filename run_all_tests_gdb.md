# run_all_tests_gdb.sh

Runs test binaries one after another under GDB and collects results.

## Usage

```bash
./run_all_tests_gdb.sh [OPTIONS]

--build <dir>       cmake build directory (default: CC_V1X90-Debug)
--playlist <file>   run a custom test selection instead of the full suite
--repeat <N>        repeat the whole suite N times (default: 1)
--shuffle           randomize test order each round
```

## Test suite

Without `--playlist` the script runs all active fast and long-running tests
in order (fast first, then long).

| Type | Timeout |
|------|---------|
| `fast_running` (tfr) | 1 min |
| `long_running` (tlr) | 3 min |

## Playlist

A playlist file groups tests into named sections, each with its own build
directory. This allows mixing tests from different builds or running only a
specific cross-binary scenario.

```ini
[OSB cross-binary]
build = /path/to/build
fast_running = rst_tfr_rst_smc_osb
long_running = rst_tlr_rst_smc_osb_tc_server

[Another group]
build = /path/to/other/build
long_running = rst_tlr_rst_smc_mics_fmis_isobus
```

## Result states

| State | Meaning |
|-------|---------|
| `PASS` | GDB exited 0 |
| `FAIL` | GDB exited non-zero |
| `TIMEOUT` | Binary exceeded the time limit |
| `INTR` | Skipped via first Ctrl+C |
| `SKIP` | Binary not found |

## Ctrl+C behaviour

- **First Ctrl+C** — kills the running test, marks it `INTR`, continues with the next test.
- **Second Ctrl+C within 2 s** — exits the script immediately.

## Output

All GDB output is appended to `gdb_all_tests_log.txt`.
A summary table is printed at the end with group, test name, status, and
execution time per entry, plus total wall-clock time and pass/fail/skip counts.
