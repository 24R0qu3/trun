from trun.playlist import _parse_ctest_subdirs


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
