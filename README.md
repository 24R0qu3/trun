# trun

Test runner CLI and MCP server for C++ (GDB/Valgrind) and Python (pytest) test suites.
Define *playlists* of tests with per-group executor and timeout settings, run them from
the terminal, and expose everything as MCP tools so an AI can drive debugging autonomously.

## Installation

```bash
pipx install git+ssh://git@github.com/24R0qu3/trun.git
```

Or from a local clone:

```bash
git clone git@github.com:24R0qu3/trun.git
cd trun
pipx install .
```

For development (editable install):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

## Quick start

```bash
# Run the built-in suite (23 fast + 7 long C++ tests) under GDB
trun run

# Create a playlist and add tests to it
trun playlist create smoke
trun playlist add smoke --group OSB --build /path/to/build \
    --type fast_running --executor gdb rst_tfr_rst_smc_osb

# Run only specific Qt test functions within a binary
trun playlist add smoke --group OSB --build /path/to/build \
    --type fast_running --test-cases tst_parseIso,tst_roundTrip \
    rst_tfr_rst_smc_isobus_isoxml

# Run the playlist, overriding all executors with valgrind
trun run --playlist smoke --executor valgrind

# Repeat 3 times in random order
trun run --playlist smoke --repeat 3 --shuffle
```

## CLI reference

```
trun run [--playlist NAME_OR_PATH] [--build DIR] [--repeat N] [--shuffle]
         [--executor gdb|direct|valgrind|pytest]

trun playlist list
trun playlist show     <name>
trun playlist create   <name>
trun playlist add      <name> --group G --build DIR --type fast_running|long_running
                              [--executor gdb|direct|valgrind|pytest]
                              [--timeout-fast SECS] [--timeout-long SECS]
                              [--test-cases FUNC1[,FUNC2,...]]
                              <test1> [test2 ...]
trun playlist remove-tests <name> --group G <test1> [test2 ...]
trun playlist delete   <name>
trun playlist migrate  [<name>]

trun executors
trun mcp [--print-config]
trun patch-claude [--remove]
```

## Playlist format

Playlists are `.yaml` files stored in `~/.config/trun/playlists/`.

```yaml
groups:
  - name: OSB cross-binary
    build: /path/to/build/CC_V1X90-Debug
    executor: gdb
    timeout_fast: 90
    timeout_long: 240
    tests:
      - name: rst_tfr_rst_smc_osb
        subdir: fast_running
      - name: rst_tlr_rst_smc_osb_tc_server
        subdir: long_running
      # Run only specific Qt test functions within a binary:
      - name: rst_tfr_rst_smc_isobus_isoxml
        subdir: fast_running
        test_cases:
          - tst_parseIso
          - tst_roundTrip

  - name: Python checks
    build: /path/to/repo
    executor: pytest
    timeout_fast: 30
    tests:
      - name: tests/unit
        subdir: fast_running
      - name: tests/integration
        subdir: fast_running
```

**Group fields:**

| Field | Default | Description |
|-------|---------|-------------|
| `name` | required | Group identifier |
| `build` | required | Build root (cmake build dir or project root) |
| `executor` | `gdb` | How to run: `gdb`, `direct`, `valgrind`, `pytest` |
| `timeout_fast` | executor default | Timeout in seconds for `fast_running` tests |
| `timeout_long` | executor default | Timeout in seconds for `long_running` tests |

**Test entry fields:**

| Field | Default | Description |
|-------|---------|-------------|
| `name` | required | Test binary name |
| `subdir` | required | `fast_running` or `long_running` |
| `test_cases` | — | Qt test function names to run; omit to run all |

The CLI `--executor` flag overrides per-group settings for a single run.

### Migrating from the old `.ini` format

```bash
trun playlist migrate          # migrate all .ini playlists
trun playlist migrate <name>   # migrate a single playlist
```

## Executors

| Executor | Command | fast timeout | long timeout |
|----------|---------|-------------|-------------|
| `gdb` | `gdb -batch -ex run -ex bt -ex quit <bin>` | 60 s | 180 s |
| `direct` | `<bin>` | 60 s | 180 s |
| `valgrind` | `valgrind --leak-check=full <bin>` | 120 s | 360 s |
| `pytest` | `pytest <path> -v` | 60 s | 180 s |

## Test result states

| State | Meaning |
|-------|---------|
| `PASS` | Process exited 0 |
| `FAIL` | Process exited non-zero |
| `TIMEOUT` | Exceeded time limit |
| `SKIP` | Binary not found |

## MCP server

Register trun as an MCP server in Claude Code:

```bash
trun patch-claude
# Restart Claude Code
```

Or manually add the output of `trun mcp --print-config` to `~/.claude.json`.

### Available MCP tools

| Tool | Description |
|------|-------------|
| `run_tests` | Run a playlist or built-in suite |
| `list_playlists` | List saved playlists |
| `get_playlist` | Get full contents of a playlist |
| `create_playlist` | Create an empty playlist |
| `add_tests` | Add tests to a playlist group (supports `test_cases` per entry) |
| `remove_tests` | Remove tests from a group |
| `delete_playlist` | Delete a playlist |
| `get_last_log` | Fetch the last run's GDB/valgrind output |
| `list_executors` | List execution modes and timeouts |
| `migrate_playlist` | Convert a single `.ini` playlist to YAML |
| `migrate_all_playlists` | Convert all `.ini` playlists to YAML |

## Configuration

| Environment variable | Description |
|----------------------|-------------|
| `TRUN_BUILD_DIR` | Default build directory for the built-in suite |

Config: `~/.config/trun/`  
Logs: `~/.local/share/trun/last_run.log`
