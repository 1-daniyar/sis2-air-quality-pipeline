"""Helpers shared by the Kafka producer and consumer scripts."""
import json

import numpy as np

from src import config


def json_default(value):
    """Make numpy scalars JSON serialisable."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def serialize_value(record: dict) -> bytes:
    """dict -> JSON bytes. allow_nan=False guarantees strictly valid JSON (NaN must be None)."""
    return json.dumps(record, ensure_ascii=False, default=json_default, allow_nan=False).encode("utf-8")


def deserialize_value(raw: bytes) -> dict:
    """JSON bytes -> dict. Raises ValueError for anything that is not a JSON object."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("message value is not a JSON object")
    return data


def ensure_topic() -> None:
    """Create the topic if it does not exist yet (needs a running broker)."""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import KafkaError, TopicAlreadyExistsError

    admin = KafkaAdminClient(bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS, client_id="sis2-admin")
    try:
        if config.KAFKA_TOPIC in admin.list_topics():
            print(f"[kafka] topic '{config.KAFKA_TOPIC}' already exists", flush=True)
            return
        topic = NewTopic(
            name=config.KAFKA_TOPIC,
            num_partitions=config.KAFKA_PARTITIONS,
            replication_factor=config.KAFKA_REPLICATION_FACTOR,
        )
        admin.create_topics([topic])
        print(f"[kafka] topic '{config.KAFKA_TOPIC}' created "
              f"({config.KAFKA_PARTITIONS} partitions, replication factor {config.KAFKA_REPLICATION_FACTOR})", flush=True)
    except TopicAlreadyExistsError:
        print(f"[kafka] topic '{config.KAFKA_TOPIC}' already exists", flush=True)
    except KafkaError as exc:
        # The broker may still auto-create the topic on first use, so this is not fatal.
        print(f"[kafka] WARNING could not create topic explicitly: {exc!r}", flush=True)
    finally:
        admin.close()
