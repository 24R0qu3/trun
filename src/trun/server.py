from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import DEFAULT_BUILD, LOG_FILE, PLAYLISTS_DIR
from .executor import list_executors
from .history import _append_run_history, _get_run_history
from .log_analysis import aggregate_failures, filter_log_lines, parse_log
from .models import TestEntry
from .playlist import (
    _data_add_tests,
    _data_create_playlist,
    _data_create_playlist_from_dir,
    _data_delete_playlist,
    _data_get_groups,
    _data_get_playlist,
    _data_list_available_tests,
    _data_list_playlists,
    _data_load_builtin,
    _data_load_playlist_file,
    _data_migrate_all_playlists,
    _data_migrate_playlist,
    _data_remove_tests,
    _data_set_pipeline,
)
from .runner import _data_build, _data_get_test_cases, _data_run_tests

server = Server("trun")


def _load_entries(arguments: dict) -> list | dict:
    playlist = arguments.get("playlist")
    if playlist:
        path = Path(playlist)
        if not path.exists():
            named = PLAYLISTS_DIR / f"{playlist}.yaml"
            if not named.exists():
                ini_fallback = PLAYLISTS_DIR / f"{playlist}.ini"
                if ini_fallback.exists():
                    return {
                        "error": (
                            f"Playlist '{playlist}' is still in .ini format. "
                            f"Use migrate_playlist to convert it first."
                        )
                    }
                return {"error": f"Playlist '{playlist}' not found"}
            path = named
        entries = _data_load_playlist_file(str(path))
    else:
        entries = _data_load_builtin(arguments.get("build_dir", DEFAULT_BUILD))

    if only_tests := arguments.get("only_tests"):
        only_set = set(only_tests)
        entries = [e for e in entries if e.name in only_set]
        if not entries:
            return {"error": f"No matching tests found for only_tests={only_tests}"}

    return entries


def _make_progress_cb(session, token: str | int, total: int):
    passed = 0
    failed = 0

    async def on_result(result, done, _total):
        nonlocal passed, failed
        if result.status == "PASS":
            passed += 1
        elif result.status not in ("SKIP", "INTR"):
            failed += 1
        dur = f"{result.duration_secs:.0f}s" if result.duration_secs is not None else "?"
        if result.status == "PASS":
            icon = "✓"
        elif result.status in ("SKIP", "INTR"):
            icon = "~"
        else:
            icon = "✗"
        msg = f"{icon} [{done}/{total}] {result.name} {result.status} {dur} | ✓{passed} ✗{failed}"
        await session.send_progress_notification(token, done, total, message=msg)

    return on_result

