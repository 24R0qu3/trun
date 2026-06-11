from trun.playlist import _data_list_available_tests, _parse_ctest_subdirs


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
