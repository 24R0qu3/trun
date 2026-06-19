import asyncio
import shutil
import subprocess

import pytest

from trun.executor import get_executor, list_executors
from trun.models import TestEntry
from trun.runner import (
    _MAX_OUTPUT_LINES,
    _data_build,
    _data_get_test_cases,
    _data_run_tests,
    _filter_gdb_noise,
    _has_crash_in_output,
    _truncate_output,
    fmt_duration,
    parse_ninja_progress,
)


def test_parse_ninja_progress():
    assert parse_ninja_progress("[2/10] Building foo.o") == (2, 10, "Building foo.o")
    assert parse_ninja_progress("[12/12] Linking bar") == (12, 12, "Linking bar")
    assert parse_ninja_progress("plain line") is None
    assert parse_ninja_progress("gcc -c foo.c") is None


async def test_data_build_progress_only_ninja_lines():
    calls = []

    async def on_progress(n, total, target):
        calls.append((n, total, target))

    code = "print('[1/2] Building a'); print('plain output'); print('[2/2] Linking b')"
    r = await _data_build(cwd=None, cmd=f'python3 -c "{code}"', on_progress=on_progress)
    assert r["status"] == "PASS"
    assert calls == [(1, 2, "Building a"), (2, 2, "Linking b")]
    assert "plain output" in r["output"]  # non-progress lines still in buffered output


async def test_data_build_fail_status_streamed():
    r = await _data_build(cwd=None, cmd='python3 -c "import sys; sys.exit(1)"')
    assert r["status"] == "FAIL"


def test_fmt_duration_seconds():
    assert fmt_duration(0) == "0s"
    assert fmt_duration(45) == "45s"
    assert fmt_duration(59) == "59s"


def test_fmt_duration_minutes():
    assert fmt_duration(60) == "1m00s"
    assert fmt_duration(90) == "1m30s"
    assert fmt_duration(3599) == "59m59s"


def test_fmt_duration_hours():
    assert fmt_duration(3600) == "1h00m00s"
    assert fmt_duration(3661) == "1h01m01s"
    assert fmt_duration(7322) == "2h02m02s"


def test_get_executor_gdb():
    exc = get_executor("gdb")
    assert exc.name == "gdb"
    cmd = exc.build_command("/tmp/my_binary")
    assert cmd[0] == "gdb"
    assert "/tmp/my_binary" in cmd
    assert "-batch" in cmd
    assert "--return-child-result" in cmd


def test_get_executor_direct():
    exc = get_executor("direct")
    assert exc.build_command("/tmp/my_binary") == ["/tmp/my_binary"]


def test_get_executor_valgrind():
    exc = get_executor("valgrind")
    cmd = exc.build_command("/tmp/my_binary")
    assert cmd[0] == "valgrind"
    assert "/tmp/my_binary" in cmd


def test_get_executor_pytest():
    exc = get_executor("pytest")
    cmd = exc.build_command("tests/unit")
    assert cmd[0] == "pytest"
    assert "tests/unit" in cmd


def test_get_executor_unknown():
    with pytest.raises(ValueError, match="Unknown executor"):
        get_executor("unknown_executor")


def test_list_executors():
    execs = list_executors()
    names = [e["name"] for e in execs]
    assert set(names) >= {"gdb", "direct", "valgrind", "pytest"}
    for e in execs:
        assert "description" in e
        assert "timeouts" in e


def test_executor_timeouts():
    gdb = get_executor("gdb")
    assert gdb.default_timeout("fast_running") == 60
    assert gdb.default_timeout("long_running") == 180

    valgrind = get_executor("valgrind")
    assert valgrind.default_timeout("fast_running") == 120
    assert valgrind.default_timeout("long_running") == 360


def test_executor_unknown_subdir_uses_default():
    gdb = get_executor("gdb")
    assert gdb.default_timeout("custom_subdir") == 180


def test_test_entry_defaults():
    e = TestEntry(name="my_test", subdir="fast_running", build_dir="/tmp", group="smoke")
    assert e.executor == "gdb"
    assert e.timeout is None


def test_test_entry_timeout_override():
    e = TestEntry(
        name="my_test", subdir="fast_running", build_dir="/tmp", group="smoke", timeout=30
    )
    assert e.timeout == 30


