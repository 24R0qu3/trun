from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .config import LOG_FILE, PLAYLISTS_DIR, resolve_build_dir, set_config
from .executor import list_executors
from .models import TestEntry
from .playlist import (
    _data_add_tests,
    _data_create_playlist,
    _data_create_playlist_from_dir,
    _data_delete_playlist,
    _data_get_playlist,
    _data_list_playlists,
    _data_load_builtin,
    _data_load_playlist_file,
    _data_migrate_all_playlists,
    _data_migrate_playlist,
    _data_remove_tests,
)
from .runner import _data_run_tests, fmt_duration

console = Console()


def _fail(msg: str) -> None:
    console.print(f"[red]Error:[/red] {msg}")
    raise SystemExit(1)


def _ok(msg: str) -> None:
    console.print(f"[green]OK[/green] {msg}")


# ── run ────────────────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> None:
    if args.playlist:
        path = Path(args.playlist)
        if not path.exists():
            named = PLAYLISTS_DIR / f"{args.playlist}.yaml"
            if not named.exists():
                ini_fallback = PLAYLISTS_DIR / f"{args.playlist}.ini"
                if ini_fallback.exists():
                    _fail(
                        f"Playlist '{args.playlist}' is still in .ini format. "
                        f"Run: trun playlist migrate {args.playlist}"
                    )
                _fail(
                    f"Playlist '{args.playlist}' not found "
                    "(tried as file path and as named playlist)"
                )
            path = named
        try:
            entries = _data_load_playlist_file(str(path))
        except Exception as e:
            _fail(str(e))
    else:
        build = resolve_build_dir(args.build)
        if not build:
            _fail(
                "No build directory. Pass --build DIR, set TRUN_BUILD_DIR, "
                "run 'trun config set build_dir DIR', or use --playlist."
            )
        entries = _data_load_builtin(build_dir=build)

    if args.only:
        only = set(args.only)
        entries = [e for e in entries if e.name in only]
        if not entries:
            _fail(f"No matching tests for --only {args.only}")

    if not entries:
        _fail("No tests to run.")

    _execute(
        entries,
        repeat=args.repeat,
        shuffle=args.shuffle,
        executor=args.executor,
        stop=args.stop_on_first_failure,
        append=args.append,
    )


def _execute(
    entries: list,
    *,
    repeat: int = 1,
    shuffle: bool = False,
    executor: str | None = None,
    stop: bool = False,
    append: bool = False,
) -> None:
    console.print(f"[bold]Tests  :[/bold] {len(entries)}")
    console.print(f"[bold]Repeat :[/bold] {repeat}")
    console.print(f"[bold]Shuffle:[/bold] {shuffle}")
    if executor:
        console.print(f"[bold]Executor override:[/bold] {executor}")
    console.print(f"[bold]Log    :[/bold] {LOG_FILE}")
    console.print()

    results_so_far: list = []
    t_start = time.monotonic()

    async def on_result(r, done, total):
        results_so_far.append(r)
        status_style = {
            "PASS": "green",
            "FAIL": "red",
            "TIMEOUT": "yellow",
            "SKIP": "dim",
            "INTR": "dim",
        }.get(r.status, "")
        dur = fmt_duration(r.duration_secs) if r.duration_secs is not None else "-"
        console.print(
            f"  [{status_style}]{r.status:<7}[/{status_style}]  "
            f"[dim]{r.group:<28}[/dim]  {r.name}  [dim]{dur}[/dim]"
        )

    try:
        result = asyncio.run(
            _data_run_tests(
                entries,
                repeat=repeat,
                shuffle=shuffle,
                executor_override=executor,
                on_result=on_result,
                stop_on_first_failure=stop,
                append=append,
            )
        )
    except KeyboardInterrupt:
        elapsed = int(time.monotonic() - t_start)
        console.print("\n[yellow]Interrupted.[/yellow]")
        result = {
            "results": [
                {
                    "name": r.name,
                    "group": r.group,
                    "status": r.status,
                    "duration_secs": r.duration_secs,
                    "round": r.round_num,
                }
                for r in results_so_far
            ],
            "total_secs": elapsed,
            "passed": sum(1 for r in results_so_far if r.status == "PASS"),
            "failed": sum(1 for r in results_so_far if r.status in ("FAIL", "TIMEOUT")),
            "skipped": sum(1 for r in results_so_far if r.status in ("SKIP", "INTR")),
            "log_file": str(LOG_FILE),
        }

    _print_summary(result, repeat > 1)

    if result["failed"]:
        raise SystemExit(1)


