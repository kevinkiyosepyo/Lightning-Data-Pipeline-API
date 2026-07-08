"""Ingestion service: Blitzortung WebSocket -> LZW decode -> Kafka.

Connects to the Blitzortung live feed, decompresses each frame with the LZW
decoder, validates it, and publishes the structured strike to the
`lightning.strikes` topic. Frames that fail to decode or validate are
published raw to `lightning.strikes.dlq` for later inspection instead of
being silently dropped.
"""

import datetime
import json
import logging
import os
import time

import websocket
from confluent_kafka import Producer

from lzw import lzw_decode

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ingest")

WEBSOCKET_URLS = [
    "wss://ws1.blitzortung.org/",
    "wss://ws7.blitzortung.org/",
    "wss://ws8.blitzortung.org/",
]
STRIKES_TOPIC = os.getenv("KAFKA_STRIKES_TOPIC", "lightning.strikes")
DLQ_TOPIC = os.getenv("KAFKA_DLQ_TOPIC", "lightning.strikes.dlq")


def normalize_epoch(ts: int) -> datetime.datetime:
    """Convert an epoch of unknown precision (s/ms/us/ns) to a UTC datetime."""
    if ts > 10**17:
        ts_seconds = ts / 1_000_000_000
    elif ts > 10**14:
        ts_seconds = ts / 1_000_000
    elif ts > 10**11:
        ts_seconds = ts / 1_000
    else:
        ts_seconds = ts
    return datetime.datetime.fromtimestamp(ts_seconds, tz=datetime.UTC)


class StrikeProducer:
    """Decodes frames and publishes them to Kafka."""

    def __init__(self):
        self.producer = Producer(
            {
                "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
                "linger.ms": 50,
                "compression.type": "lz4",
            }
        )
        self.received = 0
        self.published = 0
        self.dead_lettered = 0
        self.delivery_failures = 0

    def _on_delivery(self, err, msg):
        if err is not None:
            self.delivery_failures += 1
            logger.error(f"Kafka delivery failed: {err}")

    def decode_frame(self, frame: str) -> dict | None:
        """LZW-decompress and parse one WebSocket frame into a strike record."""
        decoded = lzw_decode(frame)
        raw = json.loads(decoded)

        lat = raw["lat"]
        lon = raw["lon"]
        if abs(lat) > 90 or abs(lon) > 180:
            raise ValueError(f"coordinates out of range: {lat}, {lon}")

        strike_time = int(raw["time"])
        return {
            "strike_time": strike_time,
            "strike_timestamp": normalize_epoch(strike_time).isoformat(),
            "latitude": lat,
            "longitude": lon,
            "altitude": raw.get("alt"),
            "polarity": raw.get("pol"),
            "mds": raw.get("mds"),
            "mcg": raw.get("mcg"),
            "status": raw.get("status"),
            "region": raw.get("region"),
            # Full station list is large; keep the count as a signal-quality metric.
            "station_count": len(raw.get("sig", [])),
        }

    def handle_frame(self, frame: str):
        self.received += 1
        try:
            strike = self.decode_frame(frame)
            self.producer.produce(
                STRIKES_TOPIC,
                value=json.dumps(strike).encode(),
                on_delivery=self._on_delivery,
            )
            self.published += 1
        except Exception as e:
            self.dead_lettered += 1
            self.producer.produce(
                DLQ_TOPIC,
                value=frame.encode("utf-8", errors="replace"),
                headers={"error": f"{type(e).__name__}: {e}"[:500]},
            )
            logger.warning(f"Frame dead-lettered: {type(e).__name__}: {e}")

        self.producer.poll(0)
        if self.received % 100 == 0:
            self.log_stats()

    def log_stats(self):
        rate = (self.published / self.received * 100) if self.received else 0.0
        logger.info(
            f"received={self.received} published={self.published} "
            f"dlq={self.dead_lettered} delivery_failures={self.delivery_failures} "
            f"decode_success={rate:.2f}%"
        )

    def flush(self):
        self.producer.flush(10)


def main():
    logger.info("Lightning ingestion service starting")
    producer = StrikeProducer()
    backoff = 1
    url_index = 0

    def on_open(ws):
        nonlocal backoff
        backoff = 1
        logger.info("WebSocket opened, subscribing to strike feed")
        ws.send(json.dumps({"a": 111}))

    def on_message(ws, message):
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        producer.handle_frame(message)

    def on_error(ws, error):
        logger.error(f"WebSocket error: {error}")

    def on_close(ws, code, msg):
        logger.warning(f"WebSocket closed: code={code} msg={msg}")

    while True:
        url = WEBSOCKET_URLS[url_index % len(WEBSOCKET_URLS)]
        url_index += 1
        try:
            ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            producer.flush()
            return
        except Exception as e:
            logger.error(f"Connection error: {e}")

        producer.flush()
        logger.info(f"Reconnecting in {backoff}s (next server: rotating)")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
