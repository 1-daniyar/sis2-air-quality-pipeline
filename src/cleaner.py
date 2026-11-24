"""Stage 5 - cleaning and transformation with pandas and NumPy.

Run:  python -m src.cleaner

Input : data/valid_records.csv   (output of the validator)
Output: data/cleaned_data.csv    (loaded into SQLite in the next stage)
        data/aggregates/*.csv    (GroupBy / pivot / melt results)
        data/cleaning_report.json
"""
import sys

import numpy as np
import pandas as pd

from src import config, summary

MAX_INTERPOLATION_GAP_H = 3   # fill at most 3 consecutive missing hours
EXTREME_PERCENTILE = 90       # "high pollution" = PM2.5 at or above the 90th percentile

RATIONALE = {
    "test_records": "Test records created for the validation demo must never reach the database.",
    "missing_values": "Short gaps in hourly model data are filled by linear interpolation inside each city so that daily and hourly statistics are not biased; rows that still have no PM10/PM2.5 value carry no information for the analysis and are dropped.",
    "duplicates": "Repeated rows would be counted twice in every aggregate and would violate the database primary key.",
    "dtypes": "CSV files store everything as text; real datetime and numeric types are required for sorting, time features, SQL storage and plotting.",
    "text": "Trimming whitespace and unifying case prevents the same city from appearing under several spellings in GroupBy results.",
    "numpy": "np.select / np.where / np.log1p / np.divide create analysis features (AQI category, WHO exceedance flag, log-PM2.5, PM ratio, z-score) in a fast vectorised way.",
    "filtering": "Combining several conditions isolates the most polluted evening and night hours, which are the hours of interest for the research question.",
    "groupby": "Aggregation by city, day and part of the day answers the comparison question between the four cities.",
    "reshape": "pivot_table gives a city x hour matrix (heat map), melt converts the pollutant columns to long format, unstack builds a day x city table.",
}


def missing_report(frame: pd.DataFrame) -> dict:
    nulls = frame[config.NUMERIC_COLUMNS].isna().sum()
    return {column: int(count) for column, count in nulls.items()}


