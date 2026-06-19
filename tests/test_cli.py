import os
import subprocess
import sys
import textwrap
from pathlib import Path

import trun

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _run_cli(args, env_extra=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "trun.main", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _isolated_env(tmp_path):
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }


def _pytest_playlist(tmp_path, test_body: str) -> Path:
    builddir = tmp_path / "proj"
    builddir.mkdir(exist_ok=True)
    (builddir / "test_x.py").write_text(test_body)
    pl = tmp_path / "pl.yaml"
    pl.write_text(
        textwrap.dedent(f"""        groups:
          - name: g
            build: {builddir}
            executor: pytest
            tests:
              - name: test_x.py
                subdir: fast_running
        """)
    )
    return pl


def test_version_matches_package():
    r = _run_cli(["--version"])
    assert trun.__version__ in r.stdout


def test_run_exits_nonzero_on_failure(tmp_path):
    pl = _pytest_playlist(tmp_path, "def test_a():\n    assert False\n")
    r = _run_cli(["run", "--playlist", str(pl)], env_extra=_isolated_env(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr


def test_run_exits_zero_on_pass(tmp_path):
    pl = _pytest_playlist(tmp_path, "def test_a():\n    assert True\n")
    r = _run_cli(["run", "--playlist", str(pl)], env_extra=_isolated_env(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr


def test_builtin_without_build_dir_errors(tmp_path):
    env = _isolated_env(tmp_path)
    env["TRUN_BUILD_DIR"] = ""
    r = _run_cli(["run"], env_extra=env)
    assert r.returncode != 0
    assert "build" in (r.stdout + r.stderr).lower()


def _two_test_playlist(tmp_path) -> Path:
    builddir = tmp_path / "proj"
    builddir.mkdir(exist_ok=True)
    (builddir / "a.py").write_text("def test_a():\n    assert True\n")
    (builddir / "b.py").write_text("def test_b():\n    assert True\n")
    pl = tmp_path / "pl.yaml"
    pl.write_text(
        textwrap.dedent(f"""        groups:
          - name: g
            build: {builddir}
            executor: pytest
            tests:
              - name: a.py
                subdir: fast_running
              - name: b.py
                subdir: fast_running
        """)
    )
    return pl


def test_run_only_filters(tmp_path):
    pl = _two_test_playlist(tmp_path)
    r = _run_cli(
        ["run", "--playlist", str(pl), "--only", "a.py"], env_extra=_isolated_env(tmp_path)
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Tests  : 1" in r.stdout


def test_single_pass_and_fail(tmp_path):
    builddir = tmp_path / "proj"
    builddir.mkdir()
    (builddir / "ok.py").write_text("def test_x():\n    assert True\n")
    (builddir / "bad.py").write_text("def test_x():\n    assert False\n")
    env = _isolated_env(tmp_path)
    ok = _run_cli(
        ["single", "ok.py", "--build", str(builddir), "--executor", "pytest"], env_extra=env
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    bad = _run_cli(
        ["single", "bad.py", "--build", str(builddir), "--executor", "pytest"], env_extra=env
    )
    assert bad.returncode == 1, bad.stdout + bad.stderr


def test_playlist_add_from_dir(tmp_path):
    ctdir = tmp_path / "proj" / "test" / "fast_running"
    ctdir.mkdir(parents=True)
    (ctdir / "CTestTestfile.cmake").write_text('subdirs("rst_foo")\nsubdirs("rst_bar")\n')
    env = _isolated_env(tmp_path)
    add = _run_cli(
        [
            "playlist", "add-from-dir", "mypl",
            "--build", str(tmp_path / "proj"),
            "--subdir", "fast_running",
            "--group", "g",
        ],
        env_extra=env,
    )
    assert add.returncode == 0, add.stdout + add.stderr
    show = _run_cli(["playlist", "show", "mypl"], env_extra=env)
    assert "rst_foo" in show.stdout and "rst_bar" in show.stdout
