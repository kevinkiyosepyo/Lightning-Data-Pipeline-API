# Lightning Data Pipeline & API

A real-time lightning strike pipeline in **Rust**. It reverse-engineers
Blitzortung's undocumented LZW-compressed WebSocket feed, decodes every frame
to exact JSON, batches strikes into PostgreSQL, and serves them over a REST
API with a live dashboard.

- 200–500+ strikes/min sustained, 100% decode success on the live feed
- Two ~50 MB distroless containers, no runtime dependencies
- Golden-tested against real captured frames; decoder output is byte-identical
  to the reference implementation

---

## The Core Challenge

Blitzortung has no public API or documented data format. Strikes arrive over
a WebSocket as compressed text where every character is a compression code.

Three iterations to get it right:

| | Approach | Result |
|---|---|---|
| **v1** (Python) | Fixed 200-entry byte-substitution table, reverse-engineered by hand from hex dumps | ~92% "success", but ~27% of survivors had **silently lost digits** — trailing zeros of the ns epoch — yielding strikes dated 1975 or 2537 |
| **v2** (Python) | Identified the format as **LZW** with a positional phrase dictionary; implemented the real decompressor | 100% decode, exact 19-digit timestamps |
| **v3** (Rust) | Full rewrite: same LZW algorithm, plus batched writes, backpressure, connection pooling, golden tests | Same correctness; **11× less ingest memory, 19× less API memory, 5× smaller images** (measured, below) |

The v1 → v2 lesson: the substitution table *looked* right because it was
approximately right — dictionary codes are positional, not fixed, so a table
can only ever match a subset of frames. The failure mode was silent data
corruption, not crashes. That is the kind of bug that only shows up when you
actually look at the data's distribution (in this case: "why are 27% of
timestamps in the wrong century?").

---

## Architecture

```
┌──────────────────┐
│   Blitzortung    │  wss://ws7.blitzortung.org  ({"a":111} subscribes)
│   WebSocket      │
└────────┬─────────┘
         │ LZW-compressed frames
         ▼
┌──────────────────────────────────────────────────────────────┐
│  lightning-ingest  (Rust, tokio)                             │
│                                                              │
│   reader task            mpsc(2048)          writer task     │
│   ├ LZW decode      ───────────────────►     ├ batch ≤50     │
│   ├ serde_json                               ├ flush 250 ms  │
│   ├ validate                                 └ 1 multi-row   │
│   └ reconnect w/ backoff                        INSERT       │
└────────────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  PostgreSQL 15        │
                     │  lightning_strikes    │
                     │  ingestion_stats      │
                     └───────────┬───────────┘
                                 │ deadpool (16 conns)
                                 ▼
┌──────────────────────────────────────────────────────────────┐
│  lightning-api  (Rust, axum)                                 │
│  /strikes  /strikes/recent  /strikes/nearby  /strikes/stats  │
│  /ingestion/stats  /health  /live (embedded dashboard)       │
└──────────────────────────────────────────────────────────────┘
```

Decoding happens on the reader task so a slow database never stalls the
socket; the bounded channel applies backpressure instead of unbounded growth.

**Stack:** Rust 1.90 · tokio · tokio-tungstenite · axum 0.8 · tokio-postgres +
deadpool · PostgreSQL 15 · Docker Compose · distroless runtime

---

## Quick Start

```bash
# Prerequisite: Docker running
git clone https://github.com/kevinkiyosepyo/Lightning-Data-Pipeline-API.git
cd Lightning-Data-Pipeline-API

docker compose up -d --build      # first build ~5 min (release + LTO)
docker compose logs -f ingestion  # watch strikes arrive

open http://localhost:8000/live   # live dashboard
```

Run the test suite (needs a local Rust toolchain):

```bash
cargo test --workspace            # 15 unit + golden tests
scripts/smoke.sh                  # 17 end-to-end checks against a running stack
```

---

## Workspace Layout

```
crates/
├── lightning-core/      Library shared by both services
│   ├── src/lzw.rs         LZW decompressor (the protocol)
│   ├── src/epoch.rs       ns-epoch plausibility guard
│   ├── src/strike.rs      RawStrike → validated Strike
│   ├── src/geo.rs         Haversine, bounding box
│   └── tests/golden.rs    40 real frames; Rust output must equal reference
├── lightning-ingest/    WebSocket → decode → batched Postgres
│   ├── src/main.rs        reader/writer tasks, reconnect loop
│   └── src/db.rs          schema, multi-row INSERT, stats
└── lightning-api/       axum REST API
    ├── src/main.rs        router, pool, CORS
    ├── src/routes.rs      one handler per endpoint
    └── src/models.rs      row → DTO, timestamp parsing
Dockerfile               multi-stage, cargo-chef cached, two distroless targets
docker-compose.yml       postgres + ingestion + api
live.html                dashboard (embedded into the API binary at build time)
scripts/smoke.sh         end-to-end verification
reference/               v2 Python decoder + fixture capture script (the
                         reference implementation the golden tests check against)
```

---

## API Endpoints

