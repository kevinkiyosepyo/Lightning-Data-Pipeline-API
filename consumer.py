"""Storage consumer: Kafka -> PostgreSQL (PostGIS).

Consumes decoded strikes from `lightning.strikes` and writes them in batches
with a single multi-row INSERT. Offsets are committed only after the database
transaction commits, and the table has a natural-key uniqueness constraint
with ON CONFLICT DO NOTHING, so redelivered messages never produce duplicate
rows (at-least-once delivery + idempotent writes).
"""

import json
import logging
import os
import time

import psycopg2
from confluent_kafka import Consumer, KafkaError
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("consumer")

STRIKES_TOPIC = os.getenv("KAFKA_STRIKES_TOPIC", "lightning.strikes")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "200"))
BATCH_TIMEOUT_SECONDS = float(os.getenv("BATCH_TIMEOUT_SECONDS", "1.0"))

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS lightning_strikes (
    id BIGSERIAL PRIMARY KEY,
    strike_time BIGINT NOT NULL,
    strike_timestamp TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    altitude INTEGER,
    polarity INTEGER,
    mds INTEGER,
    mcg INTEGER,
    status INTEGER,
    region SMALLINT,
    station_count INTEGER,
    geom GEOGRAPHY(POINT, 4326) NOT NULL,
    inserted_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT valid_latitude CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT valid_longitude CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT unique_strike UNIQUE (strike_time, latitude, longitude)
);

CREATE INDEX IF NOT EXISTS idx_strike_timestamp ON lightning_strikes (strike_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_geom ON lightning_strikes USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_inserted_at ON lightning_strikes (inserted_at DESC);

CREATE TABLE IF NOT EXISTS ingestion_stats (
    id SERIAL PRIMARY KEY,
    total_received BIGINT DEFAULT 0,
    total_stored BIGINT DEFAULT 0,
    total_failed BIGINT DEFAULT 0,
    last_strike_time TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

INSERT INTO ingestion_stats (total_received, total_stored, total_failed)
SELECT 0, 0, 0
WHERE NOT EXISTS (SELECT 1 FROM ingestion_stats);
"""

INSERT_SQL = """
INSERT INTO lightning_strikes
    (strike_time, strike_timestamp, latitude, longitude, altitude,
     polarity, mds, mcg, status, region, station_count, geom)
VALUES %s
ON CONFLICT ON CONSTRAINT unique_strike DO NOTHING
"""

VALUE_TEMPLATE = (
    "(%(strike_time)s, %(strike_timestamp)s, %(latitude)s, %(longitude)s, %(altitude)s,"
    " %(polarity)s, %(mds)s, %(mcg)s, %(status)s, %(region)s, %(station_count)s,"
    " ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)::geography)"
)


def connect_db():
    max_retries = 10
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                database=os.getenv("POSTGRES_DB", "lightning"),
                user=os.getenv("POSTGRES_USER", "lightning_user"),
                password=os.getenv("POSTGRES_PASSWORD", "lightning_pass"),
                port=os.getenv("POSTGRES_PORT", "5432"),
            )
            logger.info("Connected to PostgreSQL")
            return conn
        except psycopg2.OperationalError:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"PostgreSQL not ready (attempt {attempt + 1}/{max_retries}), retrying in 5s")
            time.sleep(5)


def create_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "group.id": os.getenv("KAFKA_GROUP_ID", "lightning-storage"),
            "auto.offset.reset": "earliest",
            # Offsets are committed manually, after the DB transaction commits.
            "enable.auto.commit": False,
        }
    )


def parse_message(msg) -> dict | None:
    try:
        strike = json.loads(msg.value())
        strike.setdefault("altitude", None)
        strike.setdefault("polarity", None)
        strike.setdefault("mds", None)
        strike.setdefault("mcg", None)
        strike.setdefault("status", None)
        strike.setdefault("region", None)
        strike.setdefault("station_count", None)
        return strike
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Unparseable message skipped: {e}")
        return None


def flush_batch(conn, consumer, batch: list[dict]) -> int:
    """Insert a batch, update stats, then commit Kafka offsets. Returns rows stored."""
    failed = 0
    with conn.cursor() as cursor:
        execute_values(cursor, INSERT_SQL, batch, template=VALUE_TEMPLATE)
        stored = cursor.rowcount if cursor.rowcount >= 0 else 0
        cursor.execute(
            """
            UPDATE ingestion_stats SET
                total_received = total_received + %s,
                total_stored = total_stored + %s,
                total_failed = total_failed + %s,
                last_strike_time = GREATEST(coalesce(last_strike_time, 'epoch'::timestamptz), %s),
                updated_at = now()
            """,
            (len(batch), stored, failed, max(s["strike_timestamp"] for s in batch)),
        )
    conn.commit()
    consumer.commit(asynchronous=False)
    return stored


def main():
    logger.info("Storage consumer starting")
    conn = connect_db()
    with conn.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)
    conn.commit()
    logger.info("Schema verified")

    consumer = create_consumer()
    consumer.subscribe([STRIKES_TOPIC])

    batch: list[dict] = []
    last_flush = time.monotonic()
    total_stored = 0

    try:
        while True:
            msg = consumer.poll(timeout=0.5)
            if msg is not None:
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        logger.error(f"Kafka error: {msg.error()}")
                else:
                    strike = parse_message(msg)
                    if strike is not None:
                        batch.append(strike)

            batch_expired = (time.monotonic() - last_flush) >= BATCH_TIMEOUT_SECONDS
            if batch and (len(batch) >= BATCH_SIZE or batch_expired):
                stored = flush_batch(conn, consumer, batch)
                total_stored += stored
                logger.info(f"Flushed batch: {len(batch)} consumed, {stored} stored (total {total_stored})")
                batch = []
                last_flush = time.monotonic()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        if batch:
            flush_batch(conn, consumer, batch)
        consumer.close()
        conn.close()


if __name__ == "__main__":
    main()
