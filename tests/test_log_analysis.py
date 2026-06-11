from trun.log_analysis import filter_log_lines, get_error_hint, parse_log

SIMPLE_LOG = """\
=== [round 1] my_test: PASS (2s) ===
PASS   : my_test::case1()
Totals: 1 passed, 0 failed

=== [round 1] other_test: FAIL (exit 1, 3s) ===
FAIL   : other_test::case2() 'x == 1' returned FALSE
"""

CRASH_LOG = """\
=== [round 1] crash_test [diagServer_foo]: PASS (3s) ===
Thread 13 "worker" received signal SIGABRT, Aborted.
Assertion `__null != p_driver' failed.
#0  __pthread_kill at ./nptl/pthread_kill.c:44
#13 0x000055555 in MyClass::start (this=0x...) at /media/nielsruehr/projects/src/my.cpp:163
"""


class TestParseLog:
    def test_simple_log_summary(self):
        result = parse_log(SIMPLE_LOG)
        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1

    def test_finds_pass_test(self):
        result = parse_log(SIMPLE_LOG)
        tests = result["tests"]
        my_test = next((t for t in tests if t["name"] == "my_test"), None)
        assert my_test is not None
        assert my_test["status"] == "PASS"

    def test_finds_fail_test(self):
        result = parse_log(SIMPLE_LOG)
        tests = result["tests"]
        other_test = next((t for t in tests if t["name"] == "other_test"), None)
        assert other_test is not None
        assert other_test["status"] == "FAIL"

    def test_crash_log_detects_crash_signal(self):
        result = parse_log(CRASH_LOG)
        tests = result["tests"]
        crash_test = tests[0]
        # P9 retroactive fix: PASS sections with signal become CRASH
        assert crash_test["status"] == "CRASH"
        assert crash_test["signal"] == "SIGABRT"

    def test_crash_log_extracts_assertion(self):
        result = parse_log(CRASH_LOG)
        crash_test = result["tests"][0]
        assert crash_test["assertion"] == "__null != p_driver"

    def test_crash_log_includes_user_frame(self):
        result = parse_log(CRASH_LOG)
        crash_test = result["tests"][0]
        user_frames = crash_test["user_frames"]
        # Should contain my.cpp:163 frame
        assert any("my.cpp:163" in frame for frame in user_frames)

    def test_crash_log_excludes_system_frames(self):
        result = parse_log(CRASH_LOG)
        crash_test = result["tests"][0]
        user_frames = crash_test["user_frames"]
        # Should NOT contain pthread_kill.c frame
        assert not any("pthread_kill.c" in frame for frame in user_frames)


class TestGetErrorHint:
    def test_pass_status_returns_none(self):
        lines = ["some output"]
        hint = get_error_hint(lines, "PASS")
        assert hint is None

    def test_fail_with_qt_failure(self):
        lines = [
            "Some output",
            "FAIL   : test::case() assertion failed",
            "More output",
        ]
        hint = get_error_hint(lines, "FAIL")
        assert hint is not None
        assert "FAIL" in hint

    def test_crash_with_signal_and_assertion(self):
        lines = [
            "Thread 13 received signal SIGABRT",
            "Assertion `x != 0' failed.",
        ]
        hint = get_error_hint(lines, "CRASH")
        assert "SIGABRT" in hint
        assert "x != 0" in hint

    def test_crash_with_signal_only(self):
        lines = [
            "Thread 13 received signal SIGSEGV",
        ]
        hint = get_error_hint(lines, "CRASH")
        assert hint == "SIGSEGV"

    def test_fail_with_assertion_only(self):
        lines = [
            "Assertion `foo > 0' failed.",
        ]
        hint = get_error_hint(lines, "FAIL")
        assert "assertion failed" in hint
        assert "foo > 0" in hint

    def test_empty_lines_fail_status(self):
        hint = get_error_hint([], "FAIL")
        assert hint is None


