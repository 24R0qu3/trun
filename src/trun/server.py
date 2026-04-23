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
from .playlist import (
    _data_add_tests,
    _data_create_playlist,
    _data_delete_playlist,
    _data_get_playlist,
    _data_list_playlists,
    _data_load_builtin,
    _data_load_playlist_file,
    _data_migrate_all_playlists,
    _data_migrate_playlist,
    _data_remove_tests,
)
from .runner import _data_run_tests

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
                                "description": (
                                    "Qt test function names to run. Omit to run all."
                                ),
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
            "Use after run_tests to inspect GDB backtraces, valgrind output, or test output."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to return (from the end of the file).",
                    "default": 200,
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
                max_lines = arguments.get("max_lines", 200)
                if LOG_FILE.exists():
                    lines = LOG_FILE.read_text().splitlines()
                    result = {
                        "lines": lines[-max_lines:],
                        "total_lines": len(lines),
                        "path": str(LOG_FILE),
                    }
                else:
                    result = {"lines": [], "total_lines": 0, "path": str(LOG_FILE)}
            case "list_executors":
                result = list_executors()
            case "migrate_playlist":
                result = await asyncio.to_thread(_data_migrate_playlist, arguments["name"])
            case "migrate_all_playlists":
                result = await asyncio.to_thread(_data_migrate_all_playlists)
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