Responses are identical in shape to the original Python API, so existing
consumers work unchanged.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness + DB connectivity + total row count |
| `GET /strikes/recent?minutes=60&limit=100` | Newest strikes, **ordered by ingestion time** (see below) |
| `GET /strikes?since=&until=&min_lat=&max_lat=&min_lon=&max_lon=&limit=&offset=` | Filtered query |
| `GET /strikes/nearby?lat=&lon=&radius=50&minutes=60&limit=100` | Radius search: bbox SQL prefilter → exact Haversine → sort by distance |
| `GET /strikes/stats?since=&until=` | Count, time range, centroid |
| `GET /ingestion/stats` | Received / stored / failed / success rate |
| `GET /live` | Dashboard, served from the binary |

Validation failures return **422** with `{"detail": "..."}`, never 500.

**Why `/strikes/recent` orders by `inserted_at`:** the feed can in principle
deliver a corrupt strike time. Ordering by that column lets a single
far-future row pin the top of a live view forever. Ingestion time is
monotonic and ours.

---

## Performance Profile

Measured on the same host, same Postgres, same live feed, ~2 min warm:

| Metric | Python (v2) | Rust (v3) | Notes |
|---|---|---|---|
| Throughput | 200–500/min | 200–500/min | Feed-limited, not pipeline-limited |
| Decode success | 100% | 100% | 0 failures across every measured window |
| Feed latency (p50) | ~20 s | ~20 s | Blitzortung's own network delay; not ours |
| Insert latency | per-row commit | ≤250 ms batch | One multi-row INSERT per flush |
| `/strikes/recent?limit=100` | ~30 ms | **~10 ms** | Pooled connections, no per-request connect |
| Ingest RSS | 28.5 MB | **2.5 MB** | 11× |
| API RSS | 39.9 MB | **2.1 MB** | 19× |
| Ingest image | 253 MB | **52 MB** | distroless `cc-debian12:nonroot` |
| API image | 302 MB | **52 MB** | " |

---

## Technical Deep Dive

### The LZW decoder

Every character of a frame is a code. Codes < 256 are literals; codes ≥ 256
index a phrase dictionary that is built *while decoding*: after each output,
`previous_phrase + first_char(current_phrase)` is appended. The classic
`cScSc` edge case (a code referencing the entry currently being defined)
resolves to `previous + previous[0]`.

```rust
// crates/lightning-core/src/lzw.rs — the whole protocol
for ch in chars {
    let code = ch as usize;
    let phrase = if code < 256 { ch.to_string() }
                 else { dict.get(code - 256).cloned()
                        .unwrap_or_else(|| previous.clone() + current) };
    out.push_str(&phrase);
    current = phrase.chars().next().unwrap();
    dict.push(previous.clone() + current);
    previous = phrase;
}
```

The output is plain JSON. `serde_json` parses it into a typed `RawStrike`;
no regexes, no field guessing.

### Golden tests

`crates/lightning-core/tests/fixtures/frames.json` holds 40 frames captured
from the live feed alongside the reference decoder's output. Two tests:

1. Rust LZW output equals the reference **byte-for-byte** on every frame.
2. Every frame decodes to a validated strike with a 19-digit epoch and the
   epoch-recovery path **never fires**.

If Blitzortung changes the protocol, these fail before anything reaches the
database.

### Epoch safety net

`epoch::normalize` accepts any plausible ns epoch as-is. If the magnitude is
wrong (e.g. lost trailing zeros), it picks the power-of-ten rescaling that
lands nearest the wall clock — valid because strikes are live — and tags the
result `Recovery::Rescaled`. `strike::from_raw` then rejects anything still
>24 h from now. Recovery count is logged; in production it stays at 0.

### Batched writes with backpressure

The writer drains a bounded `mpsc(2048)` into batches of ≤50 or every 250 ms,
whichever first, and issues one multi-row parameterized `INSERT`. If Postgres
stalls, the channel fills and the reader's `send().await` applies
backpressure; the socket is never read faster than we can persist. Dropped
batches are counted in `ingestion_stats.total_failed`, not silently lost.

---

## Database Schema

| Column | Type | Description |
|---|---|---|
| id | BIGSERIAL | Primary key |
| strike_time | BIGINT | Feed epoch, nanoseconds |
| strike_timestamp | TIMESTAMP | Decoded UTC instant |
| latitude / longitude | DOUBLE PRECISION | Checked constraints on range |
| altitude | INTEGER | Meters (nullable) |
| polarity | VARCHAR(50) | Feed `pol` field (nullable) |
| mds / mcg | INTEGER | Feed deviation metrics (nullable) |
| stations | SMALLINT | Number of detecting stations (`len(sig)`) |
| region | SMALLINT | Blitzortung region id |
| delay_s | REAL | Feed-reported network delay |
| inserted_at | TIMESTAMP | Ingestion time |

Indexes: `strike_timestamp DESC`, `(latitude, longitude)`, `inserted_at DESC`.
Schema setup is idempotent (`CREATE … IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`), so the Rust services run against a volume created by the Python
version without migration.

---

## Production Considerations

What I'd add for enterprise deployment:

- **Scale:** Kafka between ingest and storage; TimescaleDB hypertables;
  read replicas behind the API
- **Observability:** Prometheus `/metrics` from both services (batch sizes,
  channel depth, decode failures, recovery count); JSON logs with trace IDs
- **Data quality:** dead-letter table for rejected frames; alert when decode
  success drops below 99.9% or recovery count becomes non-zero
- **Security:** API auth, per-client rate limiting, TLS to Postgres, secrets
  from a vault rather than compose env

---

## Acknowledgments

**Blitzortung.org** — community lightning detection network providing the feed.
