"""Tests for trun MCP UX improvements:
1. stop_on_first_failure in _data_run_tests
2. Crash frame in get_error_hint
3. Append mode (accumulated statistics across runs)
4. only_tests filtering in server run_tests handler
"""

from __future__ import annotations

import asyncio

import pytest

from trun.log_analysis import get_error_hint, parse_log
from trun.models import TestEntry
from trun.runner import _data_run_tests


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_pytest_file(tmp_path, body: str) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "test_script.py"
    script.write_text(body)
    return str(script)


def _pytest_entry(binary: str, name_override: str | None = None) -> TestEntry:
    return TestEntry(
        name=name_override or binary,
        subdir="fast_running",
        build_dir=None,
        group="g",
        executor="pytest",
    )


# ── Feature 1: stop_on_first_failure ─────────────────────────────────────────


async def test_stop_on_first_failure_stops_after_first_fail(tmp_path):
    pass_bin = _make_pytest_file(tmp_path / "p", "def test_it(): pass\n")
    fail_bin = _make_pytest_file(tmp_path / "f", "def test_it(): assert False\n")
    pass_bin2 = _make_pytest_file(tmp_path / "p2", "def test_it(): pass\n")

    log = tmp_path / "test.log"
    result = await _data_run_tests(
        [_pytest_entry(pass_bin), _pytest_entry(fail_bin), _pytest_entry(pass_bin2)],
        log_file=log,
        stop_on_first_failure=True,
    )
    assert result["failed"] >= 1
    assert len(result["results"]) < 3
    assert result.get("stopped_early") is True


async def test_stop_on_first_failure_false_runs_all(tmp_path):
    fail1 = _make_pytest_file(tmp_path / "f1", "def test_it(): assert False\n")
    fail2 = _make_pytest_file(tmp_path / "f2", "def test_it(): assert False\n")

    log = tmp_path / "test.log"
    result = await _data_run_tests(
        [_pytest_entry(fail1), _pytest_entry(fail2)],
        log_file=log,
        stop_on_first_failure=False,
    )
    assert result["failed"] == 2
    assert len(result["results"]) == 2
    assert result.get("stopped_early") is not True


async def test_stop_on_first_failure_all_pass_no_stop(tmp_path):
    pass1 = _make_pytest_file(tmp_path / "p1", "def test_it(): pass\n")
    pass2 = _make_pytest_file(tmp_path / "p2", "def test_it(): pass\n")

    log = tmp_path / "test.log"
    result = await _data_run_tests(
        [_pytest_entry(pass1), _pytest_entry(pass2)],
        log_file=log,
        stop_on_first_failure=True,
    )
    assert result["passed"] == 2
    assert result.get("stopped_early") is not True


async def test_stop_on_first_failure_stops_across_rounds(tmp_path):
    pass_bin = _make_pytest_file(tmp_path / "p", "def test_it(): pass\n")
    fail_bin = _make_pytest_file(tmp_path / "f", "def test_it(): assert False\n")

    log = tmp_path / "test.log"
    result = await _data_run_tests(
        [_pytest_entry(pass_bin), _pytest_entry(fail_bin)],
        repeat=10,
        log_file=log,
        stop_on_first_failure=True,
    )
    # Should stop well before 20 total results
    assert len(result["results"]) < 20
    assert result.get("stopped_early") is True


# ── Feature 2: Crash frame in get_error_hint ──────────────────────────────────


SIGSEGV_WITH_FRAMES = [
    'Thread 1 "my_process" received signal SIGSEGV, Segmentation fault.',
    "#0  __pthread_kill_implementation () at ./nptl/pthread_kill.c:44",
    "#13 0x0000555555 in MyClass::doWork (this=0x...) at /home/niels/src/my_module.cpp:323",
    "#14 0x0000666666 in main () at /home/niels/src/main.cpp:12",
]

SIGABRT_WITH_ASSERTION_AND_FRAME = [
    "Assertion `ptr != nullptr' failed.",
    'Thread 1 received signal SIGABRT, Aborted.',
    "#0  __pthread_kill () at ./nptl/pthread_kill.c:44",
    "#5 0x000055 in FooClass::bar (this=0x) at /home/niels/src/foo.cpp:99",
]