async def test_run_tests_skip_missing_binary(tmp_path):
    entries = [
        TestEntry(
            name="nonexistent_test",
            subdir="fast_running",
            build_dir="/nonexistent/build",
            group="test_group",
        )
    ]
    log = tmp_path / "test.log"
    result = await _data_run_tests(entries, log_file=log)
    assert result["skipped"] == 0
    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "FAIL"


async def test_run_tests_multiple_entries_all_skip(tmp_path):
    entries = [
        TestEntry(name=f"test_{i}", subdir="fast_running", build_dir="/no/such/dir", group="g")
        for i in range(3)
    ]
    log = tmp_path / "test.log"
    result = await _data_run_tests(entries, log_file=log)
    assert result["failed"] == 3
    assert result["skipped"] == 0
    assert len(result["results"]) == 3


async def test_run_tests_repeat(tmp_path):
    entries = [TestEntry(name="test_x", subdir="fast_running", build_dir="/no/such/dir", group="g")]
    log = tmp_path / "test.log"
    result = await _data_run_tests(entries, repeat=3, log_file=log)
    assert len(result["results"]) == 3
    assert all(r["round"] in (1, 2, 3) for r in result["results"])


# ── PASS / FAIL / TIMEOUT / INTR ──────────────────────────────────────────────
# Use the pytest executor so we can point at a real Python script as the binary.


def _make_pytest_file(tmp_path, body: str) -> str:
    """Write a pytest test file and return its path as a string."""
    script = tmp_path / "test_script.py"
    script.write_text(body)
    return str(script)


def _pytest_entry(binary: str, group: str = "g", timeout: int | None = None) -> TestEntry:
    return TestEntry(
        name=binary,
        subdir="fast_running",
        build_dir=None,
        group=group,
        executor="pytest",
        timeout=timeout,
    )


async def test_run_tests_pass(tmp_path):
    binary = _make_pytest_file(tmp_path, "def test_it(): pass\n")
    log = tmp_path / "test.log"
    result = await _data_run_tests([_pytest_entry(binary)], log_file=log)
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["results"][0]["status"] == "PASS"


async def test_run_tests_fail(tmp_path):
    binary = _make_pytest_file(tmp_path, "def test_it(): assert False\n")
    log = tmp_path / "test.log"
    result = await _data_run_tests([_pytest_entry(binary)], log_file=log)
    assert result["failed"] == 1
    assert result["passed"] == 0
    assert result["skipped"] == 0
    assert result["results"][0]["status"] == "FAIL"


async def test_run_tests_timeout(tmp_path):
    binary = _make_pytest_file(tmp_path, "import time\ndef test_it(): time.sleep(60)\n")
    log = tmp_path / "test.log"
    result = await _data_run_tests([_pytest_entry(binary, timeout=1)], log_file=log)
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "TIMEOUT"


async def test_run_tests_intr(tmp_path):
    binary = _make_pytest_file(tmp_path, "import time\ndef test_it(): time.sleep(60)\n")
    log = tmp_path / "test.log"
    result = await _cancel_after(0.1, _data_run_tests([_pytest_entry(binary)], log_file=log))
    assert result is None
    assert "INTR" in log.read_text()


# ── direct / gdb shared helpers ───────────────────────────────────────────────


async def _cancel_after(delay: float, coro):
    """Run coro, cancel it after delay seconds; return None if cancelled."""
    task = asyncio.create_task(coro)
    await asyncio.sleep(delay)
    task.cancel()
    try:
        return await task
    except asyncio.CancelledError:
        return None


def _make_binary_entry(
    tmp_path,
    code: str,
    executor: str,
    name: str = "my_test",
    timeout: int | None = None,
) -> TestEntry:
    """Create the build-dir tree non-pytest executors expect and return a TestEntry."""
    build_dir = tmp_path / "build"
    binary_path = build_dir / "test" / "fast_running" / name / name
    binary_path.parent.mkdir(parents=True)
    binary_path.write_text(f"#!/usr/bin/env python3\n{code}")
    binary_path.chmod(0o755)
    return TestEntry(
        name=name,
        subdir="fast_running",
        build_dir=str(build_dir),
        group="g",
        executor=executor,
        timeout=timeout,
    )


gdb_and_gcc = pytest.mark.skipif(
    shutil.which("gdb") is None or shutil.which("gcc") is None,
    reason="gdb and gcc required",
)


