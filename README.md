# trun

Test runner CLI and MCP server for C++ (GDB/Valgrind) and Python (pytest) test suites.
Define *playlists* of tests with per-group executor and timeout settings, run them from
the terminal, and expose everything as MCP tools so an AI can drive debugging autonomously.

## Installation

```bash
git clone git@github.com:24R0qu3/trun.git
cd trun
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
                              --executor gdb|direct|valgrind|pytest
                              [--timeout-fast SECS] [--timeout-long SECS]
                              <test1> [test2 ...]
trun playlist remove-tests <name> --group G <test1> [test2 ...]
trun playlist delete   <name>

trun executors
trun mcp [--print-config]
trun patch-claude [--remove]
```

## Playlist format

Playlists are `.ini` files stored in `~/.config/trun/playlists/`.
The format is backward-compatible with `run_all_tests_gdb.sh`.

```ini
[OSB cross-binary]
build        = /path/to/build/CC_V1X90-Debug
executor     = gdb
timeout_fast = 90
timeout_long = 240
fast_running = rst_tfr_rst_smc_osb
long_running = rst_tlr_rst_smc_osb_tc_server

[Python checks]
build        = /path/to/repo
executor     = pytest
timeout_fast = 30
fast_running = tests/unit tests/integration
```

**Keys per section:**

| Key | Default | Description |
|-----|---------|-------------|
| `build` | required | Build root (cmake build dir or project root) |
| `executor` | `gdb` | How to run: `gdb`, `direct`, `valgrind`, `pytest` |
| `timeout_fast` | executor default | Timeout in seconds for `fast_running` tests |
| `timeout_long` | executor default | Timeout in seconds for `long_running` tests |
| `fast_running` | — | Space-separated test names |
| `long_running` | — | Space-separated test names |

The CLI `--executor` flag overrides per-section settings for a single run.

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
| `add_tests` | Add tests to a playlist group |
| `remove_tests` | Remove tests from a group |
| `delete_playlist` | Delete a playlist |
| `get_last_log` | Fetch the last run's GDB/valgrind output |
| `list_executors` | List execution modes and timeouts |

## Configuration

| Environment variable | Description |
|----------------------|-------------|
| `TRUN_BUILD_DIR` | Default build directory for the built-in suite |

Config: `~/.config/trun/`  
Logs: `~/.local/share/trun/last_run.log`
