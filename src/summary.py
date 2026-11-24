"""Tiny helper: every stage writes its counts into data/pipeline_summary.json."""
import json
from datetime import datetime, timezone

from src.config import DATA_DIR, PIPELINE_SUMMARY


def load() -> dict:
    if PIPELINE_SUMMARY.exists():
        try:
            return json.loads(PIPELINE_SUMMARY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def update(section: str, values: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    summary = load()
    values = dict(values)
    values["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary[section] = values
    PIPELINE_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