SIGNAL_NO_USER_FRAME = [
    'Thread 1 received signal SIGSEGV, Segmentation fault.',
    "#0  __pthread_kill () at /usr/lib/something.c:10",
]

QT_FAIL_LINES = [
    "FAIL   : MyTest::testFoo() 'x == 1' returned FALSE",
]


def test_get_error_hint_sigsegv_includes_user_frame():
    hint = get_error_hint(SIGSEGV_WITH_FRAMES, "CRASH")
    assert hint is not None
    assert "SIGSEGV" in hint
    assert "my_module.cpp:323" in hint


def test_get_error_hint_sigsegv_no_user_frame_no_frame_appended():
    hint = get_error_hint(SIGNAL_NO_USER_FRAME, "CRASH")
    assert hint == "SIGSEGV"


def test_get_error_hint_sigabrt_with_assertion_and_frame():
    hint = get_error_hint(SIGABRT_WITH_ASSERTION_AND_FRAME, "CRASH")
    assert hint is not None
    assert "SIGABRT" in hint
    assert "ptr != nullptr" in hint
    # frame appended to signal+assertion combo
    assert "foo.cpp:99" in hint


def test_get_error_hint_qt_fail_no_frame_appended():
    hint = get_error_hint(QT_FAIL_LINES, "FAIL")
    assert hint is not None
    assert "FAIL" in hint
    # Qt fail branch should not append frames
    assert "@" not in hint


def test_get_error_hint_pass_returns_none():
    hint = get_error_hint(SIGSEGV_WITH_FRAMES, "PASS")
    assert hint is None


def test_get_error_hint_frame_format_is_compact():
    hint = get_error_hint(SIGSEGV_WITH_FRAMES, "CRASH")
    # Should be "filename:line", not full path
    assert "/home/niels/src/" not in hint
    assert "my_module.cpp:323" in hint


# ── Feature 3: Append mode ────────────────────────────────────────────────────


async def test_append_false_clears_log(tmp_path):
    pass_bin = _make_pytest_file(tmp_path / "p", "def test_it(): pass\n")
    log = tmp_path / "test.log"

    await _data_run_tests([_pytest_entry(pass_bin)], log_file=log)
    first_content = log.read_text()

    await _data_run_tests([_pytest_entry(pass_bin)], log_file=log, append=False)
    second_content = log.read_text()

    # Log is cleared each time without append
    assert second_content == first_content


async def test_append_true_accumulates_log(tmp_path):
    pass_bin = _make_pytest_file(tmp_path / "p", "def test_it(): pass\n")
    fail_bin = _make_pytest_file(tmp_path / "f", "def test_it(): assert False\n")
    log = tmp_path / "test.log"

    await _data_run_tests([_pytest_entry(pass_bin)], log_file=log)
    await _data_run_tests([_pytest_entry(fail_bin)], log_file=log, append=True)

    content = log.read_text()
    # Both rounds should appear in the log
    assert "PASS" in content
    assert "FAIL" in content


async def test_append_continues_round_numbering(tmp_path):
    pass_bin = _make_pytest_file(tmp_path / "p", "def test_it(): pass\n")
    log = tmp_path / "test.log"

    await _data_run_tests([_pytest_entry(pass_bin)], repeat=3, log_file=log)
    await _data_run_tests([_pytest_entry(pass_bin)], repeat=2, log_file=log, append=True)

    content = log.read_text()
    parsed = parse_log(content)
    rounds = [t["round"] for t in parsed["tests"]]
    # Rounds should be 1, 2, 3, 4, 5 — no duplicates
    assert sorted(rounds) == [1, 2, 3, 4, 5]


async def test_append_analyze_shows_combined_failure_rate(tmp_path):
    fail_bin = _make_pytest_file(tmp_path / "f", "def test_it(): assert False\n")
    log = tmp_path / "test.log"

    await _data_run_tests([_pytest_entry(fail_bin)], repeat=3, log_file=log)
    await _data_run_tests([_pytest_entry(fail_bin)], repeat=2, log_file=log, append=True)

    content = log.read_text()
    parsed = parse_log(content)
    total_rounds = parsed["summary"]["total_rounds"]
    assert total_rounds == 5
