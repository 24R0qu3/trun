import json

import pytest

from trun.history import _append_run_history, _get_run_history


def _make_run(passed: int, failed: int, names: list[str], statuses: list[str]) -> dict:
    return {
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "total_secs": 10,
        "results": [{"name": n, "status": s, "round": 1} for n, s in zip(names, statuses)],
    }


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    import trun.config as cfg
    import trun.history as h

    history_file = tmp_path / "run_history.jsonl"
    monkeypatch.setattr(cfg, "RUN_HISTORY_FILE", history_file)
    monkeypatch.setattr(h, "RUN_HISTORY_FILE", history_file)
    return history_file


def test_append_and_get_single_run():
    run = _make_run(1, 0, ["test_a"], ["PASS"])
    _append_run_history("my_playlist", run)
    result = _get_run_history(n=10)
    assert result["total_stored"] == 1
    assert len(result["runs"]) == 1
    assert result["runs"][0]["playlist"] == "my_playlist"
    assert result["runs"][0]["passed"] == 1
    assert result["runs"][0]["failed"] == 0


def test_get_history_most_recent_first():
    _append_run_history("p1", _make_run(1, 0, ["a"], ["PASS"]))
    _append_run_history("p2", _make_run(0, 1, ["a"], ["FAIL"]))
    result = _get_run_history(n=2)
    assert result["runs"][0]["playlist"] == "p2"
    assert result["runs"][1]["playlist"] == "p1"


def test_get_history_n_limits_output():
    for i in range(5):
        _append_run_history(f"p{i}", _make_run(1, 0, ["a"], ["PASS"]))
    result = _get_run_history(n=2)
    assert len(result["runs"]) == 2
    assert result["total_stored"] == 5


def test_per_test_hidden_by_default():
    _append_run_history(None, _make_run(1, 0, ["test_a"], ["PASS"]))
    result = _get_run_history(n=1)
    assert "per_test" not in result["runs"][0]


def test_include_results_opt_in():
    _append_run_history(None, _make_run(1, 0, ["test_a"], ["PASS"]))
    result = _get_run_history(n=1, include_results=True)
    assert "per_test" in result["runs"][0]
    assert result["runs"][0]["per_test"][0]["name"] == "test_a"


def test_compute_flakiness():
    _append_run_history(None, _make_run(1, 1, ["a", "b"], ["PASS", "FAIL"]))
    _append_run_history(None, _make_run(2, 0, ["a", "b"], ["PASS", "PASS"]))
    result = _get_run_history(n=10, compute_flakiness=True)
    assert "flakiness" in result
    assert result["flakiness"]["a"]["pass_rate"] == "2/2"
    assert result["flakiness"]["b"]["pass_rate"] == "1/2"
    assert result["flakiness"]["b"]["fail_rate"] == "1/2"


def test_max_history_cap(monkeypatch):
    import trun.config as cfg
    import trun.history as h

    monkeypatch.setattr(cfg, "MAX_HISTORY_ENTRIES", 3)
    monkeypatch.setattr(h, "MAX_HISTORY_ENTRIES", 3)

    for i in range(5):
        _append_run_history(f"p{i}", _make_run(1, 0, ["a"], ["PASS"]))

    result = _get_run_history(n=10)
    assert result["total_stored"] == 3


def test_empty_history():
    result = _get_run_history(n=10)
    assert result["runs"] == []
    assert result["total_stored"] == 0


def test_history_file_written_as_jsonl(isolated_history):
    _append_run_history("pl", _make_run(1, 0, ["a"], ["PASS"]))
    _append_run_history("pl", _make_run(0, 1, ["a"], ["FAIL"]))
    lines = [ln for ln in isolated_history.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        json.loads(ln)
