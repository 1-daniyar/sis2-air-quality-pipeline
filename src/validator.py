"""Stage 4 - data validation with Pandera.

Run:  python -m src.validator

Validates the records received from Kafka (data/consumed_data.csv) BEFORE any
cleaning. Valid rows go to data/valid_records.csv, invalid rows are quarantined
in data/invalid_records.csv together with the reason(s). Afterwards the schema
is tested with deliberately invalid test records (never written to the valid file).
"""
import sys

import pandas as pd

try:                                   # pandera >= 0.24
    import pandera.pandas as pa
except ImportError:                    # older pandera
    import pandera as pa

from src import config, summary

TIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"
ID_PATTERN = r"^[a-z0-9]+_\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"
WINDOW_START = pd.Timestamp(config.START_DATE)
WINDOW_END = pd.Timestamp(config.END_DATE) + pd.DateOffset(days=1)

# Human readable list of the validation rules (used by the README and the PDF report).
RULES = [
    ("R1 data types", "Every column must have the expected type (float for measurements, str for text, datetime for `time`); values that cannot be converted are rejected."),
    ("R2 required fields", "record_id, city, region, latitude, longitude, time and collected_at must not be null."),
    ("R3 numerical ranges", "pm10/pm2_5/nitrogen_dioxide/ozone in [0, 1000], sulphur_dioxide in [0, 2000], carbon_monoxide in [0, 50000] µg/m³, european_aqi in [0, 500]; latitude in [40, 56], longitude in [46, 88]. Missing measurements (null) are allowed here and handled in cleaning."),
    ("R4 allowed categories", "city must be one of the four configured cities and region one of the configured regions."),
    ("R5 date/time validation", "time must be a valid timestamp inside the requested collection window."),
    ("R6 unique identifier", "record_id must be unique (repeated occurrences are rejected, the first one is kept)."),
    ("R7 identifier format", "record_id must look like '<city>_YYYY-MM-DDTHH:MM'."),
    ("R8 unique (city, time)", "A city may have only one measurement per timestamp."),
    ("R9 custom cross-column rule", "PM2.5 is a part of PM10, therefore pm2_5 must not be larger than pm10 when both are present."),
]


def _pm25_not_above_pm10(frame: pd.DataFrame) -> pd.Series:
    pm10 = pd.to_numeric(frame["pm10"], errors="coerce")
    pm25 = pd.to_numeric(frame["pm2_5"], errors="coerce")
    return (pm25 <= pm10 + 1e-6) | pm10.isna() | pm25.isna()


def _measurement(upper: float) -> "pa.Column":
    return pa.Column(float, pa.Check.in_range(0, upper), nullable=True, coerce=True)


SCHEMA = pa.DataFrameSchema(
    columns={
        "record_id": pa.Column(str, pa.Check.str_matches(ID_PATTERN), nullable=False,
                               unique=True, report_duplicates="exclude_first"),
        "city": pa.Column(str, pa.Check.isin(config.CITY_NAMES), nullable=False),
        "region": pa.Column(str, pa.Check.isin(config.REGION_NAMES), nullable=False),
        "latitude": pa.Column(float, pa.Check.in_range(40, 56), nullable=False, coerce=True),
        "longitude": pa.Column(float, pa.Check.in_range(46, 88), nullable=False, coerce=True),
        "time": pa.Column(pa.DateTime, [pa.Check.ge(WINDOW_START), pa.Check.lt(WINDOW_END)], nullable=False, coerce=True),
        "pm10": _measurement(1000),
        "pm2_5": _measurement(1000),
        "carbon_monoxide": _measurement(50000),
        "nitrogen_dioxide": _measurement(1000),
        "sulphur_dioxide": _measurement(2000),
        "ozone": _measurement(1000),
        "european_aqi": _measurement(500),
        "collected_at": pa.Column(str, nullable=False),
    },
    checks=[pa.Check(_pm25_not_above_pm10, error="pm2_5 must not exceed pm10")],
    unique=["city", "time"],
    report_duplicates="exclude_first",
    strict=False,      # extra columns are tolerated
    coerce=False,
)


