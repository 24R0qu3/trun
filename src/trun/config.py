import json
import os
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir("trun"))
DATA_DIR = Path(platformdirs.user_data_dir("trun"))
PLAYLISTS_DIR = CONFIG_DIR / "playlists"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = DATA_DIR / "last_run.log"
RUN_HISTORY_FILE = DATA_DIR / "run_history.jsonl"
MAX_HISTORY_ENTRIES = 50


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def set_config(key: str, value: str) -> None:
    cfg = _read_config()
    cfg[key] = value
    atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))


# ponytail: no hardcoded build path — resolve from --build > $TRUN_BUILD_DIR > config.json > "".
def resolve_build_dir(explicit: str | None = None) -> str:
    return explicit or os.environ.get("TRUN_BUILD_DIR") or _read_config().get("build_dir", "")


DEFAULT_BUILD = resolve_build_dir()