# ── direct executor ───────────────────────────────────────────────────────────


async def test_direct_pass(tmp_path):
    entry = _make_binary_entry(tmp_path, "import sys; sys.exit(0)\n", "direct")
    log = tmp_path / "test.log"
    result = await _data_run_tests([entry], log_file=log)
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["status"] == "PASS"


async def test_direct_fail(tmp_path):
    entry = _make_binary_entry(tmp_path, "import sys; sys.exit(1)\n", "direct")
    log = tmp_path / "test.log"
    result = await _data_run_tests([entry], log_file=log)
    assert result["failed"] == 1
    assert result["passed"] == 0
    assert result["results"][0]["status"] == "FAIL"


async def test_direct_timeout(tmp_path):
    entry = _make_binary_entry(tmp_path, "import time; time.sleep(60)\n", "direct", timeout=1)
    log = tmp_path / "test.log"
    result = await _data_run_tests([entry], log_file=log)
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "TIMEOUT"


async def test_direct_intr(tmp_path):
    entry = _make_binary_entry(tmp_path, "import time; time.sleep(60)\n", "direct")
    log = tmp_path / "test.log"
    result = await _cancel_after(0.1, _data_run_tests([entry], log_file=log))
    assert result is None
    assert "INTR" in log.read_text()


# ── gdb executor ──────────────────────────────────────────────────────────────
# gdb is designed for ELF binaries. Script inferiors don't get properly traced,
# so we compile small C programs inline. gdb exits non-zero only when the
# inferior is killed by a signal (abort/crash), not on a clean non-zero exit.


def _compile_gdb_entry(
    tmp_path, c_code: str, name: str = "my_test", timeout: int | None = None
) -> TestEntry:
    build_dir = tmp_path / "build"
    binary_dir = build_dir / "test" / "fast_running" / name
    binary_dir.mkdir(parents=True)
    binary_path = binary_dir / name
    src = tmp_path / f"{name}.c"
    src.write_text(c_code)
    subprocess.check_call(["gcc", "-o", str(binary_path), str(src)], stderr=subprocess.DEVNULL)
    return TestEntry(
        name=name,
        subdir="fast_running",
        build_dir=str(build_dir),
        group="g",
        executor="gdb",
        timeout=timeout,
    )


@gdb_and_gcc
async def test_gdb_pass(tmp_path):
    entry = _compile_gdb_entry(tmp_path, "int main(void) { return 0; }\n")
    log = tmp_path / "test.log"
    result = await _data_run_tests([entry], log_file=log)
    assert result["passed"] == 1
    assert result["results"][0]["status"] == "PASS"


@gdb_and_gcc
async def test_gdb_fail(tmp_path):
    entry = _compile_gdb_entry(tmp_path, "#include <stdlib.h>\nint main(void) { abort(); }\n")
    log = tmp_path / "test.log"
    result = await _data_run_tests([entry], log_file=log)
    assert result["failed"] == 1
    # --return-child-result makes GDB exit non-zero; output-based detection catches it too
    assert result["results"][0]["status"] in ("FAIL", "CRASH")


@gdb_and_gcc
async def test_gdb_timeout(tmp_path):
    entry = _compile_gdb_entry(
        tmp_path,
        "#include <unistd.h>\nint main(void) { while(1) sleep(1); }\n",
        timeout=1,
    )
    log = tmp_path / "test.log"
    result = await _data_run_tests([entry], log_file=log)
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "TIMEOUT"


@gdb_and_gcc
async def test_gdb_intr(tmp_path):
    entry = _compile_gdb_entry(
        tmp_path, "#include <unistd.h>\nint main(void) { while(1) sleep(1); }\n"
    )
    log = tmp_path / "test.log"
    result = await _cancel_after(0.5, _data_run_tests([entry], log_file=log))
    assert result is None
    assert "INTR" in log.read_text()


# ── unit tests for new helper functions ───────────────────────────────────────


def test_has_crash_sigabrt():
    assert _has_crash_in_output("received signal SIGABRT, Aborted.") is True


def test_has_crash_sigsegv():
    assert _has_crash_in_output("received signal SIGSEGV") is True


def test_has_crash_negative():
    assert _has_crash_in_output("all tests passed") is False