def validate_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Validate a DataFrame. Returns (valid_rows, invalid_rows_with_errors, failure_cases)."""
    frame = frame.reset_index(drop=True)
    try:
        SCHEMA.validate(frame, lazy=True)       # lazy=True collects ALL errors instead of stopping at the first
        return frame.copy(), frame.iloc[0:0].assign(validation_errors=pd.Series(dtype=str)), pd.DataFrame()
    except pa.errors.SchemaErrors as exc:
        failures = exc.failure_cases.copy()

    row_failures = failures[failures["index"].notna()].copy()
    schema_level = failures[failures["index"].isna() & failures["column"].isna()]
    if len(schema_level) and row_failures.empty:
        raise RuntimeError(f"Schema-level validation failure:\n{schema_level}")

    row_failures["index"] = row_failures["index"].astype(int)
    # Column-level failures name the column and the offending value; DataFrame-level rules
    # (uniqueness of (city, time), PM2.5 <= PM10) only name the rule.
    is_frame_rule = row_failures["schema_context"].astype(str).eq("DataFrameSchema").tolist()
    rules, reasons_list = [], []
    for frame_rule, column, check, value in zip(is_frame_rule, row_failures["column"], row_failures["check"],
                                                row_failures["failure_case"]):
        rule = str(check) if frame_rule else f"{column}: {check}"
        rules.append(rule)
        reasons_list.append(rule if frame_rule else f"{rule} (value={str(value)[:40]})")
    row_failures["rule"] = rules
    row_failures["reason"] = reasons_list
    reasons = row_failures.groupby("index")["reason"].agg(lambda s: " | ".join(dict.fromkeys(s.astype(str))))
    invalid_mask = frame.index.isin(reasons.index)
    invalid = frame[invalid_mask].copy()
    invalid["validation_errors"] = reasons.reindex(invalid.index).values
    return frame[~invalid_mask].copy(), invalid, row_failures


def make_invalid_test_records(template: dict) -> pd.DataFrame:
    """Six deliberately invalid records. Their ids start with 'test' so they are always recognisable."""
    base = dict(template)
    base_time = f"{config.START_DATE}T03:30"   # real data has only :00 timestamps

    def make(n: int, **changes) -> dict:
        record = dict(base)
        record.update({"record_id": f"test{n}_{base_time}", "time": f"{config.START_DATE}T{n:02d}:30"})
        record.update(changes)
        return record

    return pd.DataFrame([
        make(1, pm2_5=-12.5, pm10=20.0),                                 # negative concentration  (R3)
        make(2, city="Atlantis"),          # unknown city            (R4)
        make(3, time="not-a-date", record_id="test3_not-a-date"),          # impossible timestamp    (R5, R7)
        make(4, pm10=10.0, pm2_5=40.0),                                    # PM2.5 > PM10            (R9)
        make(5, city=template["city"], time=template["time"]),  # duplicate (city, time) (R8)
        make(6, record_id=None),                                           # missing required field  (R2)
    ])


def run_validation_test(valid_rows: pd.DataFrame) -> dict:
    """Demonstrate that the schema detects invalid records."""
    if valid_rows.empty:
        print("[validator] WARNING no valid rows available for the validation test", flush=True)
        return {}
    sample = valid_rows.head(5).copy()
    template = sample.iloc[0].astype(object).where(sample.iloc[0].notna(), None).to_dict()
    test_records = make_invalid_test_records(template)
    test_frame = pd.concat([sample, test_records], ignore_index=True)
    ok, bad, _ = validate_frame(test_frame)

    is_test = bad["record_id"].fillna("test6_missing_id").astype(str).str.startswith("test")
    detected = int(is_test.sum())
    false_positives = int((~is_test).sum())
    result = bad.copy()
    result.insert(0, "test_case", range(1, len(result) + 1))
    result.to_csv(config.VALIDATION_TEST_CSV, index=False, encoding="utf-8")

    print(f"[validator] validation test: {len(test_records)} invalid test records injected, {detected} detected, "
          f"{false_positives} real rows wrongly rejected", flush=True)
    for _, row in bad.iterrows():
        label = row["record_id"] if pd.notna(row["record_id"]) else "test6 (record_id missing)"
        print(f"[validator]   TEST {label}: {row['validation_errors'][:150]}", flush=True)
    return {
        "test_records_injected": int(len(test_records)),
        "test_records_detected": detected,
        "real_rows_wrongly_rejected": false_positives,
        "all_detected": detected == len(test_records) and false_positives == 0,
        "real_sample_rows": int(len(sample)),
    }


def run() -> int:
    if not config.CONSUMED_CSV.exists():
        print(f"[validator] ERROR {config.CONSUMED_CSV} not found - run the consumer first", flush=True)
        return 1
    frame = pd.read_csv(config.CONSUMED_CSV)
    total = len(frame)
    print(f"[validator] validating {total} records received from Kafka ({len(RULES)} rules)", flush=True)

    valid, invalid, failures = validate_frame(frame)
    valid.to_csv(config.VALID_CSV, index=False, encoding="utf-8")
    invalid.to_csv(config.INVALID_CSV, index=False, encoding="utf-8")

    errors_per_rule = {}
    if not failures.empty:
        errors_per_rule = failures.groupby("rule").size().rename("count").reset_index().to_dict("records")

    test_result = run_validation_test(valid)
    report = {
        "total_records": int(total),
        "valid_records": int(len(valid)),
        "invalid_records": int(len(invalid)),
        "handling_strategy": "invalid rows are quarantined in data/invalid_records.csv and excluded from further stages",
        "rules": [{"name": n, "description": d} for n, d in RULES],
        "failures_by_rule": errors_per_rule,
        "validation_test": test_result,
    }
    summary.write_json(config.VALIDATION_REPORT, report)
    summary.update("validation", {"total": int(total), "valid": int(len(valid)), "invalid": int(len(invalid)),
                                  "validation_test_all_detected": test_result.get("all_detected")})

    print(f"[validator] valid={len(valid)} invalid={len(invalid)} (quarantined in {config.INVALID_CSV.name})", flush=True)
    return 0 if len(valid) > 0 and test_result.get("all_detected", False) else 1


if __name__ == "__main__":
    sys.exit(run())