_TOOLS = [
    Tool(
        name="run_tests",
        description=(
            "Run a test playlist or the built-in suite. "
            "Returns per-test status (PASS/FAIL/TIMEOUT/SKIP) and summary counts. "
            "Use get_last_log afterwards to retrieve detailed GDB/valgrind output."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "playlist": {
                    "type": "string",
                    "description": (
                        "Named playlist or path to .yaml file. Omit to run built-in suite."
                    ),
                },
                "build_dir": {
                    "type": "string",
                    "description": "Build directory (built-in suite only).",
                    "default": DEFAULT_BUILD,
                },
                "repeat": {
                    "type": "integer",
                    "description": "Number of repetitions.",
                    "default": 1,
                },
                "shuffle": {
                    "type": "boolean",
                    "description": "Randomize test order each round.",
                    "default": False,
                },
                "executor": {
                    "type": "string",
                    "description": (
                        "Override executor for all tests "
                        "(gdb/direct/valgrind/pytest). "
                        "Omit to use per-section setting from the playlist."
                    ),
                    "enum": ["gdb", "direct", "valgrind", "pytest"],
                },
                "stop_on_first_failure": {
                    "type": "boolean",
                    "description": (
                        "Stop the run immediately after the first FAIL/CRASH/TIMEOUT. "
                        "Useful for crash reproduction — no need to run all repetitions."
                    ),
                    "default": False,
                },
                "append": {
                    "type": "boolean",
                    "description": (
                        "Append to the existing log instead of clearing it. "
                        "Round numbering continues from the last logged round so "
                        "analyze_last_run reports combined failure rates across runs."
                    ),
                    "default": False,
                },
                "only_tests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Run only the listed test names from the playlist. "
                        "Exact match on the test binary name. "
                        "Omit to run all tests."
                    ),
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="list_playlists",
        description="List all saved test playlists.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_playlist",
        description=(
            "Get the full contents of a named playlist "
            "including per-test executor, timeout, and test_cases settings."
        ),
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Playlist name."}},
            "required": ["name"],
        },
    ),
    Tool(
        name="create_playlist",
        description="Create a new empty playlist file.",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Playlist name."}},
            "required": ["name"],
        },
    ),
    Tool(
        name="add_tests",
        description=(
            "Add tests to a playlist group. Creates the group section if it does not exist."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Playlist name."},
                "group": {"type": "string", "description": "Group/section name."},
                "build_dir": {"type": "string", "description": "Build directory for this group."},
                "subdir": {
                    "type": "string",
                    "description": "Test type: fast_running or long_running.",
                },
                "tests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Test binary name.",
                            },
                            "test_cases": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": ("Qt test function names to run. Omit to run all."),
                            },
                        },
                        "required": ["name"],
                    },
                    "description": "Tests to add. Each entry is {name, test_cases?}.",
                },
                "executor": {
                    "type": "string",
                    "description": "Executor for this group.",
                    "default": "gdb",
                },
                "timeout_fast": {
                    "type": "integer",
                    "description": "Override fast_running timeout in seconds.",
                },
                "timeout_long": {
                    "type": "integer",
                    "description": "Override long_running timeout in seconds.",
                },
            },
            "required": ["name", "group", "build_dir", "subdir", "tests"],
        },
    ),
    Tool(
        name="remove_tests",
        description="Remove specific tests from a playlist group.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Playlist name."},
                "group": {"type": "string", "description": "Group/section name."},
                "tests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Test names to remove.",
                },
            },
            "required": ["name", "group", "tests"],
        },
    ),
    Tool(
        name="delete_playlist",
        description="Delete a playlist by name.",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Playlist name."}},
            "required": ["name"],
        },
    ),
    Tool(
        name="get_last_log",
        description=(
            "Return the content of the most recent test run log. "
            "Use after run_tests to inspect GDB backtraces, valgrind output, or test output. "
            "Use test_filter or errors_only to reduce output size. Hard cap: 500 lines."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "max_lines": {
                    "type": "integer",
                    "description": "Lines to return (hard cap: 500, default: 200).",
                    "default": 200,
                },
                "test_filter": {
                    "type": "string",
                    "description": "Return only sections whose test name contains this string.",
                },
                "errors_only": {
                    "type": "boolean",
                    "description": "Skip PASS sections; return only FAIL/TIMEOUT/CRASH sections.",
                    "default": False,
                },
                "from_start": {
                    "type": "boolean",
                    "description": "Read from log start (default: tail). Use for early failures.",
                    "default": False,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="analyze_last_run",
        description=(
            "Parse the last run log and return structured error analysis per test: "
            "crash type, signal, assertion text, and user-code stack frames only "
            "(stdlib/Qt frames stripped). Much more compact than get_last_log. "
            "Use this instead of get_last_log when diagnosing failures."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "failed_only": {
                    "type": "boolean",
                    "description": "Only include failed/crashed tests (default: true).",
                    "default": True,
                },
                "aggregate": {
                    "type": "boolean",
                    "description": (
                        "Group failures by test name with flakiness rate (default true). "
                        "Set false for per-round detail."
                    ),
                    "default": True,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_run_history",
        description=(
            "Return summaries of the last N test runs. "
            "Use compute_flakiness=true to get a per-test pass/fail rate table across those runs. "
            "Use include_results=true to attach compact per-test status lists (higher token cost)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of recent runs to return (default: 10).",
                    "default": 10,
                },
                "compute_flakiness": {
                    "type": "boolean",
                    "description": (
                        "Compute aggregated pass/fail rate per test across the selected runs "
                        "(default: false)."
                    ),
                    "default": False,
                },
                "include_results": {
                    "type": "boolean",
                    "description": (
                        "Include per-test status lists in each run entry (default: false). "
                        "Higher token cost — omit unless you need individual run details."
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="list_executors",
        description=(
            "List available test execution modes with their names, descriptions, and timeouts."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="migrate_playlist",
        description=(
            "Migrate a single .ini playlist to YAML format and delete the .ini file. "
            "Use migrate_all_playlists to migrate all at once."
        ),
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Playlist name."}},
            "required": ["name"],
        },
    ),
    Tool(
        name="migrate_all_playlists",
        description="Migrate all .ini playlists to YAML format, deleting the old .ini files.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="run_and_analyze",
        description=(
            "Run a test playlist (or built-in suite) and return structured failure analysis "
            "in a single call — no need for a follow-up analyze_last_run. "
            "Returns summary counts plus aggregated failure details (crash type, signal, "
            "assertion, user-code frames). Raw per-test results are omitted to save tokens; "
            "use get_last_log if you need the full output."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "playlist": {
                    "type": "string",
                    "description": (
                        "Named playlist or path to .yaml file. Omit to run built-in suite."
                    ),
                },
                "build_dir": {
                    "type": "string",
                    "description": "Build directory (built-in suite only).",
                    "default": DEFAULT_BUILD,
                },
                "repeat": {
                    "type": "integer",
                    "description": "Number of repetitions.",
                    "default": 1,
                },
                "shuffle": {
                    "type": "boolean",
                    "description": "Randomize test order each round.",
                    "default": False,
                },
                "executor": {
                    "type": "string",
                    "description": "Override executor for all tests (gdb/direct/valgrind/pytest).",
                    "enum": ["gdb", "direct", "valgrind", "pytest"],
                },
                "stop_on_first_failure": {
                    "type": "boolean",
                    "description": (
                        "Stop immediately after the first FAIL/CRASH/TIMEOUT. "
                        "Useful for crash reproduction — no need to run all repetitions."
                    ),
                    "default": False,
                },
                "append": {
                    "type": "boolean",
                    "description": (
                        "Append to the existing log instead of clearing it. "
                        "Round numbering continues so failure rates are correct."
                    ),
                    "default": False,
                },
                "only_tests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Run only the listed test names from the playlist. "
                        "Exact match on the test binary name. "
                        "Omit to run all tests."
                    ),
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="run_single_test",
        description=(
            "Run a single test binary without needing a playlist. "
            "Returns the same result shape as run_tests. "
            "Use for quick one-shot re-runs after a fix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Test binary name."},
                "build_dir": {
                    "type": "string",
                    "description": "Build directory containing the binary.",
                },
                "subdir": {
                    "type": "string",
                    "description": "Test subdirectory (default: fast_running).",
                    "default": "fast_running",
                },
                "executor": {
                    "type": "string",
                    "description": "Executor to use (default: gdb).",
                    "enum": ["gdb", "direct", "valgrind", "pytest"],
                    "default": "gdb",
                },
                "test_cases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Qt test function names to run. Omit to run all.",
                },
            },
            "required": ["name", "build_dir"],
        },
    ),
    Tool(
        name="get_test_cases",
        description=(
            "List the Qt test function names available inside a test binary "
            "by running it with the -functions flag. Use the result to populate "
            "test_cases in add_tests for targeted re-runs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Test binary name."},
                "build_dir": {
                    "type": "string",
                    "description": "Build directory containing the binary.",
                },
                "subdir": {
                    "type": "string",
                    "description": "Test subdirectory (default: fast_running).",
                    "default": "fast_running",
                },
            },
            "required": ["name", "build_dir"],
        },
    ),
    Tool(
        name="list_available_tests",
        description=(
            "Discover test binary names available in a build directory by reading "
            "CTestTestfile.cmake. Returns test names grouped by subdir. "
            "Use before creating a playlist to know what tests exist."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "build_dir": {
                    "type": "string",
                    "description": "Path to the cmake build directory.",
                },
                "subdir": {
                    "type": "string",
                    "description": (
                        "Test subdirectory to scan (e.g. 'fast_running' or 'long_running'). "
                        "Omit to scan both."
                    ),
                },
            },
            "required": ["build_dir"],
        },
    ),
    Tool(
        name="create_playlist_from_dir",
        description=(
            "Discover tests from a CTestTestfile.cmake subdirectory and add them to a playlist. "
            "Reads {build_dir}/test/{subdir}/CTestTestfile.cmake, extracts all subdirs() entries "
            "(each is a test binary name), creates the playlist if it does not exist, and adds "
            "all discovered tests. Much faster than manual add_tests for large test directories."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Playlist name."},
                "build_dir": {
                    "type": "string",
                    "description": "Path to the cmake build directory.",
                },
                "subdir": {
                    "type": "string",
                    "description": "Test subdirectory, e.g. 'fast_running' or 'long_running'.",
                },
                "group": {
                    "type": "string",
                    "description": "Group name inside the playlist.",
                },
                "executor": {
                    "type": "string",
                    "description": "Executor name (default: gdb).",
                },
                "timeout_fast": {"type": "integer"},
                "timeout_long": {"type": "integer"},
            },
            "required": ["name", "build_dir", "subdir", "group"],
        },
    ),
    Tool(
        name="set_pipeline",
        description=(
            "Store build pipeline commands in a playlist group. "
            "Sets build_cmd (how to compile tests) and optionally configure_cmd "
            "(cmake/qmake setup). "
            "After this, build_tests and configure_build can run without repeating these commands."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "playlist": {"type": "string"},
                "group": {"type": "string"},
                "build_cmd": {
                    "type": "string",
                    "description": (
                        "Build command run in the group's build dir. "
                        "E.g. 'cmake --build . -j8', 'make -j4', 'ninja', 'cargo build --tests'."
                    ),
                },
                "configure_cmd": {
                    "type": "string",
                    "description": (
                        "Optional configure command "
                        "(e.g. 'cmake -S /src -B /build -DCMAKE_PREFIX_PATH=/opt/Qt'). "
                        "Use absolute paths — it runs in the server's working directory."
                    ),
                },
                "build_dir": {
                    "type": "string",
                    "description": "Build directory — only needed if the group does not exist yet.",
                },
            },
            "required": ["playlist", "group", "build_cmd"],
        },
    ),
    Tool(
        name="configure_build",
        description=(
            "Run the configure step. "
            "Playlist mode: reads configure_cmd from each group (skips groups without one); "
            "command runs in the server's working directory so use absolute paths. "
            "Explicit mode: provide build_dir and cmd. "
            "Call once before build_tests when the build directory does not exist yet."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "playlist": {
                    "type": "string",
                    "description": "Named playlist (reads configure_cmd per group).",
                },
                "group": {
                    "type": "string",
                    "description": "Limit to one group (playlist mode).",
                },
                "build_dir": {
                    "type": "string",
                    "description": "Explicit working directory. Provide this or playlist.",
                },
                "cmd": {"type": "string", "description": "Configure command (explicit mode)."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 600).",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="build_tests",
        description=(
            "Build test binaries. "
            "Playlist mode: reads build_cmd per group (defaults to 'cmake --build .' if not set); "
            "skips pytest groups; fails if the build dir does not exist "
            "(run configure_build first). "
            "Explicit mode: provide build_dir and optional cmd. "
            "Call before run_tests when sources have changed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "playlist": {
                    "type": "string",
                    "description": "Named playlist; builds all groups.",
                },
                "build_dir": {
                    "type": "string",
                    "description": "Explicit build directory. Provide this or playlist.",
                },
                "cmd": {
                    "type": "string",
                    "description": "Build command override (default: 'cmake --build .').",
                    "default": "cmake --build .",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout per build in seconds (default: 600).",
                },
            },
            "required": [],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        match name:
            case "run_tests":
                entries = await asyncio.to_thread(_load_entries, arguments)
                if isinstance(entries, dict):
                    return [TextContent(type="text", text=json.dumps(entries))]
                repeat = arguments.get("repeat", 1)
                ctx = server.request_context
                token = ctx.meta.progressToken if ctx.meta else None
                on_result = (
                    _make_progress_cb(ctx.session, token, len(entries) * repeat) if token else None
                )
                result: dict = await _data_run_tests(
                    entries,
                    repeat=repeat,
                    shuffle=arguments.get("shuffle", False),
                    executor_override=arguments.get("executor"),
                    on_result=on_result,
                    stop_on_first_failure=arguments.get("stop_on_first_failure", False),
                    append=arguments.get("append", False),
                )
                MAX_RESULT_ENTRIES = 500
                entries_list = result.get("results", [])
                if len(entries_list) > MAX_RESULT_ENTRIES:
                    result["results"] = entries_list[:MAX_RESULT_ENTRIES]
                    result["results_truncated"] = True
                    result["results_total"] = len(entries_list)
                    result["warning"] = (
                        f"Results truncated: showing {MAX_RESULT_ENTRIES} of"
                        f" {len(entries_list)} entries."
                        " Later rounds may be missing from analysis."
                    )
                await asyncio.to_thread(_append_run_history, arguments.get("playlist"), result)
            case "run_and_analyze":
                entries = await asyncio.to_thread(_load_entries, arguments)
                if isinstance(entries, dict):
                    return [TextContent(type="text", text=json.dumps(entries))]
                repeat = arguments.get("repeat", 1)
                ctx = server.request_context
                token = ctx.meta.progressToken if ctx.meta else None
                on_result = (
                    _make_progress_cb(ctx.session, token, len(entries) * repeat) if token else None
                )
                run_result = await _data_run_tests(
                    entries,
                    repeat=repeat,
                    shuffle=arguments.get("shuffle", False),
                    executor_override=arguments.get("executor"),
                    on_result=on_result,
                    stop_on_first_failure=arguments.get("stop_on_first_failure", False),
                    append=arguments.get("append", False),
                )
                result = {
                    "passed": run_result["passed"],
                    "failed": run_result["failed"],
                    "skipped": run_result["skipped"],
                    "total_secs": run_result["total_secs"],
                    "log_file": run_result["log_file"],
                }
                if LOG_FILE.exists():
                    analysis = parse_log(LOG_FILE.read_text())
                    total_rounds = analysis["summary"].get("total_rounds", 1)
                    failures = [t for t in analysis["tests"] if t["status"] != "PASS"]
                    result["failures"] = aggregate_failures(failures, total_rounds)
                    result["total_rounds"] = total_rounds
                await asyncio.to_thread(_append_run_history, arguments.get("playlist"), run_result)
            case "list_playlists":
                result = await asyncio.to_thread(_data_list_playlists)
            case "get_playlist":
                result = await asyncio.to_thread(_data_get_playlist, arguments["name"])
            case "create_playlist":
                result = await asyncio.to_thread(_data_create_playlist, arguments["name"])
            case "add_tests":
                result = await asyncio.to_thread(
                    _data_add_tests,
                    arguments["name"],
                    arguments["group"],
                    arguments["build_dir"],
                    arguments["subdir"],
                    arguments["tests"],
                    arguments.get("executor", "gdb"),
                    arguments.get("timeout_fast"),
                    arguments.get("timeout_long"),
                )
            case "remove_tests":
                result = await asyncio.to_thread(
                    _data_remove_tests,
                    arguments["name"],
                    arguments["group"],
                    arguments["tests"],
                )
            case "delete_playlist":
                result = await asyncio.to_thread(_data_delete_playlist, arguments["name"])
            case "get_last_log":
                max_lines = min(arguments.get("max_lines", 200), 500)
                test_filter = arguments.get("test_filter")
                errors_only = arguments.get("errors_only", False)
                from_start = arguments.get("from_start", False)
                if LOG_FILE.exists():
                    lines = LOG_FILE.read_text().splitlines()
                    total = len(lines)
                    if test_filter or errors_only:
                        lines = filter_log_lines(lines, test_filter, errors_only)
                    selected = lines[:max_lines] if from_start else lines[-max_lines:]
                    result = {
                        "lines": selected,
                        "total_lines": total,
                        "filtered_lines": len(lines),
                        "capped": len(lines) > max_lines,
                        "path": str(LOG_FILE),
                    }
                else:
                    result = {
                        "lines": [],
                        "total_lines": 0,
                        "filtered_lines": 0,
                        "capped": False,
                        "path": str(LOG_FILE),
                    }
            case "analyze_last_run":
                failed_only = arguments.get("failed_only", True)
                aggregate = arguments.get("aggregate", True)
                if LOG_FILE.exists():
                    analysis = parse_log(LOG_FILE.read_text())
                    total_rounds = analysis["summary"].get("total_rounds", 1)
                    if failed_only:
                        analysis["tests"] = [t for t in analysis["tests"] if t["status"] != "PASS"]
                    if aggregate:
                        analysis["tests"] = aggregate_failures(analysis["tests"], total_rounds)
                    result = analysis
                else:
                    result = {"tests": [], "summary": {"total": 0, "passed": 0, "failed": 0}}
            case "get_run_history":
                result = await asyncio.to_thread(
                    _get_run_history,
                    arguments.get("n", 10),
                    arguments.get("compute_flakiness", False),
                    arguments.get("include_results", False),
                )
            case "list_executors":
                result = list_executors()
            case "migrate_playlist":
                result = await asyncio.to_thread(_data_migrate_playlist, arguments["name"])
            case "migrate_all_playlists":
                result = await asyncio.to_thread(_data_migrate_all_playlists)
            case "run_single_test":
                entry = TestEntry(
                    name=arguments["name"],
                    subdir=arguments.get("subdir", "fast_running"),
                    build_dir=arguments["build_dir"],
                    group="single",
                    executor=arguments.get("executor", "gdb"),
                    test_cases=arguments.get("test_cases", []),
                )
                ctx = server.request_context
                token = ctx.meta.progressToken if ctx.meta else None
                on_result = _make_progress_cb(ctx.session, token, 1) if token else None
                result = await _data_run_tests([entry], on_result=on_result)
                await asyncio.to_thread(_append_run_history, None, result)
            case "get_test_cases":
                result = await _data_get_test_cases(
                    arguments["name"],
                    arguments["build_dir"],
                    arguments.get("subdir", "fast_running"),
                )
            case "list_available_tests":
                result = await asyncio.to_thread(
                    _data_list_available_tests,
                    arguments["build_dir"],
                    arguments.get("subdir"),
                )
            case "create_playlist_from_dir":
                result = await asyncio.to_thread(
                    _data_create_playlist_from_dir,
                    arguments["name"],
                    arguments["build_dir"],
                    arguments["subdir"],
                    arguments["group"],
                    arguments.get("executor", "gdb"),
                    arguments.get("timeout_fast"),
                    arguments.get("timeout_long"),
                )
            case "set_pipeline":
                result = await asyncio.to_thread(
                    _data_set_pipeline,
                    arguments["playlist"],
                    arguments["group"],
                    arguments["build_cmd"],
                    arguments.get("configure_cmd"),
                    arguments.get("build_dir"),
                )
            case "configure_build":
                timeout = arguments.get("timeout", 600)
                build_dir = arguments.get("build_dir")
                if build_dir:
                    cmd = arguments.get("cmd")
                    if not cmd:
                        result = {"error": "Provide cmd when using explicit build_dir"}
                    else:
                        r = await _data_build(cwd=None, cmd=cmd, timeout=timeout)
                        failed = int(r["status"] == "FAIL")
                        result = {
                            "results": [{"build_dir": build_dir, **r}],
                            "total": 1,
                            "failed": failed,
                        }
                elif playlist_name := arguments.get("playlist"):
                    groups = await asyncio.to_thread(_data_get_groups, playlist_name)
                    if isinstance(groups, dict):
                        result = groups
                    else:
                        group_filter = arguments.get("group")
                        results = []
                        for grp in groups:
                            if group_filter and grp["name"] != group_filter:
                                continue
                            configure_cmd = grp.get("configure_cmd")
                            if not configure_cmd:
                                continue
                            r = await _data_build(cwd=None, cmd=configure_cmd, timeout=timeout)
                            results.append(
                                {"group": grp["name"], "build_dir": grp.get("build"), **r}
                            )
                        failed = sum(1 for r in results if r["status"] == "FAIL")
                        result = {"results": results, "total": len(results), "failed": failed}
                else:
                    result = {"error": "Provide playlist or build_dir + cmd"}
            case "build_tests":
                timeout = arguments.get("timeout", 600)
                build_dir = arguments.get("build_dir")
                cmd_override = arguments.get("cmd")
                if build_dir:
                    if not Path(build_dir).is_dir():
                        err = f"Build dir not found: {build_dir} — run configure_build first"
                        result = {"error": err}
                    else:
                        cmd = cmd_override or "cmake --build ."
                        r = await _data_build(cwd=build_dir, cmd=cmd, timeout=timeout)
                        failed = int(r["status"] == "FAIL")
                        result = {
                            "builds": [{"build_dir": build_dir, **r}],
                            "total": 1,
                            "failed": failed,
                        }
                elif playlist_name := arguments.get("playlist"):
                    groups = await asyncio.to_thread(_data_get_groups, playlist_name)
                    if isinstance(groups, dict):
                        result = groups
                    else:
                        builds = []
                        for grp in groups:
                            if grp.get("executor") == "pytest":
                                continue
                            bd = grp.get("build")
                            if bd and not Path(bd).is_dir():
                                builds.append({
                                    "group": grp["name"],
                                    "build_dir": bd,
                                    "status": "FAIL",
                                    "duration_secs": 0,
                                    "output": (
                                        f"Build dir not found: {bd}"
                                        " — run configure_build first"
                                    ),
                                })
                                continue
                            cmd = cmd_override or grp.get("build_cmd") or "cmake --build ."
                            r = await _data_build(cwd=bd, cmd=cmd, timeout=timeout)
                            builds.append({"group": grp["name"], "build_dir": bd, **r})
                        failed = sum(1 for b in builds if b["status"] == "FAIL")
                        result = {"builds": builds, "total": len(builds), "failed": failed}
                else:
                    result = {"error": "Provide playlist or build_dir"}
            case _:
                result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def main() -> None:
    print("Starting trun MCP server...", file=sys.stderr)
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