def cmd_single(args: argparse.Namespace) -> None:
    build = resolve_build_dir(args.build)
    if not build:
        _fail("No build directory. Pass --build DIR or set a default with 'trun config'.")
    entry = TestEntry(
        name=args.name,
        subdir=args.subdir,
        build_dir=build,
        group="single",
        executor=args.executor,
        test_cases=args.test_cases or [],
    )
    _execute([entry])


def _print_summary(result: dict, show_round: bool) -> None:
    console.print()
    t = Table(show_header=True, header_style="bold")
    if show_round:
        t.add_column("Run", style="dim", width=4)
    t.add_column("Group", style="dim", max_width=28)
    t.add_column("Test", max_width=48)
    t.add_column("Status", width=8)
    t.add_column("Time", width=10)

    for r in result["results"]:
        dur = fmt_duration(r["duration_secs"]) if r["duration_secs"] is not None else "-"
        status = r["status"]
        style = {"PASS": "green", "FAIL": "red", "TIMEOUT": "yellow"}.get(status, "dim")
        row = []
        if show_round:
            row.append(f"#{r['round']}")
        row += [r["group"], r["name"], f"[{style}]{status}[/{style}]", dur]
        t.add_row(*row)

    console.print(t)
    total = fmt_duration(result["total_secs"])
    console.print(
        f"  Total: {total}  —  "
        f"[green]{result['passed']} passed[/green], "
        f"[red]{result['failed']} failed[/red], "
        f"[dim]{result['skipped']} skipped[/dim]"
    )
    console.print(f"  Full output in {result['log_file']}")


# ── playlist ───────────────────────────────────────────────────────────────────


def cmd_playlist_list(args: argparse.Namespace) -> None:
    playlists = _data_list_playlists()
    if not playlists:
        console.print(
            "[dim]No playlists found. Use 'trun playlist create <name>' to create one.[/dim]"
        )
        return
    t = Table("Name", "Path")
    for p in playlists:
        t.add_row(p["name"], p["path"])
    console.print(t)


def cmd_playlist_show(args: argparse.Namespace) -> None:
    result = _data_get_playlist(args.name)
    if "error" in result:
        _fail(result["error"])
    entries = result["entries"]
    if not entries:
        console.print(f"[dim]Playlist '{args.name}' is empty.[/dim]")
        return
    t = Table("Group", "Subdir", "Executor", "Timeout", "Test", "Test Cases")
    for e in entries:
        to = f"{e['timeout']}s" if e["timeout"] is not None else "[dim]default[/dim]"
        tc = ", ".join(e["test_cases"]) if e.get("test_cases") else "[dim]all[/dim]"
        t.add_row(e["group"], e["subdir"], e["executor"], to, e["name"], tc)
    console.print(t)


def cmd_playlist_create(args: argparse.Namespace) -> None:
    result = _data_create_playlist(args.name)
    if "error" in result:
        _fail(result["error"])
    _ok(result["message"])


def cmd_playlist_add(args: argparse.Namespace) -> None:
    tc = args.test_cases or []
    tests_dicts = [{"name": t, **({"test_cases": tc} if tc else {})} for t in args.tests]
    result = _data_add_tests(
        name=args.name,
        group=args.group,
        build_dir=args.build,
        subdir=args.type,
        tests=tests_dicts,
        executor=args.executor,
        timeout_fast=args.timeout_fast,
        timeout_long=args.timeout_long,
    )
    if "error" in result:
        _fail(result["error"])
    _ok(result["message"])


def cmd_playlist_add_from_dir(args: argparse.Namespace) -> None:
    result = _data_create_playlist_from_dir(
        name=args.name,
        build_dir=args.build,
        subdir=args.subdir,
        group=args.group,
        executor=args.executor,
        timeout_fast=args.timeout_fast,
        timeout_long=args.timeout_long,
    )
    if "error" in result:
        _fail(result["error"])
    _ok(f"{result['message']} (discovered {result.get('discovered', 0)})")


def cmd_playlist_remove_tests(args: argparse.Namespace) -> None:
    result = _data_remove_tests(args.name, args.group, args.tests)
    if "error" in result:
        _fail(result["error"])
    _ok(result["message"])


def cmd_playlist_delete(args: argparse.Namespace) -> None:
    result = _data_delete_playlist(args.name)
    if "error" in result:
        _fail(result["error"])
    _ok(result["message"])


