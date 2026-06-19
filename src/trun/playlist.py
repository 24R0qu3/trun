from __future__ import annotations

import re as _re
from pathlib import Path

import yaml

from .config import DEFAULT_BUILD, PLAYLISTS_DIR
from .models import TestEntry

_CTEST_SUBDIRS_RE = _re.compile(r'^subdirs\("([^"]+)"\)')

_FAST_TESTS = [
    "rst_tfr_rst_smc_core_mediator",
    "rst_tfr_rst_smc_database",
    "rst_tfr_rst_smc_utils_container_difference",
    "rst_tfr_rst_smc_fmis_device_internal",
    "rst_tfr_rst_smc_isobus_isoxml",
    "rst_tfr_rst_smc_logging",
    "rst_tfr_rst_smc_settings",
    "rst_tfr_rst_smc_settings_config",
    "rst_tfr_rst_smc_storage",
    "rst_tfr_rst_smc_utils_nholthaus_units",
    "rst_tfr_rst_smc_zmq",
    "rst_tfr_rst_smc_platform_can",
    "rst_tfr_rst_smc_platform_datetime_iso8601",
    "rst_tfr_rst_smc_isobus",
    "rst_tfr_rst_smc_vt_server",
    "rst_tfr_rst_smc_osb",
    "rst_tfr_rst_smc_mics",
    "rst_tfr_rst_smc_integrations_exception_isobus",
    "rst_tfr_rst_smc_isobus_diagnostic",
    "rst_tfr_rst_smc_isobus_isolib",
    "rst_tfr_rst_smc_isobus_isb",
    "rst_tfr_rst_smc_client_isobus_isb",
    "rst_tfr_rst_smc_mics_fmis_isobus",
]

_LONG_TESTS = [
    "rst_tlr_rst_smc_fmis_device_interfaces",
    "rst_tlr_rst_smc_fmis_task",
    "rst_tlr_rst_smc_fmis_imp_exp",
    "rst_tlr_rst_smc_isobus_isolib",
    "rst_tlr_rst_smc_isobus",
    "rst_tlr_rst_smc_osb_tc_server",
    "rst_tlr_rst_smc_mics_fmis_isobus",
]


def _data_load_builtin(build_dir: str = DEFAULT_BUILD) -> list[TestEntry]:
    entries = []
    for t in _FAST_TESTS:
        entries.append(
            TestEntry(name=t, subdir="fast_running", build_dir=build_dir, group="fast_running")
        )
    for t in _LONG_TESTS:
        entries.append(
            TestEntry(name=t, subdir="long_running", build_dir=build_dir, group="long_running")
        )
    return entries


