import trun.config as cfg
from trun.executor import get_executor
from trun.log_analysis import get_error_hint


# ── pytest executor maps test_cases to -k ───────────────────────────────────────
def test_pytest_build_command_no_cases():
    assert get_executor("pytest").build_command("tests/unit") == ["pytest", "tests/unit", "-v"]


def test_pytest_build_command_with_cases():
    cmd = get_executor("pytest").build_command("tests/unit", ["test_a", "test_b"])
    assert cmd == ["pytest", "tests/unit", "-v", "-k", "test_a or test_b"]


# ── valgrind failures produce an error_hint ─────────────────────────────────────
def test_valgrind_error_hint():
    lines = ["==123== ERROR SUMMARY: 3 errors from 2 contexts"]
    assert get_error_hint(lines, "FAIL") == "valgrind: 3 errors"


def test_valgrind_zero_errors_no_hint():
    lines = ["==123== ERROR SUMMARY: 0 errors from 0 contexts"]
    assert get_error_hint(lines, "FAIL") is None


# ── persistent build-dir config ─────────────────────────────────────────────────
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("TRUN_BUILD_DIR", raising=False)


def test_set_and_read_config(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    cfg.set_config("build_dir", "/opt/build")
    assert cfg._read_config()["build_dir"] == "/opt/build"


def test_resolve_build_dir_precedence(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    cfg.set_config("build_dir", "/from/config")
    assert cfg.resolve_build_dir() == "/from/config"
    monkeypatch.setenv("TRUN_BUILD_DIR", "/from/env")
    assert cfg.resolve_build_dir() == "/from/env"
    assert cfg.resolve_build_dir("/explicit") == "/explicit"


def test_resolve_build_dir_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert cfg.resolve_build_dir() == ""


def test_atomic_write(tmp_path):
    p = tmp_path / "f.txt"
    cfg.atomic_write(p, "one")
    assert p.read_text() == "one"
    cfg.atomic_write(p, "two")
    assert p.read_text() == "two"
