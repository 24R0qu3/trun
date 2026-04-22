from __future__ import annotations

from pathlib import Path

from .config import DEFAULT_BUILD, PLAYLISTS_DIR
from .models import TestEntry

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


def _data_load_playlist_file(path: str) -> list[TestEntry]:
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


def _data_list_playlists() -> list[dict]:
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    return [
        {"name": p.stem, "path": str(p)} for p in sorted(PLAYLISTS_DIR.glob("*.ini"))
    ]


def _resolve_playlist_path(name: str) -> Path:
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    return PLAYLISTS_DIR / f"{name}.ini"


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
            }
            for e in entries
        ],
    }


def _data_create_playlist(name: str) -> dict:
    path = _resolve_playlist_path(name)
    if path.exists():
        return {"error": f"Playlist '{name}' already exists"}
    path.write_text("")
    return {"message": f"Created playlist '{name}'", "path": str(path)}


def _data_add_tests(
    name: str,
    group: str,
    build_dir: str,
    subdir: str,
    tests: list[str],
    executor: str = "gdb",
    timeout_fast: int | None = None,
    timeout_long: int | None = None,
) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {
            "error": (
                f"Playlist '{name}' not found. "
                f"Create it first with 'trun playlist create {name}'"
            )
        }

    existing = path.read_text()
    section_header = f"[{group}]"

    if section_header in existing:
        lines = existing.splitlines()
        new_lines: list[str] = []
        in_section = False
        subdir_found = False

        for line in lines:
            stripped = line.strip()
            if stripped == section_header:
                in_section = True
            elif stripped.startswith("[") and stripped.endswith("]") and in_section:
                if not subdir_found:
                    new_lines.append(f"{subdir} = {' '.join(tests)}")
                in_section = False
                subdir_found = False

            if in_section and stripped.startswith(f"{subdir} ="):
                existing_tests = stripped[len(subdir) + 2 :].strip()
                line = f"{subdir} = {existing_tests} {' '.join(tests)}"
                subdir_found = True

            new_lines.append(line)

        if in_section and not subdir_found:
            new_lines.append(f"{subdir} = {' '.join(tests)}")

        path.write_text("\n".join(new_lines) + "\n")
    else:
        section_lines = [f"\n[{group}]", f"build = {build_dir}", f"executor = {executor}"]
        if timeout_fast is not None:
            section_lines.append(f"timeout_fast = {timeout_fast}")
        if timeout_long is not None:
            section_lines.append(f"timeout_long = {timeout_long}")
        section_lines.append(f"{subdir} = {' '.join(tests)}")
        path.write_text(existing.rstrip() + "\n" + "\n".join(section_lines) + "\n")

    return {"message": f"Added {len(tests)} test(s) to '{name}' [{group}]"}


def _data_remove_tests(name: str, group: str, tests: list[str]) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}

    to_remove = set(tests)
    lines = path.read_text().splitlines()
    new_lines: list[str] = []
    current_section: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()

        if current_section == group and "=" in stripped:
            key, _, val = stripped.partition("=")
            key = key.strip()
            if key not in ("build", "executor", "timeout_fast", "timeout_long"):
                remaining = [t for t in val.strip().split() if t not in to_remove]
                if remaining:
                    new_lines.append(f"{key} = {' '.join(remaining)}")
                continue

        new_lines.append(line)

    path.write_text("\n".join(new_lines) + "\n")
    return {"message": f"Removed tests from '{name}' [{group}]"}


def _data_delete_playlist(name: str) -> dict:
    path = _resolve_playlist_path(name)
    if not path.exists():
        return {"error": f"Playlist '{name}' not found"}
    path.unlink()
    return {"message": f"Deleted playlist '{name}'"}