def test_has_crash_empty():
    assert _has_crash_in_output("") is False


def test_filter_gdb_noise_removes_new_thread():
    text = "[New Thread 0x7ffff7e18000 (LWP 1234)]\nOther output"
    assert "[New Thread" not in _filter_gdb_noise(text)
    assert "Other output" in _filter_gdb_noise(text)


def test_filter_gdb_noise_removes_detaching():
    text = "[Detaching after vfork from child process 1234]\nOther output"
    assert "[Detaching" not in _filter_gdb_noise(text)
    assert "Other output" in _filter_gdb_noise(text)


def test_filter_gdb_noise_keeps_signal_line():
    line = 'Thread 13 "Server (pooled)" received signal SIGABRT'
    assert _filter_gdb_noise(line) == line


def test_filter_gdb_noise_keeps_regular_lines():
    text = "Regular output\nAnother line"
    assert _filter_gdb_noise(text) == text


def test_truncate_output_under_limit():
    text = "line1\nline2\nline3"
    assert _truncate_output(text, max_lines=10) == text


def test_truncate_output_at_limit():
    text = "line1\nline2\nline3"
    assert _truncate_output(text, max_lines=3) == text


def test_truncate_output_keeps_head_and_tail():
    # backtrace lives at the END of gdb output — truncation must keep the tail
    text = "\n".join(f"line{i}" for i in range(20))  # line0 .. line19
    result = _truncate_output(text, max_lines=10, tail_lines=4)
    assert "line0" in result  # head kept
    assert "line19" in result  # tail kept (this is where bt lands)
    assert "line16" in result
    assert "line10" not in result  # middle dropped
    assert "[10 lines truncated]" in result


def test_truncate_output_default_limit():
    text = "\n".join(f"line{i}" for i in range(_MAX_OUTPUT_LINES + 10))
    assert "truncated" in _truncate_output(text)


# ── get_test_cases ────────────────────────────────────────────────────────────


def _make_functions_binary(tmp_path, functions: list[str], name: str = "my_test") -> str:
    """Create a binary that prints test function names when called with -functions."""
    build_dir = tmp_path / "build"
    binary_path = build_dir / "test" / "fast_running" / name / name
    binary_path.parent.mkdir(parents=True)
    lines = ["#!/usr/bin/env python3", "import sys"]
    fn_list = "\n".join(f'    print("{f}")' for f in functions)
    binary_path.write_text(
        f"{lines[0]}\n{lines[1]}\nif sys.argv[1:] == ['-functions']:\n{fn_list}\n"
    )
    binary_path.chmod(0o755)
    return str(build_dir)


async def test_get_test_cases_returns_functions(tmp_path):
    build_dir = _make_functions_binary(tmp_path, ["tst_foo()", "tst_bar()"])
    result = await _data_get_test_cases("my_test", build_dir)
    assert result["test_cases"] == ["tst_foo()", "tst_bar()"]
    assert result["name"] == "my_test"


async def test_get_test_cases_missing_binary(tmp_path):
    result = await _data_get_test_cases("no_such_test", str(tmp_path))
    assert "error" in result
    assert "not found" in result["error"]


async def test_get_test_cases_empty_output(tmp_path):
    build_dir = _make_functions_binary(tmp_path, [])
    result = await _data_get_test_cases("my_test", build_dir)
    assert result["test_cases"] == []


# ── run_single_test (via _data_run_tests with inline TestEntry) ───────────────


async def test_run_single_pass(tmp_path):
    binary = _make_pytest_file(tmp_path, "def test_it(): pass\n")
    log = tmp_path / "test.log"
    entry = TestEntry(
        name=binary, subdir="fast_running", build_dir=None, group="single", executor="pytest"
    )
    result = await _data_run_tests([entry], log_file=log)
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["group"] == "single"


async def test_run_single_fail(tmp_path):
    binary = _make_pytest_file(tmp_path, "def test_it(): assert False\n")
    log = tmp_path / "test.log"
    entry = TestEntry(
        name=binary, subdir="fast_running", build_dir=None, group="single", executor="pytest"
    )
    result = await _data_run_tests([entry], log_file=log)
    assert result["failed"] == 1
    assert result["passed"] == 0


# ── on_result progress callback ───────────────────────────────────────────────