def run() -> int:
    if not config.VALID_CSV.exists():
        print(f"[cleaner] ERROR {config.VALID_CSV} not found - run the validator first", flush=True)
        return 1
    config.AGG_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(config.VALID_CSV)
    report = {"rationale": RATIONALE, "input_records": int(len(df))}
    print(f"[cleaner] input records (valid): {len(df)}", flush=True)

    # 1. defensive removal of test records --------------------------------------------------
    is_test = df["record_id"].astype(str).str.lower().str.startswith("test")
    report["test_records_removed"] = int(is_test.sum())
    df = df[~is_test].copy()

    # 2. data types -------------------------------------------------------------------------
    report["dtypes_before"] = {c: str(t) for c, t in df.dtypes.items()}
    df["time"] = pd.to_datetime(df["time"], format="%Y-%m-%dT%H:%M")
    df["collected_at"] = pd.to_datetime(df["collected_at"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for column in config.NUMERIC_COLUMNS + ["latitude", "longitude"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")

    # 3. text normalisation -----------------------------------------------------------------
    for column in ("city", "region"):
        df[column] = df[column].astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.title()
    df["record_id"] = df["record_id"].astype(str).str.strip().str.lower()

    # 4. duplicates -------------------------------------------------------------------------
    exact_duplicates = int(df.duplicated().sum())
    id_duplicates = int(df.duplicated(subset=["record_id"]).sum())
    key_duplicates = int(df.duplicated(subset=["city", "time"]).sum())
    df = df.drop_duplicates(subset=["record_id"], keep="first").drop_duplicates(subset=["city", "time"], keep="first")
    report["duplicates"] = {"exact_duplicate_rows": exact_duplicates, "duplicate_record_id": id_duplicates,
                            "duplicate_city_time": key_duplicates, "rows_after_removal": int(len(df))}

    # 5. missing values ---------------------------------------------------------------------
    df = df.sort_values(["city", "time"]).reset_index(drop=True)
    nulls_before = missing_report(df)
    for column in config.NUMERIC_COLUMNS:
        df[column] = df.groupby("city")[column].transform(
            lambda s: s.interpolate(method="linear", limit=MAX_INTERPOLATION_GAP_H, limit_area="inside")
        )
    nulls_after_interpolation = missing_report(df)
    rows_before_drop = len(df)
    df = df.dropna(subset=["pm10", "pm2_5"]).reset_index(drop=True)
    report["missing_values"] = {
        "nulls_before": nulls_before,
        "nulls_after_interpolation": nulls_after_interpolation,
        "values_filled": int(sum(nulls_before.values()) - sum(nulls_after_interpolation.values())),
        "rows_dropped_without_pm": int(rows_before_drop - len(df)),
        "max_interpolation_gap_hours": MAX_INTERPOLATION_GAP_H,
    }

    # 6. NumPy feature engineering ----------------------------------------------------------
    pm25 = df["pm2_5"].to_numpy()
    pm10 = df["pm10"].to_numpy()
    aqi = df["european_aqi"].to_numpy()
    hour = df["time"].dt.hour.to_numpy()

    df["hour"] = hour
    df["date"] = df["time"].dt.strftime("%Y-%m-%d")
    df["is_weekend"] = np.where(df["time"].dt.dayofweek.to_numpy() >= 5, 1, 0)
    df["day_period"] = np.select(
        [hour < 6, hour < 12, hour < 18], ["night", "morning", "afternoon"], default="evening")
    # European AQI bands: 0-20 good, 20-40 fair, 40-60 moderate, 60-80 poor, 80-100 very poor, >100 extremely poor
    df["aqi_category"] = np.select(
        [aqi <= 20, aqi <= 40, aqi <= 60, aqi <= 80, aqi <= 100, aqi > 100],
        ["Good", "Fair", "Moderate", "Poor", "Very poor", "Extremely poor"], default="Unknown")
    df["pm25_exceeds_who"] = np.where(pm25 > config.WHO_PM25_24H, 1, 0)
    df["pm2_5_log"] = np.log1p(pm25)
    df["pm_ratio"] = np.divide(pm25, pm10, out=np.full(len(df), np.nan), where=pm10 > 0)
    df["pm2_5_zscore"] = df.groupby("city")["pm2_5"].transform(
        lambda s: (s.to_numpy() - np.mean(s.to_numpy())) / (np.std(s.to_numpy()) or 1.0))
    q1, q3 = np.percentile(pm25, [25, 75])
    upper_fence = q3 + 1.5 * (q3 - q1)
    report["numpy"] = {
        "pm2_5_mean": float(np.mean(pm25)), "pm2_5_median": float(np.median(pm25)),
        "pm2_5_std": float(np.std(pm25)), "pm2_5_p90": float(np.percentile(pm25, EXTREME_PERCENTILE)),
        "iqr_upper_fence": float(upper_fence), "iqr_outliers": int(np.sum(pm25 > upper_fence)),
        "features_created": ["hour", "date", "is_weekend", "day_period", "aqi_category", "pm25_exceeds_who",
                             "pm2_5_log", "pm_ratio", "pm2_5_zscore"],
    }

    # 7. filtering with several conditions -------------------------------------------------
    p90 = float(np.percentile(pm25, EXTREME_PERCENTILE))
    high_events = df[(df["pm2_5"] >= p90) & df["day_period"].isin(["evening", "night"]) & df["european_aqi"].notna()]
    high_events.to_csv(config.AGG_DIR / "high_pollution_events.csv", index=False, encoding="utf-8")
    report["filtering"] = {
        "condition": f"pm2_5 >= {p90:.1f} (90th percentile) AND day_period in (evening, night) AND european_aqi is not null",
        "rows_matching": int(len(high_events)),
        "share_of_dataset_percent": round(100 * len(high_events) / len(df), 2),
        "rows_per_city": high_events["city"].value_counts().to_dict(),
    }

    # 8. GroupBy + aggregation ------------------------------------------------------------
    city_summary = df.groupby("city").agg(
        records=("record_id", "count"),
        pm2_5_mean=("pm2_5", "mean"), pm2_5_median=("pm2_5", "median"),
        pm2_5_std=("pm2_5", "std"), pm2_5_max=("pm2_5", "max"),
        pm10_mean=("pm10", "mean"), aqi_mean=("european_aqi", "mean"),
        who_exceedance_share=("pm25_exceeds_who", "mean"),
    ).round(2).reset_index()
    daily_city = df.groupby(["city", "date"], as_index=False).agg(
        pm2_5_mean=("pm2_5", "mean"), pm10_mean=("pm10", "mean"), aqi_mean=("european_aqi", "mean")).round(2)
    period_city = df.groupby(["city", "day_period"], as_index=False)["pm2_5"].agg(["mean", "max"]).round(2)
    city_summary.to_csv(config.AGG_DIR / "city_summary.csv", index=False, encoding="utf-8")
    daily_city.to_csv(config.AGG_DIR / "daily_city.csv", index=False, encoding="utf-8")
    period_city.to_csv(config.AGG_DIR / "period_city.csv", index=False, encoding="utf-8")
    report["groupby"] = {"city_summary": city_summary.to_dict("records"),
                         "period_city_rows": int(len(period_city)), "daily_city_rows": int(len(daily_city))}

    # 9. reshaping: pivot_table, unstack, melt ---------------------------------------------
    pivot_city_hour = df.pivot_table(index="city", columns="hour", values="pm2_5", aggfunc="mean").round(2)
    daily_wide = df.groupby(["date", "city"])["pm2_5"].mean().unstack("city").round(2)
    long_format = df.melt(id_vars=["record_id", "city", "time"], value_vars=config.POLLUTANT_COLUMNS,
                          var_name="pollutant", value_name="concentration")
    pivot_city_hour.to_csv(config.AGG_DIR / "pivot_city_hour.csv", encoding="utf-8")
    daily_wide.to_csv(config.AGG_DIR / "daily_pm25_wide.csv", encoding="utf-8")
    long_format.to_csv(config.AGG_DIR / "pollutants_long.csv", index=False, encoding="utf-8")
    report["reshaping"] = {
        "pivot_table_shape": list(pivot_city_hour.shape), "unstack_shape": list(daily_wide.shape),
        "melt_shape": list(long_format.shape), "wide_columns_melted": config.POLLUTANT_COLUMNS,
    }

    # 10. save ------------------------------------------------------------------------------
    output_columns = ["record_id", "city", "region", "latitude", "longitude", "time", "date", "hour", "day_period",
                      "is_weekend"] + config.NUMERIC_COLUMNS + ["aqi_category", "pm25_exceeds_who", "pm2_5_log",
                                                               "pm_ratio", "pm2_5_zscore", "collected_at"]
    cleaned = df[output_columns].copy()
    cleaned["time"] = cleaned["time"].dt.strftime("%Y-%m-%dT%H:%M")
    cleaned.to_csv(config.CLEANED_CSV, index=False, encoding="utf-8")
    report["dtypes_after"] = {c: str(t) for c, t in df[output_columns].dtypes.items()}
    report["records_after_cleaning"] = int(len(cleaned))
    summary.write_json(config.CLEANING_REPORT, report)
    summary.update("cleaning", {"input": report["input_records"], "records_after_cleaning": int(len(cleaned)),
                                "values_filled": report["missing_values"]["values_filled"],
                                "rows_dropped_without_pm": report["missing_values"]["rows_dropped_without_pm"],
                                "duplicates_removed": int(report["input_records"] - report["test_records_removed"] - report["duplicates"]["rows_after_removal"])})

    print(f"[cleaner] missing values filled by interpolation: {report['missing_values']['values_filled']}; "
          f"rows dropped (no PM): {report['missing_values']['rows_dropped_without_pm']}", flush=True)
    print(f"[cleaner] duplicates removed: {report['duplicates']['duplicate_record_id'] + report['duplicates']['duplicate_city_time']}", flush=True)
    print(f"[cleaner] high-pollution events (filter): {report['filtering']['rows_matching']}", flush=True)
    print(f"[cleaner] records after cleaning: {len(cleaned)} -> {config.CLEANED_CSV.name}", flush=True)
    return 0 if len(cleaned) > 0 else 1


if __name__ == "__main__":
    sys.exit(run())
