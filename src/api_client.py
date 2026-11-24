"""Stage 1 - collect hourly air-quality data from the Open-Meteo Air Quality API.

Run:  python -m src.api_client

For every city one GET request is sent. The hourly arrays of the response are
flattened into one record per (city, hour). The untouched API responses are
preserved in data/raw_api_responses.json and the flattened records in
data/raw_data.csv *before* any validation, cleaning or transformation.
"""
import json
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from src import config, summary


class ApiError(Exception):
    """Raised when the API cannot deliver a usable response for one city."""


def build_params(city: dict, start_date: str, end_date: str) -> dict:
    return {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "hourly": ",".join(config.HOURLY_VARIABLES),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": config.TIMEZONE,
    }


def fetch_city(session: requests.Session, city: dict, start_date: str, end_date: str) -> tuple[dict, list[dict]]:
    """Request one city with retries. Returns (payload, request_log)."""
    params = build_params(city, start_date, end_date)
    request_log = []
    last_error = "unknown error"

    for attempt in range(1, config.MAX_RETRIES + 1):
        entry = {"city": city["name"], "attempt": attempt}
        try:
            response = session.get(config.API_URL, params=params, timeout=config.REQUEST_TIMEOUT_S)
            entry["status_code"] = response.status_code

            if response.status_code == 200:
                payload = response.json()
                _check_payload(payload)
                entry["result"] = "ok"
                request_log.append(entry)
                return payload, request_log

            # Open-Meteo answers 400 with {"error": true, "reason": "..."}
            try:
                reason = response.json().get("reason", response.text[:200])
            except ValueError:
                reason = response.text[:200]
            last_error = f"HTTP {response.status_code}: {reason}"
            entry["result"] = last_error
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:  # 4xx (bad request) will not get better by retrying
                request_log.append(entry)
                raise ApiError(last_error)

        except (requests.exceptions.RequestException, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            entry["result"] = last_error

        request_log.append(entry)
        if attempt < config.MAX_RETRIES:
            wait = config.RETRY_BACKOFF_S * 2 ** (attempt - 1)
            print(f"[api] WARNING {city['name']}: attempt {attempt} failed ({last_error}); retry in {wait:.0f}s", flush=True)
            time.sleep(wait)

    raise ApiError(f"{city['name']}: all {config.MAX_RETRIES} attempts failed ({last_error})")


def _check_payload(payload: dict) -> None:
    """Make sure the JSON has the structure we rely on."""
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError(f"API reported an error: {payload.get('reason') if isinstance(payload, dict) else payload}")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise ValueError("response has no 'hourly.time' array")
    n_times = len(hourly["time"])
    for variable in config.HOURLY_VARIABLES:
        if variable not in hourly:
            raise ValueError(f"response is missing hourly variable '{variable}'")
        if len(hourly[variable]) != n_times:
            raise ValueError(f"hourly variable '{variable}' has a different length than 'time'")


def flatten_payload(city: dict, payload: dict, collected_at: str) -> list[dict]:
    """Turn the column-oriented hourly arrays into one record per hour."""
    hourly = payload["hourly"]
    records = []
    for index, timestamp in enumerate(hourly["time"]):
        record = {
            "record_id": f"{city['key']}_{timestamp}",
            "city": city["name"],
            "region": city["region"],
            "latitude": payload.get("latitude", city["latitude"]),
            "longitude": payload.get("longitude", city["longitude"]),
            "time": timestamp,
        }
        for variable in config.HOURLY_VARIABLES:
            record[variable] = hourly[variable][index]  # may be None (missing)
        record["collected_at"] = collected_at
        records.append(record)
    return records


def collect(start_date: str = config.START_DATE, end_date: str = config.END_DATE) -> int:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_records, raw_payloads, request_log, failed_cities = [], [], [], []

    print(f"[api] endpoint: {config.API_URL}", flush=True)
    print(f"[api] period: {start_date} .. {end_date}, cities: {', '.join(config.CITY_NAMES)}", flush=True)

    with requests.Session() as session:
        for number, city in enumerate(config.CITIES):
            if number:
                time.sleep(config.PAUSE_BETWEEN_REQUESTS_S)  # be polite to the free API
            try:
                payload, log = fetch_city(session, city, start_date, end_date)
            except ApiError as exc:
                print(f"[api] ERROR {exc}", flush=True)
                failed_cities.append({"city": city["name"], "error": str(exc)})
                request_log.append({"city": city["name"], "result": str(exc)})
                continue
            request_log.extend(log)
            records = flatten_payload(city, payload, collected_at)
            raw_payloads.append({"city": city["name"], "request_params": build_params(city, start_date, end_date), "response": payload})
            all_records.extend(records)
            print(f"[api] {city['name']}: {len(records)} records", flush=True)

    if not all_records:
        print("[api] ERROR no data collected - check your internet connection", flush=True)
        return 1

    frame = pd.DataFrame(all_records)
    frame.to_csv(config.RAW_CSV, index=False, encoding="utf-8")
    config.RAW_JSON.write_text(json.dumps(raw_payloads, ensure_ascii=False), encoding="utf-8")

    per_city = frame.groupby("city").size().to_dict()
    summary.update("collection", {
        "endpoint": config.API_URL,
        "start_date": start_date,
        "end_date": end_date,
        "collected_records": int(len(frame)),
        "records_per_city": {k: int(v) for k, v in per_city.items()},
        "failed_cities": failed_cities,
        "requests": request_log,
        "raw_csv": str(config.RAW_CSV.relative_to(config.PROJECT_ROOT)).replace("\\", "/"),
    })
    print(f"[api] collected records: {len(frame)} -> {config.RAW_CSV.name}", flush=True)
    if failed_cities:
        print(f"[api] WARNING {len(failed_cities)} city request(s) failed", flush=True)
    if len(frame) < 200:
        print("[api] WARNING fewer than 200 records collected (assignment minimum is 200)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(collect())