async def test_on_result_called_for_each_test(tmp_path):
    calls = []

    async def cb(result, done, total):
        calls.append((result.name, done, total))

    entries = [
        TestEntry(name=f"test_{i}", subdir="fast_running", build_dir="/no/such/dir", group="g")
        for i in range(3)
    ]
    log = tmp_path / "test.log"
    await _data_run_tests(entries, log_file=log, on_result=cb)
    assert len(calls) == 3


async def test_on_result_done_and_total_counts(tmp_path):
    calls = []

    async def cb(result, done, total):
        calls.append((done, total))

    entries = [
        TestEntry(name=f"test_{i}", subdir="fast_running", build_dir="/no/such/dir", group="g")
        for i in range(3)
    ]
    log = tmp_path / "test.log"
    await _data_run_tests(entries, log_file=log, on_result=cb)
    assert calls == [(1, 3), (2, 3), (3, 3)]


async def test_on_result_counts_with_repeat(tmp_path):
    calls = []

    async def cb(result, done, total):
        calls.append((done, total))

    entries = [
        TestEntry(name=f"test_{i}", subdir="fast_running", build_dir="/no/such/dir", group="g")
        for i in range(2)
    ]
    log = tmp_path / "test.log"
    await _data_run_tests(entries, repeat=2, log_file=log, on_result=cb)
    assert len(calls) == 4
    assert calls[-1] == (4, 4)


async def test_on_result_not_required(tmp_path):
    entries = [TestEntry(name="t", subdir="fast_running", build_dir="/no/such/dir", group="g")]
    log = tmp_path / "test.log"
    result = await _data_run_tests(entries, log_file=log)
    assert result["failed"] == 1


# ── run_and_analyze (logic layer) ─────────────────────────────────────────────


async def test_run_and_analyze_shape_pass(tmp_path):
    from trun.log_analysis import aggregate_failures, parse_log

    binary = _make_pytest_file(tmp_path, "def test_it(): pass\n")
    log = tmp_path / "test.log"
    entry = TestEntry(
        name=binary, subdir="fast_running", build_dir=None, group="g", executor="pytest"
    )
    run_result = await _data_run_tests([entry], log_file=log)
    analysis = parse_log(log.read_text())
    failures = [t for t in analysis["tests"] if t["status"] != "PASS"]
    aggregated = aggregate_failures(failures, analysis["summary"].get("total_rounds", 1))
    assert run_result["passed"] == 1
    assert aggregated == []


# ── _data_build ───────────────────────────────────────────────────────────────


async def test_data_build_pass():
    result = await _data_build(cwd=None, cmd="true")
    assert result["status"] == "PASS"
    assert result["duration_secs"] >= 0


async def test_data_build_fail():
    result = await _data_build(cwd=None, cmd="false")
    assert result["status"] == "FAIL"


async def test_data_build_timeout():
    result = await _data_build(cwd=None, cmd="sleep 60", timeout=1)
    assert result["status"] == "FAIL"
    assert "timed out" in result["output"].lower()


async def test_data_build_invalid_cmd():
    result = await _data_build(cwd=None, cmd="__no_such_binary_xyz__")
    assert result["status"] == "FAIL"
    assert "output" in result


async def test_data_build_captures_output(tmp_path):
    result = await _data_build(cwd=str(tmp_path), cmd="echo hello")
    assert result["status"] == "PASS"
    assert "hello" in result["output"]


async def test_data_build_uses_cwd(tmp_path):
    result = await _data_build(cwd=str(tmp_path), cmd="pwd")
    assert result["status"] == "PASS"
    assert str(tmp_path) in result["output"]


async def test_run_and_analyze_shape_fail(tmp_path):
    from trun.log_analysis import aggregate_failures, parse_log

    binary = _make_pytest_file(tmp_path, "def test_it(): assert False\n")
    log = tmp_path / "test.log"
    entry = TestEntry(
        name=binary, subdir="fast_running", build_dir=None, group="g", executor="pytest"
    )
    run_result = await _data_run_tests([entry], log_file=log)
    analysis = parse_log(log.read_text())
    failures = [t for t in analysis["tests"] if t["status"] != "PASS"]
    aggregated = aggregate_failures(failures, analysis["summary"].get("total_rounds", 1))
    assert run_result["failed"] == 1
    assert len(aggregated) == 1
    assert aggregated[0]["name"] == binary