def cmd_playlist_migrate(args: argparse.Namespace) -> None:
    name = getattr(args, "name", None)
    if name:
        result = _data_migrate_playlist(name)
        if "error" in result:
            _fail(result["error"])
        _ok(result["message"])
    else:
        result = _data_migrate_all_playlists()
        any_error = False
        for m in result["migrations"]:
            if "error" in m:
                console.print(f"[red]{m['name']}:[/red] {m['error']}")
                any_error = True
            else:
                _ok(f"{m['name']}: {m['message']}")
        if not result["migrations"]:
            console.print("[dim]No .ini playlists found to migrate.[/dim]")
        elif any_error:
            raise SystemExit(1)


# ── executors ─────────────────────────────────────────────────────────────────


def cmd_config(args: argparse.Namespace) -> None:
    set_config(args.key, args.value)
    _ok(f"config: {args.key} = {args.value}")


def cmd_executors(args: argparse.Namespace) -> None:
    execs = list_executors()
    t = Table("Name", "Description", "fast_running timeout", "long_running timeout")
    for e in execs:
        t.add_row(
            e["name"],
            e["description"],
            f"{e['timeouts'].get('fast_running', '—')}s",
            f"{e['timeouts'].get('long_running', '—')}s",
        )
    console.print(t)


# ── mcp / patch-claude ────────────────────────────────────────────────────────


def cmd_mcp(args: argparse.Namespace) -> None:
    if args.print_config:
        exe = str(Path(sys.argv[0]).resolve())
        cfg = {"mcpServers": {"trun": {"type": "stdio", "command": exe, "args": ["mcp"]}}}
        console.print(json.dumps(cfg, indent=2))
        return
    from .server import main as server_main

    asyncio.run(server_main())


def cmd_patch_claude(args: argparse.Namespace) -> None:
    claude_json = Path.home() / ".claude.json"
    exe = str(Path(sys.argv[0]).resolve())
    data: dict = {}
    if claude_json.exists():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _fail("~/.claude.json contains invalid JSON.")
    servers: dict = data.setdefault("mcpServers", {})
    if args.remove:
        if "trun" in servers:
            del servers["trun"]
            claude_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
            console.print("[green]Removed[/green] trun from ~/.claude.json")
        else:
            console.print("[dim]trun not registered — nothing to remove.[/dim]")
        return
    servers["trun"] = {"type": "stdio", "command": exe, "args": ["mcp"], "env": {}}
    claude_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    console.print(f"[green]Registered[/green] trun → [cyan]{exe}[/cyan]")
    console.print("[dim]Restart Claude Code to activate.[/dim]")


