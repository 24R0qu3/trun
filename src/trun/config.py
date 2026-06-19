import os
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir("trun"))
DATA_DIR = Path(platformdirs.user_data_dir("trun"))
PLAYLISTS_DIR = CONFIG_DIR / "playlists"
LOG_FILE = DATA_DIR / "last_run.log"
RUN_HISTORY_FILE = DATA_DIR / "run_history.jsonl"
MAX_HISTORY_ENTRIES = 50

# ponytail: no hardcoded build path — built-in suite needs TRUN_BUILD_DIR or --build/--build_dir.
DEFAULT_BUILD = os.environ.get("TRUN_BUILD_DIR", "")
