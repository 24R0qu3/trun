from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path

from .config import LOG_FILE
from .executor import get_executor
from .log_analysis import get_error_hint
from .models import RunResult, TestEntry, TestResult

_CRASH_SIGNAL_RE = re.compile(r"received signal (SIG\w+)")
_MAX_OUTPUT_LINES = 300
_GDB_NOISE_RE = re.compile(
    r"^\[(?:New Thread 0x|Detaching after (?:fork|vfork) from (?:child|parent) process )"
)


def fmt_duration(secs: int) -> str:
    if secs >= 3600:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m{secs % 60:02d}s"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs}s"


def _has_crash_in_output(output: str) -> bool:
    return bool(_CRASH_SIGNAL_RE.search(output))


def _filter_gdb_noise(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _GDB_NOISE_RE.match(line))


def _truncate_output(text: str, max_lines: int = _MAX_OUTPUT_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    dropped = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n... [{dropped} lines truncated] ..."


async def _run_single(
    entry: TestEntry,
    round_num: int,
    executor_override: str | None,
    log_file: Path,
) -> TestResult:
    executor_name = executor_override or entry.executor
    executor = get_executor(executor_name)

    if executor_name == "pytest":
        binary = str(Path(entry.build_dir) / entry.name) if entry.build_dir else entry.name
    else:
        binary = str(Path(entry.build_dir) / "test" / entry.subdir / entry.name / entry.name)

    timeout = entry.timeout if entry.timeout is not None else executor.default_timeout(entry.subdir)

    if executor_name != "pytest" and not Path(binary).is_file():
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a") as f:
            f.write(
                f"=== [round {round_num}] {entry.name}: FAIL (binary not found: {binary}) ===\n\n"
            )
        return TestResult(
            name=entry.name,
            group=entry.group,
            status="FAIL",
            duration_secs=None,
            round_num=round_num,
            error_hint=None,
        )

    cmd = executor.build_command(binary, entry.test_cases or None)
    t_start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            exit_code = proc.returncode
            timed_out = False
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            stdout = b""
            exit_code = 124
            timed_out = True
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            elapsed = int(time.monotonic() - t_start)
            tc_info = f" [{', '.join(entry.test_cases)}]" if entry.test_cases else ""
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a") as f:
                f.write(
                    f"=== [round {round_num}] {entry.name}{tc_info}: INTR "
                    f"({fmt_duration(elapsed)}) ===\n\n"
                )
            raise
    except Exception as e:
        elapsed = int(time.monotonic() - t_start)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a") as f:
            f.write(
                f"=== [round {round_num}] {entry.name}: FAIL "
                f"(error: {e}, {fmt_duration(elapsed)}) ===\n\n"
            )
        return TestResult(
            name=entry.name,
            group=entry.group,
            status="FAIL",
            duration_secs=elapsed,
            round_num=round_num,
            error_hint=None,
        )

    elapsed = int(time.monotonic() - t_start)
    output = stdout.decode(errors="replace") if stdout else ""

    tc_info = f" [{', '.join(entry.test_cases)}]" if entry.test_cases else ""
    if timed_out:
        status = "TIMEOUT"
        log_line = (
            f"=== [round {round_num}] {entry.name}{tc_info}: TIMEOUT "
            f"({fmt_duration(elapsed)}, limit {fmt_duration(timeout)}) ===\n"
        )
    elif exit_code == 0 and _has_crash_in_output(output):
        status = "CRASH"
        log_line = (
            f"=== [round {round_num}] {entry.name}{tc_info}: CRASH "
            f"(signal in output, exit 0, {fmt_duration(elapsed)}) ===\n"
        )
    elif exit_code == 0:
        status = "PASS"
        log_line = (
            f"=== [round {round_num}] {entry.name}{tc_info}: PASS ({fmt_duration(elapsed)}) ===\n"
        )
    else:
        status = "FAIL"
        log_line = (
            f"=== [round {round_num}] {entry.name}{tc_info}: FAIL "
            f"(exit {exit_code}, {fmt_duration(elapsed)}) ===\n"
        )

    hint = get_error_hint(output.splitlines(), status) if output else None

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a") as f:
        f.write(log_line)
        if output:
            filtered_output = _filter_gdb_noise(output)
            filtered_output = _truncate_output(filtered_output)
            f.write(filtered_output)
        f.write("\n")

    return TestResult(
        name=entry.name,
        group=entry.group,
        status=status,
        duration_secs=elapsed,
        round_num=round_num,
        error_hint=hint,
    )


async def _data_run_tests(
    entries: list[TestEntry],
    repeat: int = 1,
    shuffle: bool = False,
    executor_override: str | None = None,
    log_file: Path | None = None,
    on_result: Callable[[TestResult], None] | None = None,
) -> dict:
    if log_file is None:
        log_file = LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("")

    run_result = RunResult()
    total_start = time.monotonic()

    for round_num in range(1, repeat + 1):
        round_entries = list(entries)
        if shuffle:
            import random

            random.shuffle(round_entries)

        prev_name: str | None = None
        for entry in round_entries:
            try:
                result = await _run_single(entry, round_num, executor_override, log_file)
            except asyncio.CancelledError:
                result = TestResult(
                    name=entry.name,
                    group=entry.group,
                    status="INTR",
                    duration_secs=None,
                    round_num=round_num,
                )
                run_result.results.append(result)
                run_result.skipped += 1
                if on_result:
                    on_result(result)
                raise
            result.predecessor = prev_name
            prev_name = entry.name
            run_result.results.append(result)
            if result.status == "PASS":
                run_result.passed += 1
            elif result.status in ("SKIP", "INTR"):
                run_result.skipped += 1
            else:  # FAIL, CRASH, TIMEOUT
                run_result.failed += 1
            if on_result:
                on_result(result)

    run_result.total_secs = int(time.monotonic() - total_start)

    return {
        "results": [
            {
                "name": r.name,
                "group": r.group,
                "status": r.status,
                "duration_secs": r.duration_secs,
                "round": r.round_num,
                "error_hint": r.error_hint,
                "predecessor": r.predecessor,
            }
            for r in run_result.results
        ],
        "total_secs": run_result.total_secs,
        "passed": run_result.passed,
        "failed": run_result.failed,
        "skipped": run_result.skipped,
        "log_file": str(log_file),
    }
