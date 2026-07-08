# Lightning Data Pipeline & API

[![CI](https://github.com/kevinkiyosepyo/Lightning-Data-Pipeline-API/actions/workflows/ci.yml/badge.svg)](https://github.com/kevinkiyosepyo/Lightning-Data-Pipeline-API/actions/workflows/ci.yml)

A real-time data platform for global lightning strikes. It reverse-engineers Blitzortung's
undocumented compressed WebSocket feed, streams decoded strikes through Kafka into PostgreSQL/PostGIS,
and builds an analytics layer (hour-partitioned Parquet lake + DuckDB warehouse) orchestrated by Dagster
with automated data-quality checks.

- Live source: https://map.blitzortung.org (200–500+ strikes/min during active storms)
- Decode success: **100%** — the feed's compression scheme is LZW, and this repo implements the decoder
- End-to-end latency: sub-second from WebSocket frame to queryable row

---

## The Core Challenge

Blitzortung provides no public API. Strikes arrive over a WebSocket as strings of seemingly
garbled Unicode (`Ĉĉ ĊċČ…`). I initially reverse-engineered a 200-entry character substitution
table by comparing raw frames against expected JSON — which worked ~99% of the time.

The breakthrough was recognizing *why* a substitution table almost works: the feed is
**LZW-compressed**, with each compressed code transmitted as one Unicode code point. A static
table is just a snapshot of the LZW dictionary for a "typical" message; it breaks whenever a
message builds different dictionary entries. Implementing the real LZW decoder
([`lzw.py`](lzw.py), ~30 lines) reconstructs the correct dictionary *per message* and yields
byte-exact JSON every time — including fields the substitution approach could never recover,
like the full list of detector stations that triangulated each strike.

```
Wire:    ą´ĂďĂŊ×ÐÜ...                     (one Unicode char = one LZW code)
Decoded: {"time":1783502092045360000,"lat":22.578867,"lon":-97.768136,
          "alt":0,"pol":0,"mds":11666,"mcg":154,"status":0,"region":3,
          "sig":[{"sta":2157,...}, ...]}   (exact original JSON)
```

---

## Architecture

```
┌──────────────────┐
│   Blitzortung    │
│  WebSocket feed  │
└────────┬─────────┘
         │ LZW-compressed frames
         ▼
┌──────────────────┐   bad frames   ┌──────────────────────┐
│ Ingestion service├───────────────►│ lightning.strikes.dlq │  (dead-letter queue)
│  LZW decode +    │                └──────────────────────┘
│  validation      │
└────────┬─────────┘
         │ lightning.strikes (Kafka)
         ▼
┌──────────────────┐
│ Storage consumer │  batched multi-row inserts, offsets committed
│                  │  after DB commit, idempotent via natural key
└────────┬─────────┘
         ▼
┌──────────────────┐      ┌─────────────────────────────────────────┐
│ PostgreSQL 15    │◄─────┤ Dagster (hourly schedule + asset checks)│
│  + PostGIS       │      │  • hourly_strike_aggregates (Postgres)  │
└────────┬─────────┘      │  • strikes_parquet (partitioned lake)   │
         │                │  • duckdb_analytics (warehouse)         │
         ▼                └─────────────────────────────────────────┘
┌──────────────────┐
│  FastAPI REST    │  spatial queries via ST_DWithin + GiST index
└──────────────────┘
```

**Stack:** Python 3.11 · Kafka · PostgreSQL 15 + PostGIS · FastAPI · Dagster · DuckDB · Parquet · Docker Compose

---

## Quick Start

```bash
# Prerequisite: Docker running

git clone https://github.com/kevinkiyosepyo/Lightning-Data-Pipeline-API.git
cd Lightning-Data-Pipeline-API

docker compose up -d --build

# Watch strikes flow in
docker compose logs -f ingestion consumer

# Query the API (interactive docs at http://localhost:8000/docs)
curl 'http://localhost:8000/strikes/recent?minutes=10&limit=5'

# Dagster UI (assets, quality checks, schedule)
open http://localhost:3000
```

Run the test suite:

```bash
pip install -r requirements/ingest.txt -r requirements/dev.txt
pytest
```

---

## Components

### 1. LZW decoder (`lzw.py`)
The heart of the project. Blitzortung LZW-compresses each strike's JSON and sends the code
stream as text. The decoder rebuilds the compression dictionary incrementally per message —
no dictionary is ever transmitted — including the classic `cScSc` self-referential edge case.
Tested against real captured frames (`tests/fixtures/frames.json`), not synthetic data.

### 2. Ingestion service (`ingest.py`)
WebSocket client → LZW decode → validation → Kafka producer. Failed frames are published to a
**dead-letter topic** with the error attached as a message header instead of being dropped, so
protocol changes are observable and replayable. Reconnects with exponential backoff, rotating
across Blitzortung's server pool.

### 3. Storage consumer (`consumer.py`)
Kafka consumer → PostgreSQL with **batched multi-row inserts** (up to 200 strikes per statement,
1s max latency). Delivery semantics: offsets are committed only after the database transaction
commits (at-least-once), and a natural-key unique constraint with `ON CONFLICT DO NOTHING`
makes writes idempotent — so redeliveries never create duplicates.

### 4. REST API (`api.py`)
FastAPI over a connection pool. `/strikes/nearby` uses PostGIS `ST_DWithin` on a `GEOGRAPHY`
column with a GiST index — exact spherical distance, correct at the poles and across the
antimeridian, no bounding-box approximation.

| Endpoint | Description |
|----------|-------------|
| `GET /strikes` | Filterable by time range and bounding box, paginated |
| `GET /strikes/recent?minutes=60` | Most recent strikes |
| `GET /strikes/nearby?lat=&lon=&radius=` | Radius search (km), sorted by distance |
| `GET /strikes/stats` | Aggregate statistics |
| `GET /ingestion/stats` | Pipeline throughput and success rate |
| `GET /health` | Service + database health |

### 5. Orchestration & analytics (`orchestration/`)
Dagster runs an hourly schedule materializing three assets:

- **`hourly_strike_aggregates`** — per-region hourly rollups upserted into Postgres;
  recomputes a trailing window so late data is folded in and reruns are idempotent
- **`strikes_parquet`** — hour-partitioned Parquet lake (`date=YYYY-MM-DD/hour=HH/`),
  rewritten per partition so backfills are safe
- **`duckdb_analytics`** — DuckDB warehouse over the lake: daily summaries, region activity,
  and storm-cell detection (spatial-bin clustering)

Plus **data-quality checks** surfaced in the Dagster UI: feed freshness (< 15 min lag),
coordinate bounds, and duplicate detection.

---

## Data Model

Core table (`lightning_strikes`):

| Column | Type | Notes |
|--------|------|-------|
| strike_time | BIGINT | Nanosecond epoch from the feed |
| strike_timestamp | TIMESTAMPTZ | Normalized UTC |
| latitude / longitude | DOUBLE PRECISION | CHECK-constrained to valid ranges |
| geom | GEOGRAPHY(POINT, 4326) | GiST-indexed for spatial queries |
| altitude, polarity, mds, mcg | INTEGER | Signal metadata |
| status, region | INTEGER | Feed metadata (unlocked by the LZW decoder) |
| station_count | INTEGER | Detectors that triangulated the strike |

Uniqueness: `(strike_time, latitude, longitude)` — the idempotency key for Kafka redeliveries.

---

## Design Decisions

- **Kafka between ingestion and storage** decouples the fragile part (third-party WebSocket)
  from the durable part. Ingestion stays up during database maintenance; the consumer replays
  from the topic. Adding a second consumer (e.g. real-time alerting) requires no changes to
  ingestion.
- **Batched writes over per-row commits** — the previous version committed twice per strike;
  batching cut database round-trips by ~200x at peak rates.
- **Exactly-once *effect* without exactly-once machinery** — at-least-once delivery plus
  idempotent inserts is simpler and sufficient here.
- **Parquet + DuckDB for analytics** keeps analytical scans off the operational database and
  costs nothing to operate.

**Known limits:** single Kafka broker and single consumer (fine at 500 strikes/min; partition the
topic by region to scale out), Dagster uses local storage rather than a database-backed instance,
and the DLQ has no automated replay job yet.

---

## Project Structure

```
├── lzw.py                    # LZW decoder (the reverse-engineering payoff)
├── ingest.py                 # WebSocket → decode → Kafka producer (+ DLQ)
├── consumer.py               # Kafka → batched PostGIS inserts
├── api.py                    # FastAPI read layer
├── orchestration/
│   ├── assets.py             # Dagster assets + data-quality checks
│   └── definitions.py        # Job, hourly schedule, definitions
├── tests/
│   ├── fixtures/frames.json  # Real frames captured from the live feed
│   ├── test_lzw.py           # Decoder: round-trips, edge cases, real frames
│   └── test_ingest.py        # Frame → record transformation, DLQ routing
├── requirements/             # Per-service pinned dependencies
├── docker-compose.yml        # postgres+postgis, kafka, ingestion, consumer, api, dagster
└── .github/workflows/ci.yml  # ruff + pytest + compose build
```

---

## Acknowledgments

**Blitzortung.org** — a volunteer-operated global lightning detection network. This project is
for educational purposes; if you use their data, respect their
[terms](https://www.blitzortung.org/en/contact.php).

## Contact

Kevin Kiyo · [kevinkpyo@gmail.com](mailto:kevinkpyo@gmail.com) ·
[LinkedIn](https://www.linkedin.com/in/kevin-pyo/) · [GitHub](https://github.com/kevinkiyosepyo)
