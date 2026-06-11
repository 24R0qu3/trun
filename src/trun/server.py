from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from rich.console import Console

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
    _data_get_playlist,
    _data_list_available_tests,
    _data_list_playlists,
    _data_load_builtin,
    _data_load_playlist_file,
    _data_migrate_all_playlists,
    _data_migrate_playlist,
    _data_remove_tests,
)
from .runner import _data_get_test_cases, _data_run_tests

console = Console(stderr=True)

server = Server("trun")

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
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        match name:
            case "run_tests":
                playlist = arguments.get("playlist")
                if playlist:
                    path = Path(playlist)
                    if not path.exists():
                        named = PLAYLISTS_DIR / f"{playlist}.yaml"
                        if not named.exists():
                            ini_fallback = PLAYLISTS_DIR / f"{playlist}.ini"
                            if ini_fallback.exists():
                                result: dict = {
                                    "error": (
                                        f"Playlist '{playlist}' is still in .ini format. "
                                        f"Use migrate_playlist to convert it first."
                                    )
                                }
                            else:
                                result = {"error": f"Playlist '{playlist}' not found"}
                            return [TextContent(type="text", text=json.dumps(result))]
                        path = named
                    entries = await asyncio.to_thread(_data_load_playlist_file, str(path))
                else:
                    build_dir = arguments.get("build_dir", DEFAULT_BUILD)
                    entries = await asyncio.to_thread(_data_load_builtin, build_dir)
                result = await _data_run_tests(
                    entries,
                    repeat=arguments.get("repeat", 1),
                    shuffle=arguments.get("shuffle", False),
                    executor_override=arguments.get("executor"),
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
                playlist = arguments.get("playlist")
                if playlist:
                    path = Path(playlist)
                    if not path.exists():
                        named = PLAYLISTS_DIR / f"{playlist}.yaml"
                        if not named.exists():
                            ini_fallback = PLAYLISTS_DIR / f"{playlist}.ini"
                            if ini_fallback.exists():
                                result = {
                                    "error": (
                                        f"Playlist '{playlist}' is still in .ini format. "
                                        f"Use migrate_playlist to convert it first."
                                    )
                                }
                            else:
                                result = {"error": f"Playlist '{playlist}' not found"}
                            return [TextContent(type="text", text=json.dumps(result))]
                        path = named
                    entries = await asyncio.to_thread(_data_load_playlist_file, str(path))
                else:
                    build_dir = arguments.get("build_dir", DEFAULT_BUILD)
                    entries = await asyncio.to_thread(_data_load_builtin, build_dir)
                run_result = await _data_run_tests(
                    entries,
                    repeat=arguments.get("repeat", 1),
                    shuffle=arguments.get("shuffle", False),
                    executor_override=arguments.get("executor"),
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
                result = await _data_run_tests([entry])
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
            case _:
                result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def main() -> None:
    console.print("[dim]Starting trun MCP server...[/dim]")
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
