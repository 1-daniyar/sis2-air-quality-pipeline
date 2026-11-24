"""Central configuration for the SIS2 air-quality pipeline.

No secrets are stored here: the Open-Meteo Air Quality API is free for
non-commercial use and needs no API key. Values can be overridden through
environment variables (see .env.example).
"""
import os
import sys
from pathlib import Path

# Never crash on characters (e.g. the micro sign) that a Windows console code page cannot print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
AGG_DIR = DATA_DIR / "aggregates"
FIGURES_DIR = PROJECT_ROOT / "figures"
LOGS_DIR = PROJECT_ROOT / "logs"
SQL_DIR = PROJECT_ROOT / "sql"
REPORT_DIR = PROJECT_ROOT / "report"

# ---------------------------------------------------------------- API ----
API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HOURLY_VARIABLES = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
    "european_aqi",
]
POLLUTANT_COLUMNS = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
]
NUMERIC_COLUMNS = POLLUTANT_COLUMNS + ["european_aqi"]
TIMEZONE = "Asia/Almaty"
START_DATE = os.getenv("AQ_START_DATE", "2026-11-10")
END_DATE = os.getenv("AQ_END_DATE", "2026-11-23")
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0
PAUSE_BETWEEN_REQUESTS_S = 1.0

CITIES = [
    {"key": "almaty", "name": "Almaty", "region": "Almaty City", "latitude": 43.238, "longitude": 76.945},
    {"key": "astana", "name": "Astana", "region": "Astana City", "latitude": 51.169, "longitude": 71.449},
    {"key": "shymkent", "name": "Shymkent", "region": "Shymkent City", "latitude": 42.317, "longitude": 69.590},
    {"key": "karaganda", "name": "Karaganda", "region": "Karaganda Region", "latitude": 49.806, "longitude": 73.085},
]
CITY_NAMES = [c["name"] for c in CITIES]
REGION_NAMES = sorted({c["region"] for c in CITIES})

# -------------------------------------------------------------- Kafka ----
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "air_quality_raw")
KAFKA_PARTITIONS = 3
KAFKA_REPLICATION_FACTOR = 1
CONSUMER_IDLE_TIMEOUT_S = 30     # stop if no message arrives for this long
CONSUMER_MAX_RUNTIME_S = 300     # hard upper limit for one consumer run

# -------------------------------------------------------------- Files ----
RAW_CSV = DATA_DIR / "raw_data.csv"
RAW_JSON = DATA_DIR / "raw_api_responses.json"
PRODUCER_STATS = DATA_DIR / "producer_stats.json"
CONSUMER_STATS = DATA_DIR / "consumer_stats.json"
CONSUMED_CSV = DATA_DIR / "consumed_data.csv"
VALID_CSV = DATA_DIR / "valid_records.csv"
INVALID_CSV = DATA_DIR / "invalid_records.csv"
VALIDATION_REPORT = DATA_DIR / "validation_report.json"
VALIDATION_TEST_CSV = DATA_DIR / "validation_test_results.csv"
CLEANED_CSV = DATA_DIR / "cleaned_data.csv"
CLEANING_REPORT = DATA_DIR / "cleaning_report.json"
DB_PATH = DATA_DIR / "output.db"
SQL_VERIFICATION_FILE = SQL_DIR / "verification_queries.sql"
SQL_VERIFICATION_RESULTS = DATA_DIR / "sql_verification_results.json"
IDEMPOTENCY_REPORT = DATA_DIR / "idempotency_report.json"
EDA_SUMMARY = DATA_DIR / "eda_summary.json"
VERIFICATION_REPORT = DATA_DIR / "verification_report.json"
PIPELINE_SUMMARY = DATA_DIR / "pipeline_summary.json"

# ------------------------------------------------- Domain thresholds ----
WHO_PM25_24H = 15.0   # ug/m3, WHO Air Quality Guidelines 2021 (24-hour mean)
WHO_PM10_24H = 45.0   # ug/m3, WHO Air Quality Guidelines 2021 (24-hour mean)
