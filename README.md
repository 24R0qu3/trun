# trun

**Test runner CLI + MCP server for C++ (GDB / Valgrind) and Python (pytest) test suites.**

trun groups tests into *playlists* — each group pins its own executor, build directory, and
timeouts — then runs them from the terminal **or** exposes them as MCP tools so an AI agent can
build, run, and diagnose tests on its own.

- **For humans:** a `trun` CLI with crash backtraces, valgrind, repeat/shuffle, and CI-friendly
  exit codes.
- **For AI agents:** MCP tools that return *structured* failure analysis — crash type, signal,
  assertion text, and user-code stack frames (stdlib/Qt noise stripped) — instead of raw logs.
  Cheap to read, easy to act on.

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Core concepts](#core-concepts)
- [CLI reference](#cli-reference)
- [Playlist format](#playlist-format)
- [Executors](#executors) · [Result states](#result-states)
- [MCP server (for AI agents)](#mcp-server-for-ai-agents) — [tools](#mcp-tools) · [agent playbook](#agent-playbook) · [tool parameters](#tool-parameters)
- [Configuration](#configuration)

## Install

```bash
pipx install git+ssh://git@github.com/24R0qu3/trun.git
```

From a local clone:

```bash
git clone git@github.com:24R0qu3/trun.git
cd trun
pipx install .
```

Development (editable install with test deps):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

## Quick start

trun needs to know where your **built** test binaries live. Set a build directory, define a
playlist, run it.

```bash
# 1. Set a default build directory once (or pass --build per run, or set $TRUN_BUILD_DIR)
trun config set build_dir /path/to/build/CC_V1X90-Debug

# 2. Create a playlist and add a test to a group
trun playlist create smoke
trun playlist add smoke --group OSB --build /path/to/build/CC_V1X90-Debug \
    --type fast_running rst_tfr_rst_smc_osb

# 3. Run it — exits non-zero if anything fails, so it is safe in CI / pre-commit
trun run --playlist smoke
```

One-off without a playlist:

```bash
trun single rst_tfr_rst_smc_osb --build /path/to/build/CC_V1X90-Debug
```

More ways to run:

```bash
trun run --playlist smoke --executor valgrind      # force valgrind on every test
trun run --playlist smoke --repeat 5 --shuffle     # flakiness hunt
trun run --playlist smoke --stop-on-first-failure  # stop at the first crash
trun run --playlist smoke --only rst_tfr_rst_smc_osb   # run a subset, no editing
```

> Bulk-import a whole cmake test directory instead of adding tests one by one:
> `trun playlist add-from-dir smoke --group OSB --build DIR --subdir fast_running`

## Core concepts

| Term | What it is |
|------|------------|
| **Playlist** | A YAML file (`~/.config/trun/playlists/<name>.yaml`) listing test *groups*. |
| **Group** | A set of tests sharing one build dir, executor, and timeout pair. |
| **Executor** | How a test is launched: `gdb` (backtrace on crash), `direct`, `valgrind`, `pytest`. |
| **Build dir** | C++: the cmake build root — binaries resolve to `<build>/test/<subdir>/<name>/<name>`. pytest: the project root — the test path is `<build>/<name>`. |
| **subdir** | `fast_running` or `long_running`. Controls default timeouts and (for C++) the lookup path. |

**Build-dir resolution order:** `--build` (or MCP `build_dir`) → `$TRUN_BUILD_DIR` →
`trun config set build_dir`. If none resolve, trun stops with a clear error rather than running
against a bad path.

> `trun run` with no `--playlist` runs a **built-in suite** — a fixed list of the author's C++
> project tests. It is only useful against that project's build; everyone else should use
> playlists.

## CLI reference

```
trun run [--playlist NAME_OR_PATH] [--build DIR] [--repeat N] [--shuffle]
         [--executor gdb|direct|valgrind|pytest]
         [--stop-on-first-failure] [--only TEST ...] [--append]

trun single <name> --build DIR [--subdir fast_running|long_running]
                   [--executor gdb|direct|valgrind|pytest]
                   [--test-cases FUNC1[,FUNC2,...]]

trun playlist list
trun playlist show     <name>
trun playlist create   <name>
trun playlist add      <name> --group G --build DIR --type fast_running|long_running
                              [--executor gdb|direct|valgrind|pytest]
                              [--timeout-fast SECS] [--timeout-long SECS]
                              [--test-cases FUNC1[,FUNC2,...]]
                              <test1> [test2 ...]
trun playlist add-from-dir <name> --group G --build DIR --subdir fast_running|long_running
                              [--executor ...] [--timeout-fast SECS] [--timeout-long SECS]
trun playlist remove-tests <name> --group G <test1> [test2 ...]
trun playlist delete   <name>
trun playlist migrate  [<name>]

trun config set build_dir DIR    # persist a default build dir (written to config.json)
trun executors                   # list executors and their timeouts
trun mcp [--print-config]        # run the MCP stdio server / print Claude config
trun patch-claude [--remove]     # register/unregister trun in ~/.claude.json
```

`trun run` and `trun single` **exit non-zero when any test fails** — use them directly in CI and
git hooks. `--only` filters by exact test-binary name; `--append` continues round numbering in
the log so failure rates accumulate across runs.

## Playlist format

Playlists are `.yaml` files in `~/.config/trun/playlists/`.

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

**Group fields**

| Field | Default | Description |
|-------|---------|-------------|
| `name` | required | Group identifier |
| `build` | required | Build root (cmake build dir or project root) |
| `executor` | `gdb` | How to run: `gdb`, `direct`, `valgrind`, `pytest` |
| `timeout_fast` | executor default | Timeout (s) for `fast_running` tests |
| `timeout_long` | executor default | Timeout (s) for `long_running` tests |
| `build_cmd` / `configure_cmd` | — | Optional build pipeline commands (see [build tools](#mcp-tools)) |

**Test entry fields**

| Field | Default | Description |
|-------|---------|-------------|
| `name` | required | Test binary name (C++) or test path (pytest) |
| `subdir` | required | `fast_running` or `long_running` |
| `test_cases` | — | Specific functions to run; omit to run all. C++/Qt → passed as args; pytest → mapped to `-k "a or b"` |

The CLI `--executor` flag overrides per-group executors for a single run.

### Migrating from the old `.ini` format

```bash
trun playlist migrate          # migrate all .ini playlists
trun playlist migrate <name>   # migrate a single playlist
```

## Executors

| Executor | Command | fast timeout | long timeout |
|----------|---------|-------------|-------------|
| `gdb` | `gdb --return-child-result -batch -ex run -ex bt -ex quit <bin>` | 60 s | 180 s |
| `direct` | `<bin>` | 60 s | 180 s |
| `valgrind` | `valgrind --leak-check=full --error-exitcode=1 <bin>` | 120 s | 360 s |
| `pytest` | `pytest <path> -v` (adds `-k "a or b"` when `test_cases` set) | 60 s | 180 s |

GDB output has thread attach/detach noise filtered out and is capped at 300 lines per test —
kept as **head + tail**, so the crash backtrace at the end is never truncated away. Override
timeouts per group with `timeout_fast` / `timeout_long`, or per run with `--executor`.

## Result states

| State | Meaning |
|-------|---------|
| `PASS` | Process exited 0 with no crash signal in output |
| `FAIL` | Process exited non-zero, or binary not found |
| `CRASH` | Process exited 0 but a crash signal was detected in GDB output |
| `TIMEOUT` | Exceeded the time limit |
| `INTR` | Run was interrupted |

## MCP server (for AI agents)

Register trun as an MCP server in Claude Code:

```bash
trun patch-claude
# then restart Claude Code
```

Or add the output of `trun mcp --print-config` to `~/.claude.json` manually.

Every run tool also emits **MCP progress notifications** (`✓3 ✗1`-style per-test updates) when
the client passes a progress token.

### MCP tools

| Tool | Description |
|------|-------------|
| `run_tests` | Run a playlist or built-in suite. Per-test `status`, `error_hint`, `predecessor`; capped at 500 entries. |
| `run_and_analyze` | **One-shot diagnosis:** run + structured failure analysis in a single call. Omits raw results to save tokens. |
| `run_single_test` | Run one binary directly, no playlist. |
| `analyze_last_run` | Parse the last run log into structured per-test/aggregated analysis: crash type, signal, assertion, user frames, flakiness. |
| `get_last_log` | Raw last-run log (hard cap 500 lines; `test_filter`, `errors_only`, `from_start`). |
| `get_run_history` | Summaries of the last N runs; `compute_flakiness=true` adds a per-test pass/fail table. |
| `list_available_tests` | Discover test binary names from a build dir's `CTestTestfile.cmake`. |
| `get_test_cases` | List Qt test functions inside a binary (runs it with `-functions`). |
| `create_playlist_from_dir` | Bulk-add every test in a `CTestTestfile.cmake` subdir to a playlist. |
| `list_playlists` · `get_playlist` | Browse saved playlists. |
| `create_playlist` · `add_tests` · `remove_tests` · `delete_playlist` | Edit playlists (`add_tests` supports `test_cases` per entry). |
| `migrate_playlist` · `migrate_all_playlists` | Convert `.ini` playlists to YAML. |
| `set_pipeline` | Store `build_cmd` (+ optional `configure_cmd`) on a playlist group. |
| `configure_build` | Run the configure step — per-group `configure_cmd`, or explicit `build_dir`+`cmd`. |
| `build_tests` | Build test binaries — per-group `build_cmd` (skips pytest groups), or explicit `build_dir`+`cmd`. |
| `rebuild` | Clear the stale CMake cache, then configure + build in one call (`clean=cache\|none`). |
| `list_executors` | List execution modes and timeouts. |

`configure_build` / `build_tests` / `rebuild` emit live build progress as a compact `[N/Total]`
ninja step counter (same minimal style as the test-run stream — raw compiler output is **not**
streamed, only returned at the end).

### Agent playbook

A typical autonomous debugging loop:

1. **Discover** — `list_available_tests` (or `create_playlist_from_dir`) to learn what exists and
   build a playlist.
2. **Diagnose in one call** — `run_and_analyze`. The returned `failures` array already has crash
   type, signal, assertion, and user-code frames; no follow-up needed for most cases.
3. **Dig deeper if needed** — `analyze_last_run` (aggregated or per-round) or `get_last_log`
   (`errors_only=true` to skip passes).
4. **Reproduce a crash fast** — `run_tests` with `stop_on_first_failure=true`, or
   `repeat` + `shuffle` to surface flakiness; `get_run_history compute_flakiness=true` for rates.
5. **Narrow the scope** — `get_test_cases` → `add_tests` with `test_cases` (or `only_tests` on a
   run) to re-run just the failing function.
6. **Rebuild after a fix** — `set_pipeline` once, then `configure_build` / `build_tests`, then
   re-run.

Prefer `run_and_analyze` over `run_tests` + `get_last_log` — it returns less and tells you more.

### Tool parameters

#### `run_tests` / `run_and_analyze`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `playlist` | — | Named playlist or path; omit for built-in suite |
| `build_dir` | resolved | Build dir for the built-in suite (`build_dir` → `$TRUN_BUILD_DIR` → config) |
| `repeat` | 1 | Number of repetitions |
| `shuffle` | false | Randomize test order each round |
| `executor` | — | Override executor for all tests |
| `only_tests` | — | Run only these test binary names (exact match) |
| `stop_on_first_failure` | false | Stop after the first FAIL/CRASH/TIMEOUT |
| `append` | false | Append to the existing log; round numbering continues |

`run_and_analyze` returns summary counts (`passed`, `failed`, `skipped`, `total_secs`) plus a
`failures` array (same shape as `analyze_last_run` aggregated). The raw per-test `results` array
is omitted; call `get_last_log` if you need it.

#### `analyze_last_run`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failed_only` | true | Only include non-PASS tests |
| `aggregate` | true | Group failures by test name with flakiness rate; false for per-round detail |

Aggregated entries include: `name`, `failure_rate` (e.g. `2/5`), `failed_rounds`, `status`,
`crash_type`, `signal`, `assertion`, `user_frames`, `user_frames_note` (when stack variants
differ), `qt_failures`, `valgrind_errors`, and `raw_frames` (system/Qt frames when no user
frames are available).

#### `get_last_log`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_lines` | 200 | Lines to return (hard cap 500) |
| `test_filter` | — | Only sections whose test name contains this string |
| `errors_only` | false | Skip PASS sections; show only FAIL/TIMEOUT/CRASH |
| `from_start` | false | Read from the log start instead of the tail (use when early tests failed) |

#### `run_single_test`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | required | Test binary name |
| `build_dir` | required | Build directory containing the binary |
| `subdir` | `fast_running` | Test subdirectory |
| `executor` | `gdb` | Executor to use |
| `test_cases` | — | Functions to run; omit to run all |

#### `create_playlist_from_dir`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | required | Playlist name (created if missing) |
| `build_dir` | required | Path to the cmake build directory |
| `subdir` | required | `fast_running` or `long_running` |
| `group` | required | Group name inside the playlist |
| `executor` | `gdb` | Executor for this group |
| `timeout_fast` / `timeout_long` | — | Optional timeout overrides (s) |

Reads `{build_dir}/test/{subdir}/CTestTestfile.cmake` and adds every `subdirs("...")` entry.

#### `list_available_tests`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `build_dir` | required | Path to the cmake build directory |
| `subdir` | — | `fast_running` or `long_running`; omit to scan both |

Returns `{"build_dir": "...", "tests": {"fast_running": [...], "long_running": [...]}}`.

#### `get_test_cases`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | required | Test binary name |
| `build_dir` | required | Build directory containing the binary |
| `subdir` | `fast_running` | Test subdirectory |

Runs `binary -functions`; feed the result into `add_tests` `test_cases` for targeted re-runs.

#### `get_run_history`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `n` | 10 | Recent runs to return (most-recent first) |
| `compute_flakiness` | false | Per-test pass/fail rates across the selected runs |
| `include_results` | false | Attach per-test status lists (higher token cost) |

History is appended after every `run_tests`, `run_single_test`, and `run_and_analyze` call.

## Configuration

| Source | What it sets | Notes |
|--------|--------------|-------|
| `trun config set build_dir DIR` | Default build dir | Persisted to `config.json` |
| `$TRUN_BUILD_DIR` | Default build dir | Overrides config.json |
| `--build` / MCP `build_dir` | Default build dir | Overrides everything, per run |

Concurrent MCP runs are serialized so they cannot interleave the shared log; history is written
atomically.

**Paths**

```
Config:    ~/.config/trun/                       (config.json, playlists/)
Logs:      ~/.local/share/trun/last_run.log
History:   ~/.local/share/trun/run_history.jsonl (capped at 50 entries)
```
