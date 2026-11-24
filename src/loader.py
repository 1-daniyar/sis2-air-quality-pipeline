"""Stage 6 - store the cleaned records in SQLite3 (idempotent loading).

Run:  python -m src.loader  [--fresh]

* Creates data/output.db with two related tables (cities, measurements).
* PRIMARY KEY (record_id) and UNIQUE (city_id, observed_at) prevent duplicates.
* INSERT OR IGNORE makes loading idempotent: the same dataset can be loaded
  again without creating duplicates or changing stored rows.
* The loading step is executed TWICE on purpose to prove idempotency.
"""
import argparse
import hashlib
import re
import sqlite3
import sys

import pandas as pd

from src import config, summary

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cities (
    city_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    city_name  TEXT NOT NULL UNIQUE,
    region     TEXT NOT NULL,
    latitude   REAL NOT NULL,
    longitude  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS measurements (
    record_id         TEXT    PRIMARY KEY,
    city_id           INTEGER NOT NULL REFERENCES cities(city_id),
    observed_at       TEXT    NOT NULL,
    observed_date     TEXT    NOT NULL,
    hour              INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    day_period        TEXT    NOT NULL,
    is_weekend        INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
    pm10              REAL    CHECK (pm10 IS NULL OR pm10 >= 0),
    pm2_5             REAL    CHECK (pm2_5 IS NULL OR pm2_5 >= 0),
    carbon_monoxide   REAL,
    nitrogen_dioxide  REAL,
    sulphur_dioxide   REAL,
    ozone             REAL,
    european_aqi      REAL,
    aqi_category      TEXT    NOT NULL,
    pm25_exceeds_who  INTEGER NOT NULL CHECK (pm25_exceeds_who IN (0, 1)),
    pm2_5_log         REAL,
    pm_ratio          REAL,
    pm2_5_zscore      REAL,
    collected_at      TEXT    NOT NULL,
    loaded_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (city_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_measurements_city_date ON measurements(city_id, observed_date);
"""

MEASUREMENT_COLUMNS = [
    "record_id", "city_id", "observed_at", "observed_date", "hour", "day_period", "is_weekend",
    "pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide", "sulphur_dioxide", "ozone", "european_aqi",
    "aqi_category", "pm25_exceeds_who", "pm2_5_log", "pm_ratio", "pm2_5_zscore", "collected_at",
]


def connect(db_path=config.DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_SQL)
    return connection


def _py(value):
    """pandas/NumPy scalar -> plain Python value (NaN -> None) for sqlite3."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, float) and value != value:
            return None
    return value


def load_dataframe(connection: sqlite3.Connection, frame: pd.DataFrame) -> dict:
    """Insert rows; rows that already exist are ignored. Returns counts."""
    before = connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]

    cities = frame.groupby("city", as_index=False).agg(region=("region", "first"),
                                                       latitude=("latitude", "first"), longitude=("longitude", "first"))
    connection.executemany(
        "INSERT OR IGNORE INTO cities (city_name, region, latitude, longitude) VALUES (?, ?, ?, ?)",
        [(r.city, r.region, float(r.latitude), float(r.longitude)) for r in cities.itertuples()])
    city_ids = dict(connection.execute("SELECT city_name, city_id FROM cities").fetchall())

    rows = frame.assign(
        city_id=frame["city"].map(city_ids),
        observed_at=frame["time"],
        observed_date=frame["date"],
    )[MEASUREMENT_COLUMNS]
    payload = [tuple(_py(v) for v in row) for row in rows.itertuples(index=False, name=None)]

    placeholders = ", ".join("?" for _ in MEASUREMENT_COLUMNS)
    with connection:   # one transaction
        connection.executemany(
            f"INSERT OR IGNORE INTO measurements ({', '.join(MEASUREMENT_COLUMNS)}) VALUES ({placeholders})", payload)
    after = connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    return {"rows_offered": len(payload), "rows_inserted": after - before,
            "rows_ignored_as_existing": len(payload) - (after - before), "rows_in_table_after": after}


def table_checksum(connection: sqlite3.Connection) -> str:
    """SHA-256 over all stored values (loaded_at excluded) - changes if any stored value changes."""
    digest = hashlib.sha256()
    query = f"SELECT {', '.join(MEASUREMENT_COLUMNS)} FROM measurements ORDER BY record_id"
    for row in connection.execute(query):
        digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()


def run_verification_queries(connection: sqlite3.Connection) -> dict:
    """Execute every named query from sql/verification_queries.sql."""
    text = config.SQL_VERIFICATION_FILE.read_text(encoding="utf-8")
    results = {}
    for block in re.split(r"^-- name:", text, flags=re.MULTILINE)[1:]:
        name, _, sql = block.partition("\n")
        if not sql.strip():
            continue
        cursor = connection.execute(sql.strip().rstrip(";"))
        columns = [d[0] for d in cursor.description]
        results[name.strip()] = {"sql": sql.strip(), "columns": columns, "rows": [list(r) for r in cursor.fetchall()]}
    return results


def run(fresh: bool = False) -> int:
    if not config.CLEANED_CSV.exists():
        print(f"[loader] ERROR {config.CLEANED_CSV} not found - run the cleaner first", flush=True)
        return 1
    if fresh and config.DB_PATH.exists():
        config.DB_PATH.unlink()
        print("[loader] --fresh: previous database deleted", flush=True)

    frame = pd.read_csv(config.CLEANED_CSV)
    processed = len(frame)
    try:
        connection = connect()
        first = load_dataframe(connection, frame)
        checksum_1 = table_checksum(connection)
        print(f"[loader] load #1: offered={first['rows_offered']} inserted={first['rows_inserted']} "
              f"ignored={first['rows_ignored_as_existing']} table_rows={first['rows_in_table_after']}", flush=True)

        second = load_dataframe(connection, frame)       # idempotency test: same dataset again
        checksum_2 = table_checksum(connection)
        print(f"[loader] load #2: offered={second['rows_offered']} inserted={second['rows_inserted']} "
              f"ignored={second['rows_ignored_as_existing']} table_rows={second['rows_in_table_after']}", flush=True)

        idempotent = second["rows_inserted"] == 0 and checksum_1 == checksum_2 \
            and first["rows_in_table_after"] == second["rows_in_table_after"]
        print(f"[loader] idempotent: {idempotent} (checksum unchanged: {checksum_1 == checksum_2})", flush=True)

        queries = run_verification_queries(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        fk_problems = connection.execute("PRAGMA foreign_key_check").fetchall()
        stored_total = connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        db_ids = {r[0] for r in connection.execute("SELECT record_id FROM measurements")}
        connection.close()
    except sqlite3.Error as exc:
        print(f"[loader] ERROR database problem: {exc!r}", flush=True)
        return 1

    cleaned_in_db = int(frame["record_id"].isin(db_ids).sum())
    idem = {"first_load": first, "second_load": second, "checksum_after_first": checksum_1,
            "checksum_after_second": checksum_2, "idempotent": idempotent}
    summary.write_json(config.IDEMPOTENCY_REPORT, idem)
    summary.write_json(config.SQL_VERIFICATION_RESULTS, {"integrity_check": integrity,
                                                          "foreign_key_violations": len(fk_problems), "queries": queries})
    summary.update("loading", {"processed_records": processed, "stored_records": stored_total,
                               "cleaned_records_found_in_db": cleaned_in_db, "idempotent": idempotent,
                               "integrity_check": integrity})

    print(f"[loader] processed={processed} stored_in_db={stored_total} cleaned_found_in_db={cleaned_in_db}", flush=True)
    for name, result in queries.items():
        print(f"[loader] SQL {name}: {result['rows'][:6]}", flush=True)
    ok = idempotent and cleaned_in_db == processed and integrity == "ok" and not fk_problems
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load cleaned records into SQLite3")
    parser.add_argument("--fresh", action="store_true", help="delete the existing database first")
    sys.exit(run(fresh=parser.parse_args().fresh))
