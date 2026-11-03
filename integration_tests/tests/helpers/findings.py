"""Spike measurements, merged into spikes/results/<spike>.json and committed with the spike notes."""
import fcntl
import json
import os
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[3] / "spikes" / "results"


def record_finding(spike: str, key: str, value) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{spike}.json"
    with open(RESULTS_DIR / f".{spike}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # concurrent writers (parallel tests, threads) must not lose keys
        data = json.loads(path.read_text()) if path.exists() else {}
        data[key] = value
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
        os.replace(tmp, path)
