"""Stage 2 - Kafka Producer: publish the collected API records to a Kafka topic.

Run:  python -m src.producer

Reads data/raw_data.csv (output of src.api_client), serialises every record to
JSON and sends it to the topic. Every message carries a `run_id` header so the
consumer can tell this run's messages apart from older runs in the same topic.
"""
import sys
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from src import config, summary
from src.kafka_utils import ensure_topic, serialize_value


def load_records(path=config.RAW_CSV) -> list[dict]:
    frame = pd.read_csv(path)
    # NaN is not valid JSON -> use None (JSON null) for missing values
    frame = frame.astype(object).where(frame.notna(), None)
    return frame.to_dict(orient="records")


def create_producer():
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
        client_id="sis2-producer",
        acks="all",                     # wait until the broker confirmed the write
        retries=5,
        linger_ms=20,
        max_block_ms=15000,             # fail fast if the broker is unreachable
        key_serializer=lambda key: key.encode("utf-8"),
        value_serializer=serialize_value,
    )


def run() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc)

    if not config.RAW_CSV.exists():
        print(f"[producer] ERROR {config.RAW_CSV} not found - run `python -m src.api_client` first", flush=True)
        return 1
    records = load_records()
    print(f"[producer] run_id={run_id}; {len(records)} records loaded from {config.RAW_CSV.name}", flush=True)
    print(f"[producer] broker={config.KAFKA_BOOTSTRAP_SERVERS} topic={config.KAFKA_TOPIC}", flush=True)

    try:
        ensure_topic()
        producer = create_producer()
    except Exception as exc:  # NoBrokersAvailable, DNS/socket errors ...
        print(f"[producer] ERROR cannot connect to Kafka broker at {config.KAFKA_BOOTSTRAP_SERVERS}: {exc!r}", flush=True)
        print("[producer] Is the broker running?  ->  docker compose up -d", flush=True)
        return 2

    lock = threading.Lock()
    delivered = Counter()      # partition -> confirmed messages
    errors = []

    def on_success(metadata):
        with lock:
            delivered[metadata.partition] += 1

    def on_error(exc):
        with lock:
            errors.append(repr(exc))

    sent = 0
    for record in records:
        try:
            future = producer.send(
                config.KAFKA_TOPIC,
                key=record["city"].lower(),          # same city -> same partition
                value=record,
                headers=[("run_id", run_id.encode("utf-8"))],
            )
            future.add_callback(on_success)
            future.add_errback(on_error)
            sent += 1
        except Exception as exc:  # serialisation or buffer/metadata timeout
            with lock:
                errors.append(f"{record.get('record_id')}: {exc!r}")

    try:
        producer.flush(timeout=60)
    except Exception as exc:
        with lock:
            errors.append(f"flush: {exc!r}")
    finally:
        producer.close(timeout=10)

    published = int(sum(delivered.values()))
    stats = {
        "run_id": run_id,
        "bootstrap_servers": config.KAFKA_BOOTSTRAP_SERVERS,
        "topic": config.KAFKA_TOPIC,
        "partitions": config.KAFKA_PARTITIONS,
        "records_loaded": len(records),
        "sent": sent,
        "published": published,
        "failed": len(records) - published,
        "published_per_partition": {str(k): int(v) for k, v in sorted(delivered.items())},
        "errors_sample": errors[:5],
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    summary.write_json(config.PRODUCER_STATS, stats)
    summary.update("producer", {k: stats[k] for k in ("run_id", "topic", "records_loaded", "sent", "published", "failed")})

    print(f"[producer] sent={sent} published(confirmed)={published} failed={stats['failed']}", flush=True)
    print(f"[producer] messages per partition: {stats['published_per_partition']}", flush=True)
    for message in errors[:5]:
        print(f"[producer] ERROR {message}", flush=True)
    return 0 if published > 0 and stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
