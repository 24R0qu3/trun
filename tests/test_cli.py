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
