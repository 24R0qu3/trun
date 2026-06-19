"""Tests for server.py refactor:
- _load_entries helper
- Executor ABC enforcement
"""

from __future__ import annotations

import pytest
import yaml

import trun.server as server_mod
from trun.executor import Executor
from trun.models import TestEntry

# ── Executor ABC ──────────────────────────────────────────────────────────────


def test_executor_is_abstract():
    with pytest.raises(TypeError):
        Executor(name="x", description="y")


# ── _load_entries helper ──────────────────────────────────────────────────────


def _write_playlist(playlists_dir, name, build_dir="/tmp/build"):
    p = playlists_dir / f"{name}.yaml"
    data = {
        "groups": [
            {
                "name": "g",
                "build": build_dir,
                "executor": "gdb",
                "tests": [
                    {"name": "test_a", "subdir": "fast_running"},
                    {"name": "test_b", "subdir": "fast_running"},
                ],
            }
        ]
    }
    p.write_text(yaml.dump(data))
    return p


def test_load_entries_missing_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "PLAYLISTS_DIR", tmp_path)
    result = server_mod._load_entries({"playlist": "nonexistent"})
    assert "error" in result
    assert "not found" in result["error"]


def test_load_entries_ini_fallback_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "PLAYLISTS_DIR", tmp_path)
    (tmp_path / "old.ini").write_text("[g]\nbuild=/tmp\nfast_running=test_a\n")
    result = server_mod._load_entries({"playlist": "old"})
    assert "error" in result
    assert "ini" in result["error"].lower() or "migrate" in result["error"].lower()


def test_load_entries_named_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "PLAYLISTS_DIR", tmp_path)
    _write_playlist(tmp_path, "mypl")
    entries = server_mod._load_entries({"playlist": "mypl"})
    assert isinstance(entries, list)
    assert len(entries) == 2
    assert all(isinstance(e, TestEntry) for e in entries)


def test_load_entries_only_tests_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "PLAYLISTS_DIR", tmp_path)
    _write_playlist(tmp_path, "mypl")
    entries = server_mod._load_entries({"playlist": "mypl", "only_tests": ["test_a"]})
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0].name == "test_a"


def test_load_entries_only_tests_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "PLAYLISTS_DIR", tmp_path)
    _write_playlist(tmp_path, "mypl")
    result = server_mod._load_entries({"playlist": "mypl", "only_tests": ["no_such"]})
    assert "error" in result


def test_load_entries_builtin_returns_list(monkeypatch):
    fake_entries = [
        TestEntry(name="t", subdir="fast_running", build_dir="/b", group="g")
    ]
    monkeypatch.setattr(server_mod, "_data_load_builtin", lambda bd: fake_entries)
    entries = server_mod._load_entries({"build_dir": "/b"})
    assert entries == fake_entries


def test_load_entries_builtin_without_build_errors(monkeypatch):
    monkeypatch.setattr(server_mod, "DEFAULT_BUILD", "")
    result = server_mod._load_entries({})
    assert "error" in result
