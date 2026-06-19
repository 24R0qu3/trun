from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from .config import MAX_HISTORY_ENTRIES, RUN_HISTORY_FILE, atomic_write


def _append_run_history(playlist_name: str | None, run_result: dict) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "playlist": playlist_name,
        "passed": run_result["passed"],
        "failed": run_result["failed"],
        "skipped": run_result["skipped"],
        "total_secs": run_result["total_secs"],
        "per_test": [
            {"name": r["name"], "status": r["status"]} for r in run_result.get("results", [])
        ],
    }
    RUN_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = RUN_HISTORY_FILE.read_text().splitlines() if RUN_HISTORY_FILE.exists() else []
    lines.append(json.dumps(entry, ensure_ascii=False))
    if len(lines) > MAX_HISTORY_ENTRIES:
        lines = lines[-MAX_HISTORY_ENTRIES:]
    atomic_write(RUN_HISTORY_FILE, "\n".join(lines) + "\n")


def _get_run_history(
    n: int = 10,
    compute_flakiness: bool = False,
    include_results: bool = False,
) -> dict:
    if not RUN_HISTORY_FILE.exists():
        return {"runs": [], "total_stored": 0}

    all_lines = [ln for ln in RUN_HISTORY_FILE.read_text().splitlines() if ln.strip()]
    tail = all_lines[-n:]

    runs = []
    for line in reversed(tail):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry: dict = {
            "ts": raw["ts"],
            "playlist": raw.get("playlist"),
            "passed": raw["passed"],
            "failed": raw["failed"],
            "skipped": raw["skipped"],
            "total_secs": raw["total_secs"],
        }
        if include_results:
            entry["per_test"] = raw.get("per_test", [])
        runs.append(entry)

    result: dict = {"runs": runs, "total_stored": len(all_lines)}

    if compute_flakiness and all_lines:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "total": 0})
        for line in all_lines[-n:]:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            for r in raw.get("per_test", []):
                c = counts[r["name"]]
                c["total"] += 1
                if r["status"] == "PASS":
                    c["pass"] += 1
                else:
                    c["fail"] += 1
        result["flakiness"] = {
            name: {
                "pass_rate": f"{d['pass']}/{d['total']}",
                "fail_rate": f"{d['fail']}/{d['total']}",
            }
            for name, d in sorted(counts.items())
        }

    return result
