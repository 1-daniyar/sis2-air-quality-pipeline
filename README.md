# SIS2 - Air-quality data streaming and validation pipeline (Kafka + Pandera + SQLite)

Course: Data Collection & Preparation. Author: Daniyar Ospanov (KBTU).

**Research question:** how does air quality (PM2.5, PM10, NO2, SO2, CO, O3, European AQI) compare between
Almaty, Astana, Shymkent and Karaganda, and when during the day is it worst?

```
Open-Meteo API -> raw_data.csv -> Kafka Producer -> Kafka topic -> Kafka Consumer -> Pandera validation
   -> pandas/NumPy cleaning -> SQLite3 (loaded twice = idempotency test) -> EDA + figures -> verification -> PDF report
```

## 1. Requirements

* Python 3.10 - 3.13
* Docker Desktop (runs the real Apache Kafka broker)
* Internet access (Open-Meteo API, no API key needed; no secrets are stored in this repository)

## 2. Installation

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Start the Kafka broker

```bash
docker compose up -d          # starts apache/kafka:3.9.0 on localhost:9092 (KRaft, single node)
docker ps                     # container "sis2-kafka" must be "Up"
```

The topic `air_quality_raw` (3 partitions, replication factor 1) is created automatically by the producer.
Optional manual check: `docker exec sis2-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list`

Configuration (defaults in `src/config.py`, override with environment variables, see `.env.example`):
`KAFKA_BOOTSTRAP_SERVERS=localhost:9092`, `KAFKA_TOPIC=air_quality_raw`,
`AQ_START_DATE=2026-11-10`, `AQ_END_DATE=2026-11-23`.

## 4. Run the complete pipeline

```bash
python main.py --fresh        # all stages in order; --fresh removes old outputs first
python main.py --report       # same, and builds report/project_report.pdf at the end
```

Run the stages one by one (execution order):

```bash
python -m src.api_client      # 1. collect API data      -> data/raw_data.csv, data/raw_api_responses.json
python -m src.producer        # 2. Kafka producer        -> data/producer_stats.json
python -m src.consumer        # 3. Kafka consumer        -> data/consumed_data.csv, data/consumer_stats.json
python -m src.validator       # 4. Pandera validation    -> data/valid_records.csv, data/invalid_records.csv
python -m src.cleaner         # 5. cleaning/transform    -> data/cleaned_data.csv, data/aggregates/
python -m src.loader --fresh  # 6. SQLite3 (twice)       -> data/output.db, data/idempotency_report.json
python -m src.analysis        # 7. EDA + figures         -> figures/*.png, data/eda_summary.json
python -m src.verification    # 8. verification          -> data/verification_report.json
```

The consumer terminates when it has received all messages of the producer run, or after 30 s without new
messages, or after 300 s in total. Console output of each stage is saved in `logs/`.

### Producer and consumer in two terminals (demonstration)

```bash
# terminal 1                       # terminal 2
python -m src.api_client           python -m src.consumer      # after the producer has finished
python -m src.producer
```

## 5. Build the report

1. Put your name/group number in `report/report_config.json`.
2. (Optional) add screenshots (`docker ps`, producer/consumer terminals) as PNG files to `report/screenshots/`.
3. `python report/build_report.py` -> `report/project_report.pdf` (all numbers, tables and figures come from the files produced by the pipeline run).

## 6. Expected output and how to verify success

* `python main.py` ends with `Pipeline finished successfully.` and every line `[verify] PASS ...`.
* Producer: `published(confirmed)=N`; consumer: `consumed=N expected=N` (N = 4 cities x 14 days x 24 h = 1344 for the default period).
* Validator: `validation test: 6 invalid test records injected, 6 detected`.
* Loader: `load #1 inserted=...`, `load #2 inserted=0`, `idempotent: True`.
* `figures/` contains six PNG files. `data/verification_report.json` lists all counts and checks.
* Database check by hand: `sqlite3 data/output.db "SELECT COUNT(*) FROM measurements;"` (queries: `sql/verification_queries.sql`).

Stop Kafka: `docker compose down` (add `-v` to wipe the topic).

## 7. Repository layout

```
main.py                  pipeline orchestrator          docker-compose.yml   Kafka broker
src/api_client.py        API collection                 src/producer.py      Kafka producer
src/consumer.py          Kafka consumer                 src/validator.py     Pandera schema + validation test
src/cleaner.py           pandas/NumPy cleaning          src/loader.py        SQLite3 schema + idempotent load
src/analysis.py          EDA + figures                  src/verification.py  count and consistency checks
sql/verification_queries.sql   docs/data_dictionary.md  report/build_report.py   PDF report generator
data/  figures/  logs/   generated outputs (committed after a successful run)
```

## 8. Troubleshooting

* `cannot connect to Kafka broker` -> Docker Desktop is not running or `docker compose up -d` was not executed.
* Open-Meteo `HTTP 429` -> wait a minute and rerun (the client already retries with back-off).
* Re-running the pipeline is safe: each run uses its own `run_id`, so old messages in the topic are skipped, and the database ignores existing rows.

Data: Open-Meteo.com Air Quality API (CAMS ENSEMBLE / CAMS global data, Copernicus Atmosphere Monitoring Service), non-commercial use.
