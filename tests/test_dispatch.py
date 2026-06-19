import json

import trun.playlist as pl_mod
from trun import server as srv


def _call(name, args):
    import asyncio

    out = asyncio.run(srv.call_tool(name, args))
    return json.loads(out[0].text)


def test_build_tests_missing_dir():
    r = _call("build_tests", {"build_dir": "/no/such/dir/xyz"})
    assert "error" in r
    assert "not found" in r["error"].lower()


def test_build_tests_explicit_ok(tmp_path):
    r = _call("build_tests", {"build_dir": str(tmp_path), "cmd": "true"})
    assert r["failed"] == 0
    assert r["builds"][0]["status"] == "PASS"


def test_configure_build_needs_args():
    r = _call("configure_build", {})
    assert "error" in r


def test_configure_build_explicit_ok(tmp_path):
    r = _call("configure_build", {"build_dir": str(tmp_path), "cmd": "true"})
    assert r["failed"] == 0
    assert r["results"][0]["status"] == "PASS"


def test_set_pipeline_then_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(pl_mod, "PLAYLISTS_DIR", tmp_path)
    _call("create_playlist", {"name": "pp"})
    r = _call(
        "set_pipeline",
        {"playlist": "pp", "group": "g", "build_cmd": "make", "build_dir": "/b"},
    )
    assert "error" not in r
    groups = pl_mod._data_get_groups("pp")
    assert groups[0]["build_cmd"] == "make"


def test_rebuild_explicit_cleans_cache(tmp_path):
    (tmp_path / "CMakeCache.txt").write_text("x")
    (tmp_path / "CMakeFiles").mkdir()
    r = _call("rebuild", {"build_dir": str(tmp_path), "cmd": "true"})
    assert not (tmp_path / "CMakeCache.txt").exists()
    assert not (tmp_path / "CMakeFiles").exists()
    assert r["clean"] == "cache"
    assert r["build"]["builds"][0]["status"] == "PASS"


def test_rebuild_clean_none_keeps_cache(tmp_path):
    (tmp_path / "CMakeCache.txt").write_text("x")
    r = _call("rebuild", {"build_dir": str(tmp_path), "cmd": "true", "clean": "none"})
    assert (tmp_path / "CMakeCache.txt").exists()
    assert r["build"]["builds"][0]["status"] == "PASS"


def test_rebuild_runs_configure_then_build(tmp_path):
    r = _call("rebuild", {"build_dir": str(tmp_path), "configure_cmd": "true", "cmd": "true"})
    assert r["configure"]["results"][0]["status"] == "PASS"
    assert r["build"]["builds"][0]["status"] == "PASS"


def test_rebuild_playlist_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(pl_mod, "PLAYLISTS_DIR", tmp_path)
    bd = tmp_path / "b"
    bd.mkdir()
    _call("create_playlist", {"name": "rb"})
    _call(
        "set_pipeline",
        {
            "playlist": "rb",
            "group": "g",
            "build_cmd": "true",
            "configure_cmd": "true",
            "build_dir": str(bd),
        },
    )
    r = _call("rebuild", {"playlist": "rb"})
    assert r["configure"]["results"][0]["status"] == "PASS"
    assert r["build"]["builds"][0]["status"] == "PASS"


def test_rebuild_needs_target():
    r = _call("rebuild", {})
    assert "error" in r
