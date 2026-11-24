"""Stage 8 - end-to-end pipeline verification and data-quality report.

Run:  python -m src.verification

Collects the counts of every stage from data/pipeline_summary.json, explains
the differences between them and checks the final database.
"""
import sqlite3
import sys

import pandas as pd

from src import config, summary


def build_counts(s: dict) -> list[dict]:
    collection, producer, consumer = s.get("collection", {}), s.get("producer", {}), s.get("consumer", {})
    validation, cleaning, loading = s.get("validation", {}), s.get("cleaning", {}), s.get("loading", {})
    return [
        {"stage": "Collected from API", "count": collection.get("collected_records")},
        {"stage": "Published to Kafka", "count": producer.get("published")},
        {"stage": "Consumed from Kafka", "count": consumer.get("consumed")},
        {"stage": "Valid after Pandera", "count": validation.get("valid")},
        {"stage": "Invalid (quarantined)", "count": validation.get("invalid")},
        {"stage": "After cleaning", "count": cleaning.get("records_after_cleaning")},
        {"stage": "Stored in SQLite3", "count": loading.get("stored_records")},
    ]


def explain(s: dict) -> list[str]:
    collected = s.get("collection", {}).get("collected_records")
    published = s.get("producer", {}).get("published")
    consumed = s.get("consumer", {}).get("consumed")
    valid = s.get("validation", {}).get("valid")
    invalid = s.get("validation", {}).get("invalid")
    cleaned = s.get("cleaning", {}).get("records_after_cleaning")
    stored = s.get("loading", {}).get("stored_records")
    processed = s.get("loading", {}).get("processed_records")
    notes = []

    def note(a, b, same, different):
        if None in (a, b):
            return
        notes.append(same if a == b else different(a - b))

    note(collected, published, "Every collected record was published to Kafka (no transmission loss).",
         lambda d: f"{d} record(s) were collected but not confirmed by the broker (see producer errors).")
    note(published, consumed, "Every published message was consumed (published = consumed).",
         lambda d: f"{d} published message(s) were not consumed (consumer stopped early or messages were unreadable).")
    if None not in (consumed, valid, invalid):
        notes.append(f"Validation split the {consumed} consumed records into {valid} valid and {invalid} invalid "
                     f"({'no record was rejected' if invalid == 0 else 'invalid ones were quarantined and excluded'}).")
    note(valid, cleaned, "No record was removed during cleaning.",
         lambda d: f"Cleaning removed {d} record(s): duplicates and/or rows with no PM10/PM2.5 value after interpolation "
                   f"(details in data/cleaning_report.json).")
    if None not in (cleaned, stored, processed):
        if stored == cleaned:
            notes.append("All cleaned records are stored in SQLite3 (cleaned = stored).")
        else:
            notes.append(f"The database holds {stored} rows but {cleaned} records were processed in this run: the database "
                         f"already contained rows from an earlier run. Use `python main.py --fresh` for a clean database.")
    return notes


def check_database() -> list[dict]:
    checks = []
    if not config.DB_PATH.exists() or not config.CLEANED_CSV.exists():
        return [{"check": "database and cleaned file exist", "passed": False, "detail": "missing file"}]
    cleaned = pd.read_csv(config.CLEANED_CSV)
    connection = sqlite3.connect(config.DB_PATH)
    try:
        db = pd.read_sql_query("SELECT m.record_id, c.city_name, m.observed_at, m.pm2_5 FROM measurements m "
                               "JOIN cities c ON c.city_id = m.city_id", connection)
        dup = connection.execute("SELECT COUNT(*) FROM (SELECT 1 FROM measurements GROUP BY city_id, observed_at HAVING COUNT(*) > 1)").fetchone()[0]
    finally:
        connection.close()

    ids_cleaned, ids_db = set(cleaned["record_id"]), set(db["record_id"])
    checks.append({"check": "every cleaned record is present in the database", "passed": ids_cleaned <= ids_db,
                   "detail": f"{len(ids_cleaned & ids_db)} of {len(ids_cleaned)} found"})
    checks.append({"check": "database has no duplicate (city, time) rows", "passed": dup == 0, "detail": f"{dup} duplicates"})
    checks.append({"check": "all configured cities are present", "passed": set(db["city_name"]) == set(config.CITY_NAMES),
                   "detail": ", ".join(sorted(set(db["city_name"])))})
    merged = cleaned.merge(db, on="record_id", suffixes=("", "_db"))
    same = bool(((merged["pm2_5"] - merged["pm2_5_db"]).abs() < 1e-9).all()) if len(merged) else False
    checks.append({"check": "stored PM2.5 values equal the cleaned values", "passed": same, "detail": f"{len(merged)} rows compared"})
    days = db["observed_at"].str.slice(0, 10).nunique()
    expected_days = (pd.Timestamp(config.END_DATE) - pd.Timestamp(config.START_DATE)).days + 1
    checks.append({"check": "database covers the requested period", "passed": days == expected_days,
                   "detail": f"{days} days stored, {expected_days} requested"})
    return checks


def run() -> int:
    s = summary.load()
    counts = build_counts(s)
    notes = explain(s)
    checks = check_database()
    idem = s.get("loading", {}).get("idempotent")
    checks.append({"check": "repeated loading creates no duplicates (idempotency)", "passed": bool(idem), "detail": "see data/idempotency_report.json"})
    validation_ok = s.get("validation", {}).get("validation_test_all_detected")
    checks.append({"check": "validation schema detects the deliberately invalid test records", "passed": bool(validation_ok), "detail": "see data/validation_test_results.csv"})
    checks.append({"check": "at least 200 records collected", "passed": (counts[0]["count"] or 0) >= 200, "detail": str(counts[0]["count"])})
    checks.append({"check": "at least 200 messages published and consumed",
                   "passed": (counts[1]["count"] or 0) >= 200 and (counts[2]["count"] or 0) >= 200,
                   "detail": f"published={counts[1]['count']} consumed={counts[2]['count']}"})

    summary.write_json(config.VERIFICATION_REPORT, {"counts": counts, "explanations": notes, "checks": checks})
    summary.update("verification", {"all_checks_passed": all(c["passed"] for c in checks)})

    print("[verify] record counts per stage:", flush=True)
    for row in counts:
        print(f"[verify]   {row['stage']:<24} {row['count']}", flush=True)
    for line in notes:
        print(f"[verify] note: {line}", flush=True)
    for c in checks:
        print(f"[verify] {'PASS' if c['passed'] else 'FAIL'}  {c['check']} ({c['detail']})", flush=True)
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(run())
