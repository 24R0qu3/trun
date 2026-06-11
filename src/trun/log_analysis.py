from __future__ import annotations

import re
from collections import defaultdict

_SECTION_HEADER = re.compile(
    r"^=== \[round (\d+)\] (\S+)(?: \[([^\]]+)\])?: "
    r"(PASS|FAIL|TIMEOUT|CRASH|SKIP|INTR) \(([^)]+)\) ===$"
)
_SIGNAL_RE = re.compile(r"received signal (SIG\w+)")
_ASSERTION_RE = re.compile(r"Assertion `(.+)' failed")
_QT_FAIL_RE = re.compile(r"^FAIL\s*:")
_VALGRIND_RE = re.compile(r"ERROR SUMMARY: (\d+) errors")
_FRAME_RE = re.compile(r"^#\d+")
_SYSTEM_PATHS = ("/usr/", "/lib/", "/home/qt/", "nptl/", "sysdeps/", "stdlib/")


def _is_user_frame(line: str) -> bool:
    if not _FRAME_RE.match(line.strip()):
        return False
    m = re.search(r" at (/\S+)", line)
    if not m:
        return False
    path = m.group(1)
    return not any(sp in path for sp in _SYSTEM_PATHS)


def _split_sections(log_text: str) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    body: list[str] = []

    for line in log_text.splitlines():
        m = _SECTION_HEADER.match(line)
        if m:
            if current is not None:
                current["lines"] = body
                sections.append(current)
            current = {
                "round": int(m.group(1)),
                "name": m.group(2),
                "test_cases": [tc.strip() for tc in m.group(3).split(",")] if m.group(3) else [],
                "status": m.group(4),
                "duration": m.group(5),
            }
            body = []
        elif current is not None:
            body.append(line)

    if current is not None:
        current["lines"] = body
        sections.append(current)

    return sections


def _analyze_section(section: dict) -> dict:
    lines = section["lines"]
    result: dict = {
        "name": section["name"],
        "test_cases": section["test_cases"],
        "status": section["status"],
        "duration": section["duration"],
        "round": section["round"],
        "crash_type": None,
        "signal": None,
        "assertion": None,
        "user_frames": [],
        "qt_failures": [],
        "valgrind_errors": None,
    }

    for line in lines:
        m = _SIGNAL_RE.search(line)
        if m:
            result["signal"] = m.group(1)
            result["crash_type"] = result["crash_type"] or "signal"

        m = _ASSERTION_RE.search(line)
        if m:
            result["assertion"] = m.group(1)
            result["crash_type"] = result["crash_type"] or "assertion"

        if _QT_FAIL_RE.match(line):
            result["qt_failures"].append(line.strip())
            result["crash_type"] = result["crash_type"] or "qt_fail"

        m = _VALGRIND_RE.search(line)
        if m:
            result["valgrind_errors"] = int(m.group(1))

        if _is_user_frame(line):
            result["user_frames"].append(line.strip())

    # Retroactively fix PASS sections that contain a crash signal (P9 legacy logs)
    if result["signal"] and result["status"] == "PASS":
        result["status"] = "CRASH"
        result["crash_type"] = result["crash_type"] or "signal"

    # Add raw_frames fallback when no user frames
    if not result["user_frames"]:
        all_frames = [line.strip() for line in lines if _FRAME_RE.match(line.strip())]
        result["raw_frames"] = all_frames[:3]
    else:
        result["raw_frames"] = []

    return result


def parse_log(log_text: str) -> dict:
    sections = _split_sections(log_text)
    tests = [_analyze_section(s) for s in sections]
    total = len(tests)
    passed = sum(1 for t in tests if t["status"] == "PASS")
    total_rounds = max((t["round"] for t in tests), default=1)
    return {
        "tests": tests,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "total_rounds": total_rounds,
        },
    }


def get_error_hint(section_lines: list[str], status: str) -> str | None:
    if status == "PASS":
        return None
    signal = assertion = qt_fail = None
    for line in section_lines:
        m = _SIGNAL_RE.search(line)
        if m:
            signal = m.group(1)
        m = _ASSERTION_RE.search(line)
        if m:
            assertion = m.group(1)
        if _QT_FAIL_RE.match(line) and qt_fail is None:
            qt_fail = line.strip()[:120]
    if signal and assertion:
        return f"{signal}: {assertion}"
    if signal:
        return signal
    if qt_fail:
        return qt_fail
    if assertion:
        return f"assertion failed: {assertion}"
    return None


def aggregate_failures(tests: list[dict], total_rounds: int) -> list[dict]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for t in tests:
        by_name[t["name"]].append(t)

    result = []
    for name, instances in by_name.items():
        failed_rounds = sorted(t["round"] for t in instances)
        n_fail = len(failed_rounds)

        # Deduplicate stacks: group by frame tuple, pick most common
        frame_groups: dict[tuple, list] = defaultdict(list)
        for t in instances:
            key = tuple(t["user_frames"])
            frame_groups[key].append(t)
        best_key = max(frame_groups, key=lambda k: len(frame_groups[k]))
        best_frames = list(best_key)
        best_count = len(frame_groups[best_key])
        n_variants = len(frame_groups)

        # Raw frames fallback if no user frames at all
        raw_frames: list[str] = []
        if not best_frames:
            for t in instances:
                if t.get("raw_frames"):
                    raw_frames = t["raw_frames"]
                    break

        # Merge qt_failures (deduplicated)
        qt_failures = list(dict.fromkeys(f for t in instances for f in t["qt_failures"]))

        first = instances[0]
        entry: dict = {
            "name": name,
            "failure_rate": f"{n_fail}/{total_rounds}",
            "failed_rounds": failed_rounds,
            "status": first["status"],
            "duration": first["duration"],
            "crash_type": first["crash_type"],
            "signal": first["signal"],
            "assertion": first["assertion"],
            "user_frames": best_frames,
            "qt_failures": qt_failures,
            "valgrind_errors": first["valgrind_errors"],
        }
        if not best_frames and raw_frames:
            entry["raw_frames"] = raw_frames
        if best_frames and best_count < n_fail:
            entry["user_frames_note"] = f"seen {best_count}x ({n_variants} variant(s))"
        result.append(entry)
    return result


def filter_log_lines(
    lines: list[str],
    test_filter: str | None,
    errors_only: bool,
) -> list[str]:
    result: list[str] = []
    current_block: list[str] = []
    keep = False

    def flush() -> None:
        if keep:
            result.extend(current_block)

    for line in lines:
        m = _SECTION_HEADER.match(line)
        if m:
            flush()
            current_block = [line]
            test_name = m.group(2)
            status = m.group(4)
            keep = True
            if test_filter and test_filter not in test_name:
                keep = False
            if errors_only and status == "PASS":
                keep = False
        else:
            current_block.append(line)

    flush()
    return result
