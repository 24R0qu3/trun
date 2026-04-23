from dataclasses import dataclass, field


@dataclass
class TestEntry:
    name: str
    subdir: str             # "fast_running" / "long_running" / custom
    build_dir: str
    group: str
    executor: str = "gdb"   # per-section executor; overridable at run time
    timeout: int | None = None  # explicit override; None → use executor default for subdir
    test_cases: list[str] = field(default_factory=list)  # empty = run all


@dataclass
class TestResult:
    name: str
    group: str
    status: str             # PASS / FAIL / TIMEOUT / INTR / SKIP
    duration_secs: int | None
    round_num: int = 1


@dataclass
class RunResult:
    results: list[TestResult] = field(default_factory=list)
    total_secs: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
