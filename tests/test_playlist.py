import pytest
import yaml

from trun.playlist import (
    _data_get_groups,
    _data_list_available_tests,
    _data_set_pipeline,
    _parse_ctest_subdirs,
    _resolve_playlist_path,
)


class TestParseCTestSubdirs:
    def test_parses_subdirs(self, tmp_path):
        cmake = tmp_path / "CTestTestfile.cmake"
        cmake.write_text('subdirs("foo")\nsubdirs("bar")\n')
        assert _parse_ctest_subdirs(cmake) == ["foo", "bar"]

    def test_ignores_comments(self, tmp_path):
        cmake = tmp_path / "CTestTestfile.cmake"
        cmake.write_text('# a comment\nsubdirs("baz")\n')
        assert _parse_ctest_subdirs(cmake) == ["baz"]

    def test_ignores_other_lines(self, tmp_path):
        cmake = tmp_path / "CTestTestfile.cmake"
        cmake.write_text('cmake_minimum_required(VERSION 3.16)\nsubdirs("qux")\n')
        assert _parse_ctest_subdirs(cmake) == ["qux"]

    def test_empty_file(self, tmp_path):
        cmake = tmp_path / "CTestTestfile.cmake"
        cmake.write_text("")
        assert _parse_ctest_subdirs(cmake) == []


class TestListAvailableTests:
    def _make_cmake(self, tmp_path, subdir: str, names: list[str]) -> None:
        d = tmp_path / "test" / subdir
        d.mkdir(parents=True)
        (d / "CTestTestfile.cmake").write_text("\n".join(f'subdirs("{n}")' for n in names) + "\n")

    def test_single_subdir(self, tmp_path):
        self._make_cmake(tmp_path, "fast_running", ["test_a", "test_b"])
        result = _data_list_available_tests(str(tmp_path), "fast_running")
        assert result["build_dir"] == str(tmp_path)
        assert result["tests"]["fast_running"] == ["test_a", "test_b"]

    def test_both_subdirs(self, tmp_path):
        self._make_cmake(tmp_path, "fast_running", ["fast_1"])
        self._make_cmake(tmp_path, "long_running", ["long_1", "long_2"])
        result = _data_list_available_tests(str(tmp_path))
        assert result["tests"]["fast_running"] == ["fast_1"]
        assert result["tests"]["long_running"] == ["long_1", "long_2"]

    def test_missing_cmake_returns_empty_list(self, tmp_path):
        result = _data_list_available_tests(str(tmp_path), "fast_running")
        assert result["tests"]["fast_running"] == []

    def test_both_subdirs_one_missing(self, tmp_path):
        self._make_cmake(tmp_path, "fast_running", ["only_fast"])
        result = _data_list_available_tests(str(tmp_path))
        assert result["tests"]["fast_running"] == ["only_fast"]
        assert result["tests"]["long_running"] == []


# ── _data_set_pipeline / _data_get_groups ────────────────────────────────────


@pytest.fixture()
def playlist_dir(tmp_path, monkeypatch):
    """Redirect PLAYLISTS_DIR to tmp_path for isolation."""
    import trun.playlist as pl
    monkeypatch.setattr(pl, "PLAYLISTS_DIR", tmp_path)
    return tmp_path


def _make_playlist(playlist_dir, name: str, groups: list[dict] | None = None) -> None:
    data = {"groups": groups or []}
    (playlist_dir / f"{name}.yaml").write_text(yaml.dump(data, default_flow_style=False))


class TestSetPipeline:
    def test_missing_playlist_returns_error(self, playlist_dir):
        result = _data_set_pipeline("nope", "grp", "cmake --build .")
        assert "error" in result

    def test_sets_build_cmd_on_existing_group(self, playlist_dir):
        _make_playlist(
            playlist_dir,
            "mypl",
            [{"name": "fast", "build": "/build", "executor": "gdb", "tests": []}],
        )
        result = _data_set_pipeline("mypl", "fast", "cmake --build . -j8")
        assert "error" not in result
        data = yaml.safe_load((playlist_dir / "mypl.yaml").read_text())
        grp = next(g for g in data["groups"] if g["name"] == "fast")
        assert grp["build_cmd"] == "cmake --build . -j8"

    def test_sets_configure_cmd(self, playlist_dir):
        _make_playlist(
            playlist_dir,
            "mypl",
            [{"name": "fast", "build": "/build", "executor": "gdb", "tests": []}],
        )
        _data_set_pipeline("mypl", "fast", "cmake --build .", configure_cmd="cmake -S /src -B /build")
        data = yaml.safe_load((playlist_dir / "mypl.yaml").read_text())
        grp = next(g for g in data["groups"] if g["name"] == "fast")
        assert grp["configure_cmd"] == "cmake -S /src -B /build"

    def test_creates_group_with_build_dir(self, playlist_dir):
        _make_playlist(playlist_dir, "mypl")
        result = _data_set_pipeline("mypl", "new_group", "make -j4", build_dir="/tmp/build")
        assert "error" not in result
        data = yaml.safe_load((playlist_dir / "mypl.yaml").read_text())
        names = [g["name"] for g in data["groups"]]
        assert "new_group" in names

    def test_create_group_without_build_dir_returns_error(self, playlist_dir):
        _make_playlist(playlist_dir, "mypl")
        result = _data_set_pipeline("mypl", "ghost", "make")
        assert "error" in result

    def test_updates_existing_build_cmd(self, playlist_dir):
        _make_playlist(
            playlist_dir,
            "mypl",
            [{"name": "fast", "build": "/b", "executor": "gdb", "tests": [], "build_cmd": "old"}],
        )
        _data_set_pipeline("mypl", "fast", "new_cmd")
        data = yaml.safe_load((playlist_dir / "mypl.yaml").read_text())
        assert data["groups"][0]["build_cmd"] == "new_cmd"


class TestGetGroups:
    def test_missing_playlist_returns_error(self, playlist_dir):
        result = _data_get_groups("nope")
        assert isinstance(result, dict)
        assert "error" in result

    def test_returns_groups_list(self, playlist_dir):
        _make_playlist(
            playlist_dir,
            "mypl",
            [{"name": "fast", "build": "/b", "executor": "gdb", "tests": []}],
        )
        result = _data_get_groups("mypl")
        assert isinstance(result, list)
        assert result[0]["name"] == "fast"

    def test_returns_pipeline_fields(self, playlist_dir):
        _make_playlist(
            playlist_dir,
            "mypl",
            [
                {
                    "name": "fast",
                    "build": "/b",
                    "executor": "gdb",
                    "tests": [],
                    "build_cmd": "cmake --build .",
                    "configure_cmd": "cmake -S /s -B /b",
                }
            ],
        )
        groups = _data_get_groups("mypl")
        assert groups[0]["build_cmd"] == "cmake --build ."
        assert groups[0]["configure_cmd"] == "cmake -S /s -B /b"

    def test_empty_playlist_returns_empty_list(self, playlist_dir):
        _make_playlist(playlist_dir, "mypl")
        result = _data_get_groups("mypl")
        assert result == []