# ── parser ────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trun",
        description="Test runner with GDB, valgrind, pytest support and MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # run
    p = sub.add_parser("run", help="Run tests.")
    p.add_argument(
        "--playlist",
        metavar="NAME_OR_PATH",
        help="Named playlist or path to .yaml file. Omit to run the built-in suite.",
    )
    p.add_argument("--build", metavar="DIR", help="Build directory (built-in suite only).")
    p.add_argument(
        "--repeat", type=int, default=1, metavar="N", help="Repeat suite N times (default: 1)."
    )
    p.add_argument("--shuffle", action="store_true", help="Randomize test order each round.")
    p.add_argument(
        "--executor",
        choices=["gdb", "direct", "valgrind", "pytest"],
        help="Override executor for all tests.",
    )
    p.add_argument(
        "--stop-on-first-failure",
        action="store_true",
        help="Stop after the first FAIL/CRASH/TIMEOUT.",
    )
    p.add_argument("--only", nargs="+", metavar="TEST", help="Run only these test names.")
    p.add_argument(
        "--append",
        action="store_true",
        help="Append to the existing log instead of clearing it.",
    )
    p.set_defaults(func=cmd_run)

    # single
    p = sub.add_parser("single", help="Run a single test binary without a playlist.")
    p.add_argument("name", help="Test binary name.")
    p.add_argument("--build", required=True, metavar="DIR", help="Build directory.")
    p.add_argument(
        "--subdir",
        default="fast_running",
        choices=["fast_running", "long_running"],
        help="Test subdirectory (default: fast_running).",
    )
    p.add_argument(
        "--executor",
        default="gdb",
        choices=["gdb", "direct", "valgrind", "pytest"],
        help="Executor (default: gdb).",
    )
    p.add_argument(
        "--test-cases",
        type=lambda s: s.split(","),
        default=None,
        metavar="FUNC1[,FUNC2,...]",
        help="Qt test function names to run, comma-separated.",
    )
    p.set_defaults(func=cmd_single)

    # playlist
    pl = sub.add_parser("playlist", help="Manage test playlists.")
    pl_sub = pl.add_subparsers(
        dest="playlist_command",
        metavar="<list|show|create|add|add-from-dir|remove-tests|delete|migrate>",
    )
    pl_sub.required = True

    p = pl_sub.add_parser("list", help="List saved playlists.")
    p.set_defaults(func=cmd_playlist_list)

    p = pl_sub.add_parser("show", help="Show playlist contents.")
    p.add_argument("name", help="Playlist name.")
    p.set_defaults(func=cmd_playlist_show)

    p = pl_sub.add_parser("create", help="Create an empty playlist.")
    p.add_argument("name", help="Playlist name.")
    p.set_defaults(func=cmd_playlist_create)

    p = pl_sub.add_parser("add", help="Add tests to a playlist group.")
    p.add_argument("name", help="Playlist name.")
    p.add_argument("--group", required=True, help="Section/group name.")
    p.add_argument("--build", required=True, metavar="DIR", help="Build directory for this group.")
    p.add_argument(
        "--type",
        required=True,
        choices=["fast_running", "long_running"],
        dest="type",
        help="Test type / subdir.",
    )
    p.add_argument(
        "--executor",
        default="gdb",
        choices=["gdb", "direct", "valgrind", "pytest"],
        help="Executor for this group (default: gdb).",
    )
    p.add_argument(
        "--timeout-fast",
        type=int,
        default=None,
        metavar="SECS",
        help="Override fast_running timeout (seconds).",
    )
    p.add_argument(
        "--timeout-long",
        type=int,
        default=None,
        metavar="SECS",
        help="Override long_running timeout (seconds).",
    )
    p.add_argument(
        "--test-cases",
        type=lambda s: s.split(","),
        default=None,
        metavar="FUNC1[,FUNC2,...]",
        help="Qt test function names to run, comma-separated (applied to all tests in this call).",
    )
    p.add_argument("tests", nargs="+", help="Test names to add.")
    p.set_defaults(func=cmd_playlist_add)

    p = pl_sub.add_parser(
        "add-from-dir",
        help="Discover tests from a CTestTestfile.cmake subdir and add them.",
    )
    p.add_argument("name", help="Playlist name (created if missing).")
    p.add_argument("--group", required=True, help="Group name.")
    p.add_argument("--build", required=True, metavar="DIR", help="Build directory.")
    p.add_argument(
        "--subdir",
        required=True,
        choices=["fast_running", "long_running"],
        help="Test subdirectory to scan.",
    )
    p.add_argument(
        "--executor",
        default="gdb",
        choices=["gdb", "direct", "valgrind", "pytest"],
        help="Executor for this group (default: gdb).",
    )
    p.add_argument("--timeout-fast", type=int, default=None, metavar="SECS")
    p.add_argument("--timeout-long", type=int, default=None, metavar="SECS")
    p.set_defaults(func=cmd_playlist_add_from_dir)

    p = pl_sub.add_parser("remove-tests", help="Remove tests from a playlist group.")
    p.add_argument("name", help="Playlist name.")
    p.add_argument("--group", required=True, help="Section/group name.")
    p.add_argument("tests", nargs="+", help="Test names to remove.")
    p.set_defaults(func=cmd_playlist_remove_tests)

    p = pl_sub.add_parser("delete", help="Delete a playlist.")
    p.add_argument("name", help="Playlist name.")
    p.set_defaults(func=cmd_playlist_delete)

    p = pl_sub.add_parser("migrate", help="Migrate .ini playlist(s) to YAML.")
    p.add_argument(
        "name",
        nargs="?",
        help="Playlist name to migrate. Omit to migrate all .ini playlists.",
    )
    p.set_defaults(func=cmd_playlist_migrate)

    pl.set_defaults(func=lambda args: pl.print_help())

    # executors
    p = sub.add_parser("executors", help="List available execution modes.")
    p.set_defaults(func=cmd_executors)

    # config
    p = sub.add_parser("config", help="Set persistent config (e.g. default build_dir).")
    cfg_sub = p.add_subparsers(dest="config_command", metavar="<set>")
    cfg_sub.required = True
    pc = cfg_sub.add_parser("set", help="Set a config key (e.g. build_dir).")
    pc.add_argument("key", help="Config key, e.g. build_dir.")
    pc.add_argument("value", help="Config value.")
    pc.set_defaults(func=cmd_config)

    # mcp
    p = sub.add_parser("mcp", help="Start the MCP stdio server.")
    p.add_argument(
        "--print-config",
        action="store_true",
        help="Print Claude Code MCP config JSON and exit.",
    )
    p.set_defaults(func=cmd_mcp)

    # patch-claude
    p = sub.add_parser("patch-claude", help="Register/unregister trun in ~/.claude.json.")
    p.add_argument("--remove", action="store_true", help="Remove from ~/.claude.json.")
    p.set_defaults(func=cmd_patch_claude)

    return parser


def run() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    run()
