"""Stage 3 - Kafka Consumer: read this run's messages back from the topic.

Run:  python -m src.consumer

Termination condition (the pipeline must be able to finish):
  1. the expected number of messages (written by the producer into
     data/producer_stats.json) has been consumed, OR
  2. no new message arrived for CONSUMER_IDLE_TIMEOUT_S seconds, OR
  3. the hard limit CONSUMER_MAX_RUNTIME_S has been reached.
Messages that belong to other runs (different `run_id` header) are skipped.
"""
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from src import config, summary
from src.kafka_utils import deserialize_value


def create_consumer(group_id: str):
    from kafka import KafkaConsumer

    return KafkaConsumer(
        config.KAFKA_TOPIC,
        bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
        client_id="sis2-consumer",
        group_id=group_id,
        auto_offset_reset="earliest",   # read the topic from the beginning
        enable_auto_commit=True,
        # raw bytes are deserialised manually so that a broken message can be counted, not crash the loop
        value_deserializer=None,
    )


def header_value(headers, name: str):
    for key, value in headers or []:
        if key == name:
            return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value
    return None


def run() -> int:
    if not config.PRODUCER_STATS.exists():
        print("[consumer] ERROR data/producer_stats.json not found - run the producer first", flush=True)
        return 1
    producer_stats = json.loads(config.PRODUCER_STATS.read_text(encoding="utf-8"))
    run_id = producer_stats["run_id"]
    expected = int(producer_stats["published"])
    group_id = f"sis2-consumer-{run_id}"   # fresh group -> always starts from the beginning
    print(f"[consumer] run_id={run_id} expecting {expected} messages from topic '{config.KAFKA_TOPIC}'", flush=True)

    try:
        consumer = create_consumer(group_id)
    except Exception as exc:
        print(f"[consumer] ERROR cannot connect to Kafka broker at {config.KAFKA_BOOTSTRAP_SERVERS}: {exc!r}", flush=True)
        return 2

    records, per_partition = [], Counter()
    skipped_other_runs = failed_deserialization = invalid_messages = 0
    first_messages = []
    started = last_activity = time.monotonic()
    stop_reason = "unknown"

    try:
        while True:
            now = time.monotonic()
            if len(records) >= expected:
                stop_reason = "expected message count reached"
                break
            if now - last_activity > config.CONSUMER_IDLE_TIMEOUT_S:
                stop_reason = f"idle timeout ({config.CONSUMER_IDLE_TIMEOUT_S}s without new messages)"
                break
            if now - started > config.CONSUMER_MAX_RUNTIME_S:
                stop_reason = f"max runtime ({config.CONSUMER_MAX_RUNTIME_S}s) reached"
                break

            batch = consumer.poll(timeout_ms=1000, max_records=500)
            for _partition, messages in batch.items():
                for message in messages:
                    last_activity = time.monotonic()
                    if header_value(message.headers, "run_id") != run_id:
                        skipped_other_runs += 1
                        continue
                    try:
                        record = deserialize_value(message.value)
                    except (UnicodeDecodeError, ValueError) as exc:   # JSONDecodeError is a ValueError
                        failed_deserialization += 1
                        print(f"[consumer] WARNING bad message p{message.partition}@{message.offset}: {exc!r}", flush=True)
                        continue
                    if "record_id" not in record:
                        invalid_messages += 1
                        continue
                    records.append(record)
                    per_partition[message.partition] += 1
                    if len(first_messages) < 3:
                        first_messages.append(f"partition={message.partition} offset={message.offset} record_id={record['record_id']}")
    except Exception as exc:
        print(f"[consumer] ERROR while consuming: {exc!r}", flush=True)
        stop_reason = f"error: {exc!r}"
    finally:
        consumer.close()

    pd.DataFrame(records).to_csv(config.CONSUMED_CSV, index=False, encoding="utf-8")
    stats = {
        "run_id": run_id,
        "group_id": group_id,
        "expected": expected,
        "consumed": len(records),
        "skipped_other_runs": skipped_other_runs,
        "failed_deserialization": failed_deserialization,
        "invalid_messages": invalid_messages,
        "consumed_per_partition": {str(k): int(v) for k, v in sorted(per_partition.items())},
        "stop_reason": stop_reason,
        "duration_s": round(time.monotonic() - started, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    summary.write_json(config.CONSUMER_STATS, stats)
    summary.update("consumer", {k: stats[k] for k in ("run_id", "expected", "consumed", "skipped_other_runs",
                                                      "failed_deserialization", "stop_reason")})

    for line in first_messages:
        print(f"[consumer] received: {line}", flush=True)
    print(f"[consumer] consumed={len(records)} expected={expected} skipped_other_runs={skipped_other_runs} "
          f"bad_json={failed_deserialization}", flush=True)
    print(f"[consumer] messages per partition: {stats['consumed_per_partition']}", flush=True)
    print(f"[consumer] stopped: {stop_reason}; saved -> {config.CONSUMED_CSV.name}", flush=True)
    return 0 if len(records) == expected and len(records) > 0 else 1


if __name__ == "__main__":
    sys.exit(run())
