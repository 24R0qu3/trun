import pytest

from trun.executor import get_executor, list_executors
from trun.models import TestEntry
from trun.runner import _data_run_tests, fmt_duration


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
    assert result["skipped"] == 1
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["results"][0]["status"] == "SKIP"


async def test_run_tests_multiple_entries_all_skip(tmp_path):
    entries = [
        TestEntry(name=f"test_{i}", subdir="fast_running", build_dir="/no/such/dir", group="g")
        for i in range(3)
    ]
    log = tmp_path / "test.log"
    result = await _data_run_tests(entries, log_file=log)
    assert result["skipped"] == 3
    assert len(result["results"]) == 3


async def test_run_tests_repeat(tmp_path):
    entries = [
        TestEntry(name="test_x", subdir="fast_running", build_dir="/no/such/dir", group="g")
    ]
    log = tmp_path / "test.log"
    result = await _data_run_tests(entries, repeat=3, log_file=log)
    assert len(result["results"]) == 3
    assert all(r["round"] in (1, 2, 3) for r in result["results"])