class TestFilterLogLines:
    def test_filter_by_test_name(self):
        lines = CRASH_LOG.splitlines()
        filtered = filter_log_lines(lines, test_filter="crash_test", errors_only=False)
        # Should only contain crash_test section
        filtered_text = "\n".join(filtered)
        assert "crash_test" in filtered_text
        assert len(filtered) > 0

    def test_errors_only_excludes_pass(self):
        lines = SIMPLE_LOG.splitlines()
        filtered = filter_log_lines(lines, test_filter=None, errors_only=True)
        filtered_text = "\n".join(filtered)
        # Should include other_test (FAIL) but exclude my_test (PASS)
        assert "other_test" in filtered_text
        # my_test section header should not be in filtered output
        assert "=== [round 1] my_test: PASS" not in filtered_text

    def test_filter_and_errors_only_combined(self):
        # Create a log with CRASH_LOG modified to show actual CRASH status (not PASS)
        crash_log_with_status = """\
=== [round 1] crash_test [diagServer_foo]: CRASH (signal in output, exit 0, 3s) ===
Thread 13 "worker" received signal SIGABRT, Aborted.
Assertion `__null != p_driver' failed.
#0  __pthread_kill at ./nptl/pthread_kill.c:44
#13 0x000055555 in MyClass::start (this=0x...) at /media/nielsruehr/projects/src/my.cpp:163
"""
        combined_log = SIMPLE_LOG + "\n" + crash_log_with_status
        lines = combined_log.splitlines()
        filtered = filter_log_lines(lines, test_filter="crash_test", errors_only=True)
        filtered_text = "\n".join(filtered)
        # Should include crash_test but not my_test or other_test
        assert "crash_test" in filtered_text
        assert "my_test" not in filtered_text
        assert "other_test" not in filtered_text

    def test_no_filter_returns_all(self):
        lines = SIMPLE_LOG.splitlines()
        filtered = filter_log_lines(lines, test_filter=None, errors_only=False)
        # Should return all lines
        assert len(filtered) == len(lines)


MULTI_ROUND_LOG = """\
=== [round 1] flaky_test: FAIL (exit 1, 2s) ===
#0  crash_func at /src/foo.cpp:10
#1  main at /src/main.cpp:5

=== [round 2] flaky_test: PASS (2s) ===

=== [round 3] flaky_test: FAIL (exit 1, 2s) ===
#0  crash_func at /src/foo.cpp:10
#1  main at /src/main.cpp:5

=== [round 4] stable_test: PASS (1s) ===
=== [round 5] stable_test: PASS (1s) ===
"""

NO_USER_FRAMES_LOG = """\
=== [round 1] isolib_test: CRASH (signal in output, exit 0, 5s) ===
Thread 1 received signal SIGSEGV, Segmentation fault.
#0  0x00007f1234 in some_stdlib_func () at /usr/lib/stdlib.c:100
#1  0x00007f5678 in another_func () at /usr/lib/other.c:200
#2  0x00007f9abc in third_func () at /lib/system.c:300
#3  0x00007fdef0 in fourth_func () at /usr/local/lib/x.c:400
"""


class TestParseLogTotalRounds:
    def test_total_rounds_in_summary(self):
        result = parse_log(MULTI_ROUND_LOG)
        assert result["summary"]["total_rounds"] == 5


class TestAggregateFailures:
    def test_groups_by_test_name(self):
        from trun.log_analysis import aggregate_failures

        result = parse_log(MULTI_ROUND_LOG)
        failures = [t for t in result["tests"] if t["status"] != "PASS"]
        aggregated = aggregate_failures(failures, total_rounds=5)
        assert len(aggregated) == 1
        assert aggregated[0]["name"] == "flaky_test"

    def test_failure_rate(self):
        from trun.log_analysis import aggregate_failures

        result = parse_log(MULTI_ROUND_LOG)
        failures = [t for t in result["tests"] if t["status"] != "PASS"]
        aggregated = aggregate_failures(failures, total_rounds=5)
        assert aggregated[0]["failure_rate"] == "2/5"

    def test_failed_rounds_list(self):
        from trun.log_analysis import aggregate_failures

        result = parse_log(MULTI_ROUND_LOG)
        failures = [t for t in result["tests"] if t["status"] != "PASS"]
        aggregated = aggregate_failures(failures, total_rounds=5)
        assert aggregated[0]["failed_rounds"] == [1, 3]

    def test_no_user_frames_note_when_identical(self):
        from trun.log_analysis import aggregate_failures

        result = parse_log(MULTI_ROUND_LOG)
        failures = [t for t in result["tests"] if t["status"] != "PASS"]
        aggregated = aggregate_failures(failures, total_rounds=5)
        assert "user_frames_note" not in aggregated[0]

    def test_raw_frames_fallback(self):
        from trun.log_analysis import aggregate_failures

        result = parse_log(NO_USER_FRAMES_LOG)
        failures = [t for t in result["tests"] if t["status"] != "PASS"]
        aggregated = aggregate_failures(failures, total_rounds=1)
        assert len(aggregated) == 1
        assert "raw_frames" in aggregated[0]
        assert len(aggregated[0]["raw_frames"]) <= 3


class TestRawFramesFallback:
    def test_raw_frames_populated_when_no_user_frames(self):
        result = parse_log(NO_USER_FRAMES_LOG)
        test = result["tests"][0]
        assert test["user_frames"] == []
        assert len(test["raw_frames"]) <= 3
        assert len(test["raw_frames"]) > 0

    def test_raw_frames_empty_when_user_frames_present(self):
        result = parse_log(CRASH_LOG)
        test = result["tests"][0]
        assert len(test["user_frames"]) > 0
        assert test["raw_frames"] == []
