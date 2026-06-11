import os
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir("trun"))
DATA_DIR = Path(platformdirs.user_data_dir("trun"))
PLAYLISTS_DIR = CONFIG_DIR / "playlists"
LOG_FILE = DATA_DIR / "last_run.log"
RUN_HISTORY_FILE = DATA_DIR / "run_history.jsonl"
MAX_HISTORY_ENTRIES = 50

DEFAULT_BUILD = os.environ.get(
    "TRUN_BUILD_DIR",
    "/media/nielsruehr/F2EC62F5EC62B38F/projects/reichhardt/SMC4/ak1/rst_smart_command/build/CC_V1X90-Debug",
)
