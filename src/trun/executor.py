from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Executor:
    name: str
    description: str
    timeouts: dict[str, int] = field(default_factory=dict)

    def build_command(self, binary: str, test_cases: list[str] | None = None) -> list[str]:
        raise NotImplementedError

    def default_timeout(self, subdir: str) -> int:
        return self.timeouts.get(subdir, self.timeouts.get("default", 180))


class GDBExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(
            name="gdb",
            description="Run binary under GDB with backtrace on crash",
            timeouts={"fast_running": 60, "long_running": 180, "default": 180},
        )

    def build_command(self, binary: str, test_cases: list[str] | None = None) -> list[str]:
        base = ["gdb", "--return-child-result", "-batch", "-ex", "run", "-ex", "bt", "-ex", "quit"]
        if test_cases:
            return base + ["--args", binary, *test_cases]
        return base + [binary]


class DirectExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(
            name="direct",
            description="Run binary directly without any wrapper",
            timeouts={"fast_running": 60, "long_running": 180, "default": 180},
        )

    def build_command(self, binary: str, test_cases: list[str] | None = None) -> list[str]:
        return [binary, *test_cases] if test_cases else [binary]


class ValgrindExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(
            name="valgrind",
            description="Run binary under Valgrind for memory error detection",
            timeouts={"fast_running": 120, "long_running": 360, "default": 360},
        )

    def build_command(self, binary: str, test_cases: list[str] | None = None) -> list[str]:
        base = ["valgrind", "--leak-check=full", "--error-exitcode=1", binary]
        return base + list(test_cases) if test_cases else base


class PytestExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(
            name="pytest",
            description="Run Python tests via pytest",
            timeouts={"fast_running": 60, "long_running": 180, "default": 180},
        )

    def build_command(self, binary: str, test_cases: list[str] | None = None) -> list[str]:
        return ["pytest", binary, "-v"]


_EXECUTORS: dict[str, Executor] = {
    "gdb": GDBExecutor(),
    "direct": DirectExecutor(),
    "valgrind": ValgrindExecutor(),
    "pytest": PytestExecutor(),
}


def get_executor(name: str) -> Executor:
    exc = _EXECUTORS.get(name)
    if exc is None:
        raise ValueError(f"Unknown executor '{name}'. Available: {list(_EXECUTORS)}")
    return exc


def list_executors() -> list[dict]:
    return [
        {"name": e.name, "description": e.description, "timeouts": e.timeouts}
        for e in _EXECUTORS.values()
    ]