def _data_load_playlist_file_ini(path: str) -> list[TestEntry]:
    """Load a legacy .ini playlist. Used only for migration."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Playlist file not found: {path}")

    entries: list[TestEntry] = []
    current_group: str | None = None
    current_build: str | None = None
    current_executor = "gdb"
    current_timeout_fast: int | None = None
    current_timeout_long: int | None = None

    for raw_line in p.read_text().splitlines():
        line = raw_line.split("#")[0].strip()
        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1].strip()
            current_build = None
            current_executor = "gdb"
            current_timeout_fast = None
            current_timeout_long = None
            continue

        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()

            if key == "build":
                current_build = val
            elif key == "executor":
                current_executor = val
            elif key == "timeout_fast":
                current_timeout_fast = int(val)
            elif key == "timeout_long":
                current_timeout_long = int(val)
            else:
                if current_build is None or current_group is None:
                    continue
                subdir = key
                timeout = current_timeout_fast if subdir == "fast_running" else current_timeout_long
                for t in val.split():
                    entries.append(
                        TestEntry(
                            name=t,
                            subdir=subdir,
                            build_dir=current_build,
                            group=current_group,
                            executor=current_executor,
                            timeout=timeout,
                        )
                    )

    return entries


def _data_load_playlist_file(path: str) -> list[TestEntry]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Playlist file not found: {path}")
    data = yaml.safe_load(p.read_text()) or {}
    entries: list[TestEntry] = []
    for group in data.get("groups", []):
        group_name = group["name"]
        build = group["build"]
        executor = group.get("executor", "gdb")
        timeout_fast = group.get("timeout_fast")
        timeout_long = group.get("timeout_long")
        for test in group.get("tests", []):
            subdir = test["subdir"]
            timeout = timeout_fast if subdir == "fast_running" else timeout_long
            entries.append(
                TestEntry(
                    name=test["name"],
                    subdir=subdir,
                    build_dir=build,
                    group=group_name,
                    executor=executor,
                    timeout=timeout,
                    test_cases=test.get("test_cases", []),
                )
            )
    return entries


def _data_list_playlists() -> list[dict]:
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    return [{"name": p.stem, "path": str(p)} for p in sorted(PLAYLISTS_DIR.glob("*.yaml"))]


def _resolve_playlist_path(name: str) -> Path:
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    return PLAYLISTS_DIR / f"{name}.yaml"


def _data_get_playlist(name: str) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}
    try:
        entries = _data_load_playlist_file(str(path))
    except Exception as e:
        return {"error": str(e)}
    return {
        "name": name,
        "path": str(path),
        "entries": [
            {
                "name": e.name,
                "subdir": e.subdir,
                "build_dir": e.build_dir,
                "group": e.group,
                "executor": e.executor,
                "timeout": e.timeout,
                "test_cases": e.test_cases,
            }
            for e in entries
        ],
    }


def _data_get_groups(name: str) -> list[dict] | dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("groups", [])


def _data_set_pipeline(
    name: str,
    group_name: str,
    build_cmd: str,
    configure_cmd: str | None = None,
    build_dir: str | None = None,
) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}
    data = yaml.safe_load(path.read_text()) or {"groups": []}
    groups = data.setdefault("groups", [])
    grp = next((g for g in groups if g["name"] == group_name), None)
    if grp is None:
        if not build_dir:
            return {"error": f"Group '{group_name}' not found; provide build_dir to create it"}
        grp = {"name": group_name, "build": build_dir, "executor": "gdb", "tests": []}
        groups.append(grp)
    grp["build_cmd"] = build_cmd
    if configure_cmd is not None:
        grp["configure_cmd"] = configure_cmd
    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    return {"message": f"Pipeline set for '{name}' [{group_name}]"}


def _data_create_playlist(name: str) -> dict:
    path = _resolve_playlist_path(name)
    if path.exists():
        return {"error": f"Playlist '{name}' already exists"}
    path.write_text(yaml.dump({"groups": []}, default_flow_style=False))
    return {"message": f"Created playlist '{name}'", "path": str(path)}


def _data_add_tests(
    name: str,
    group: str,
    build_dir: str,
    subdir: str,
    tests: list[dict],
    executor: str = "gdb",
    timeout_fast: int | None = None,
    timeout_long: int | None = None,
) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {
            "error": (
                f"Playlist '{name}' not found. Create it first with 'trun playlist create {name}'"
            )
        }

    data = yaml.safe_load(path.read_text()) or {"groups": []}
    groups = data.setdefault("groups", [])

    grp = next((g for g in groups if g["name"] == group), None)
    if grp is None:
        grp = {"name": group, "build": build_dir, "executor": executor, "tests": []}
        if timeout_fast is not None:
            grp["timeout_fast"] = timeout_fast
        if timeout_long is not None:
            grp["timeout_long"] = timeout_long
        groups.append(grp)

    existing = {t["name"]: t for t in grp["tests"]}
    added = 0
    for t in tests:
        t_name = t["name"] if isinstance(t, dict) else t
        t_cases = (t.get("test_cases") or []) if isinstance(t, dict) else []
        if t_name not in existing:
            entry: dict = {"name": t_name, "subdir": subdir}
            if t_cases:
                entry["test_cases"] = t_cases
            grp["tests"].append(entry)
            existing[t_name] = entry
            added += 1
        elif t_cases:
            existing[t_name]["test_cases"] = t_cases

    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    return {"message": f"Added {added} test(s) to '{name}' [{group}]"}


def _data_remove_tests(name: str, group: str, tests: list[str]) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}

    to_remove = set(tests)
    data = yaml.safe_load(path.read_text()) or {"groups": []}
    for grp in data.get("groups", []):
        if grp["name"] == group:
            grp["tests"] = [t for t in grp.get("tests", []) if t["name"] not in to_remove]

    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    return {"message": f"Removed tests from '{name}' [{group}]"}


def _data_delete_playlist(name: str) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}
    path.unlink()
    return {"message": f"Deleted playlist '{name}'"}


def _data_migrate_playlist(name: str) -> dict:
    """Convert a single .ini playlist to .yaml and delete the .ini."""
    ini_path = PLAYLISTS_DIR / f"{name}.ini"
    yaml_path = PLAYLISTS_DIR / f"{name}.yaml"
    if not ini_path.exists():
        return {"error": f"No .ini file found for playlist '{name}'"}
    if yaml_path.exists():
        return {"error": f"'{name}.yaml' already exists — delete it first"}

    entries = _data_load_playlist_file_ini(str(ini_path))
    groups_map: dict[str, dict] = {}
    for e in entries:
        if e.group not in groups_map:
            groups_map[e.group] = {
                "name": e.group,
                "build": e.build_dir,
                "executor": e.executor,
                "tests": [],
            }
        grp = groups_map[e.group]
        if e.timeout is not None:
            key = "timeout_fast" if e.subdir == "fast_running" else "timeout_long"
            grp[key] = e.timeout
        grp["tests"].append({"name": e.name, "subdir": e.subdir})

    yaml_path.write_text(
        yaml.dump(
            {"groups": list(groups_map.values())},
            default_flow_style=False,
            allow_unicode=True,
        )
    )
    ini_path.unlink()
    return {"message": f"Migrated '{name}' → {yaml_path}", "path": str(yaml_path)}


def _data_migrate_all_playlists() -> dict:
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    results = [
        {"name": p.stem, **_data_migrate_playlist(p.stem)}
        for p in sorted(PLAYLISTS_DIR.glob("*.ini"))
    ]
    return {"migrations": results}


def _parse_ctest_subdirs(cmake_path: Path) -> list[str]:
    return [
        m.group(1)
        for line in cmake_path.read_text().splitlines()
        if (m := _CTEST_SUBDIRS_RE.match(line.strip()))
    ]


def _data_list_available_tests(build_dir: str, subdir: str | None = None) -> dict:
    subdirs = [subdir] if subdir else ["fast_running", "long_running"]
    tests: dict[str, list[str]] = {}
    for sd in subdirs:
        cmake_path = Path(build_dir) / "test" / sd / "CTestTestfile.cmake"
        tests[sd] = _parse_ctest_subdirs(cmake_path) if cmake_path.exists() else []
    return {"build_dir": build_dir, "tests": tests}


def _data_create_playlist_from_dir(
    name: str,
    build_dir: str,
    subdir: str,
    group: str,
    executor: str = "gdb",
    timeout_fast: int | None = None,
    timeout_long: int | None = None,
) -> dict:
    cmake_path = Path(build_dir) / "test" / subdir / "CTestTestfile.cmake"
    if not cmake_path.exists():
        return {"error": f"CTestTestfile.cmake not found: {cmake_path}"}
    test_names = _parse_ctest_subdirs(cmake_path)
    if not test_names:
        return {"error": f"No subdirs() entries found in {cmake_path}"}
    if not _resolve_playlist_path(name).exists():
        _data_create_playlist(name)
    tests = [{"name": t} for t in test_names]
    result = _data_add_tests(
        name, group, build_dir, subdir, tests, executor, timeout_fast, timeout_long
    )
    result["discovered"] = len(test_names)
    result["tests"] = test_names
    return result
