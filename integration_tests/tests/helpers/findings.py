"""Spike measurements, merged into spikes/results/<spike>.json and committed with the spike notes."""
import json
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[3] / "spikes" / "results"


def record_finding(spike: str, key: str, value) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{spike}.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
