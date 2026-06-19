# trun — Feature List

## Done

### Playlist management
- Create / delete named playlists (YAML)
- Add / remove tests per group with per-group executor, timeout, build dir
- Discover tests from `CTestTestfile.cmake` — MCP (`create_playlist_from_dir`) and CLI (`trun playlist add-from-dir`)
- Migrate legacy `.ini` playlists to YAML (`migrate_playlist`, `migrate_all_playlists`)
- Inspect playlist contents (`get_playlist`, `list_playlists`)

### Test execution
- Run playlists or the built-in suite (`run_tests`)
- Single test quick-run without a playlist — MCP (`run_single_test`) and CLI (`trun single`)
- Executors: `gdb`, `direct`, `valgrind`, `pytest`
- Repeat runs, shuffle order, stop on first failure, append log mode — on both MCP and CLI (`--repeat`, `--shuffle`, `--stop-on-first-failure`, `--append`)
- Per-test timeout with executor defaults per subdir
- `only_tests` filter (MCP) / `--only` (CLI) — run a subset of a playlist without editing it
- `trun run` / `trun single` exit non-zero on failure (CI / pre-commit gating)
- Build-dir resolution: `--build` > `$TRUN_BUILD_DIR` > `trun config set build_dir`
- Progress notifications via MCP progress tokens

### Analysis
- Structured failure analysis with crash type, signal, assertion, user frames (`analyze_last_run`)
- Combined run+analyze in one call (`run_and_analyze`)
- Per-test `error_hint` for signal/assertion/Qt-fail and valgrind error summaries
- Raw log access with line cap, test filter, errors-only mode (`get_last_log`)
- Run history with flakiness rates across N runs (`get_run_history`)
- GDB noise filtering, head+tail output truncation (keeps the backtrace)

### Build pipeline
- Store `build_cmd` + `configure_cmd` per playlist group (`set_pipeline`)
- Run configure step from stored or explicit command (`configure_build`)
- Build test binaries from stored or explicit command (`build_tests`)
- Playlist mode (all groups) and explicit mode (`build_dir` + `cmd`)
- Skips pytest groups automatically in `build_tests`
- Live build progress: compact `[N/Total]` ninja step counter via MCP progress notifications
  (same minimal style as the test-run stream; raw output stays buffered, not streamed)
- `rebuild` — clear the stale CMake cache (`CMakeCache.txt` + `CMakeFiles/`), then configure +
  build in one call (`clean=cache|none`)

### Targeted test cases
- List available Qt test functions inside a binary (`get_test_cases`)
- Store selected test cases per test entry in a playlist for targeted reruns
- pytest groups map stored test cases to `-k "a or b"` (Python targeting, not just C++/Qt)

### Infrastructure
- MCP server (`trun mcp`)
- CLI entry point (`trun`) with `--version` wired to the package version
- Persistent config (`trun config set`) in `config.json`
- Run history persistence (JSON), written atomically
- MCP run lock — overlapping runs can't interleave the shared log
- Log file with round numbering for multi-run appends

---

## Planned / Ideas

_None open._ (Full-tree wipe for `rebuild` was intentionally not built — cache-clean covers the
stale-cache / changed-flags case; add a `clean=full` option only if a real need appears.)
