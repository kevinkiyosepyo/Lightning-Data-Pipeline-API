# Lightning Data Pipeline & API

A real-time lightning strike data pipeline that reverse-engineers Blitzortung’s undocumented compressed binary WebSocket feed, decodes it into structured data. 

- Processes 200–500+ strikes per minute

- End-to-end latency: sub-100ms

- Live source: https://map.blitzortung.org

---

## The Core Challenge

Blitzortung does not provide a public API or documented data format.

Instead, lightning strikes are broadcast over a compressed binary WebSocket protocol containing:

- Non-UTF8 multi-byte Unicode sequences
- Compressed numeric fields
- Obfuscated JSON-like structures

What I did: 

- Identified the wire format as LZW compression (each frame character is a
  compression code; codes ≥256 index a phrase dictionary built during decode)
- Implemented the LZW decompressor from scratch — every frame now decodes to
  exact JSON with full 19-digit nanosecond timestamps (100% decode rate)
- Kept a plausibility guard (`normalize_epoch`) that clock-anchors any
  damaged epoch and rejects strikes drifting >24h from wall time
- Persisted decoded strikes into PostgreSQL in real time

---

# Architecture:
```
┌─────────────────┐
│  Blitzortung    │
│  WebSocket API  │
└────────┬────────┘
         │ Binary Data Stream
         ▼
┌─────────────────┐
│   Ingestion     │
│   Service       │
│  - Decoder      │
│  - Validator    │
└────────┬────────┘
         │ Structured Data
         ▼
┌─────────────────┐
│  PostgreSQL     │
│   Database      │
│  - Strikes      │
│  - Statistics   │
└────────┬────────┘
         │ SQL Queries
         ▼
┌─────────────────┐
│   FastAPI       │
│   REST API      │
└─────────────────┘
```

**Stack:** Python 3.11 | FastAPI | PostgreSQL 15 | Docker Compose

---

## Quick Start

```bash
Prerequisite:
Have Docker running in the background

# 1. Clone the repo in terminal:
git clone https://github.com/kevinkiyosepyo/lightning-data-pipeline-api.git
cd lightning-data-pipeline-api

# 2. Start the services:
docker-compose up -d --build

# 3. Verify the ingestion is working:
docker-compose logs -f ingestion

#You should see lightning strikes being processed now
#Optional: to query the API, search "http://localhost:8000/strikes" in your browser. 
```

---

## Core Features

### 1. Real-time Data Ingestion
Reverse-engineered Blitzortung's wire protocol from raw WebSocket frames:
- Identified the compression as LZW with an incrementally-built phrase dictionary
- Implemented the decompressor from scratch (~20 lines, stdlib only)
- Decoded frames parse as exact JSON — 19-digit ns timestamps, station
  detection lists, polarity, deviation metrics
- PostgreSQL with PostGIS-ready schema

### 2. Infrastructure
- **Resilient Connections:** Exponential backoff reconnection strategy
- **Data Validation:** Coordinate bounds checking and schema enforcement  
- **Observability:** Real-time metrics tracking (throughput, success rates, latency)
- **Containerization:** Full Docker Compose stack with health checks

### 3. Spatial-Optimized Database
- Composite B-tree indexes on (latitude, longitude) for geographic queries
- Time-series indexes for temporal filtering
- Constraint validation ensuring data integrity
- Ready for PostGIS extension if geospatial queries expand

---

## API Endpoints

### Recent Strikes
```bash
GET /strikes/recent?limit=100
```
Returns the most recent lightning strikes with full metadata (coordinates, polarity, multi-sensor scores).

### System Statistics  
```bash
GET /stats
```
Real-time ingestion metrics: total processed, success rate, throughput, last strike timestamp.

### Health Check
```bash
GET /health
```
Service health status and database connectivity verification.

---

## Performance Profile

| Metric | Value | Context |
|--------|-------|---------|
| **Throughput** | 200-500 strikes/min | During active global storms |
| **Decode Success** | 100% | LZW decompressor, verified against live feed |
| **Insert Latency** | <100ms | WebSocket → Database |
| **Reconnection Time** | <5s | Automatic failover with backoff |

**Current Bottlenecks:** Single-threaded decoder, synchronous database writes. At 10x scale (2,000+ strikes/min), would implement async batch inserts and parallel decoders.

---

## Technical Deep Dive

### Decoder Implementation
The feed is LZW-compressed text: every character of a frame is a compression
code, and codepoints ≥ 256 reference a phrase dictionary built incrementally
during decompression. `BlitzortungDecoder.lzw_decode` implements the standard
algorithm (including the `cScSc` unknown-code case) in ~20 lines, and the
result parses with a plain `json.loads` — no regex extraction, no field
guessing.

**v1 vs v2:** the first decoder modeled dictionary codes as a *fixed*
byte-substitution table (`C4 88 → '0'`, …). Dictionary codes are positional,
not fixed, so ~27% of strikes lost digits — most visibly trailing zeros of
the nanosecond epoch, producing strikes dated 1975 or 2537. Replacing the
table with real LZW took decode success from ~92% (with damaged survivors)
to 100% with exact timestamps, verified against the live feed.

**Example transformation:**
```
Frame:    {"time (then codes ≥256 referencing earlier phrases)
Decoded:  {"time":1789362634636448800,"lat":33.403068,"lon":-107.863082,
           "alt":0,"pol":0,"mds":10197,"mcg":182,"status":0,"region":3,
           "sig":[40 station detections],"delay":3.4}
```

### Safety net: `normalize_epoch`
Damaged epochs (wrong magnitude, e.g. truncated trailing zeros) are recovered
by choosing the power-of-ten rescaling that lands closest to the current
wall clock — valid because strikes are live. Anything still >24h off is
rejected outright. With the LZW path this fires on 0 frames; it exists to
keep a future protocol change from silently corrupting the table again.

---

# Database Schema


| Column | Type | Description |
|--------|------|-------------|
| id | BIGSERIAL | Primary key |
| strike_time | BIGINT | Unix timestamp (microseconds) |
| strike_timestamp | TIMESTAMP | Human-readable timestamp |
| latitude | DOUBLE PRECISION | Latitude (-90 to 90) |
| longitude | DOUBLE PRECISION | Longitude (-180 to 180) |
| altitude | INTEGER | Altitude in meters (nullable) |
| polarity | VARCHAR(50) | Strike polarity (nullable) |
| mds | INTEGER | Multi-sensor detection score (nullable) |
| mcg | INTEGER | Multi-sensor cloud-to-ground (nullable) |
| inserted_at | TIMESTAMP | Record insertion time |

**Indexes:**
- `idx_strike_timestamp` - Optimized for time-based queries
- `idx_location` - Optimized for spatial queries
- `idx_inserted_at` - Optimized for recent data retrieval

---
## Production Considerations

If deploying this for enterprise use, I would add:

**Scalability**
- Horizontal scaling with message queue (Kafka/RabbitMQ) between ingestion and storage
- Read replicas for query load distribution
- Connection pooling with pgBouncer

**Observability**  
- Structured logging (JSON format) with correlation IDs
- Prometheus metrics export for Grafana dashboards
- Distributed tracing for request flow analysis

**Data Quality**
- Dead letter queue for failed decodes with manual review pipeline
- Data validation service comparing against Blitzortung's map UI
- Automated alerting for decode success rate drops below threshold

**Security**
- API authentication (JWT tokens)
- Rate limiting per client (Redis-based)
- TLS/SSL for all connections
- Secrets management (AWS Secrets Manager / HashiCorp Vault)

---

## Project Structure

```
lightning-data-pipeline/
├── docker-compose.yml       # Orchestration for ingestion, API, and database
├── Dockerfile.ingestion     # Container for WebSocket client + decoder
├── Dockerfile.api           # Container for FastAPI REST service
├── ingest.py               # WebSocket client with binary decoder
├── api.py                  # FastAPI endpoints and database queries
└── README.md               # This file
```

---

## Future Enhancements

**If I had another week:**
1. **Geographic Filtering API** - `/strikes/near?lat=X&lon=Y&radius=50km` endpoint (30 min implementation)
2. **Pytest Test Suite** - Unit tests for decoder, integration tests for API endpoints
3. **TimescaleDB Migration** - Hypertables for 10x time-series query performance
4. **Grafana Dashboard** - Real-time visualization of ingestion rate, success rate, geographic distribution

**For production deployment:**
5. **CI/CD Pipeline** - GitHub Actions for automated testing and deployment
6. **Cloud Infrastructure** - Terraform scripts for AWS deployment (RDS, ECS, ALB)
7. **Monitoring Stack** - Prometheus + Grafana + Alertmanager for SLA tracking

---

## Acknowledgments

**Blitzortung.org** - Global lightning detection network providing the WebSocket data feed

---

## Contact

Kevin Kiyo  
[kevinkpyo@gmail.com](mailto:kevinkpyo@gmail.com)  
[LinkedIn](https://www.linkedin.com/in/kevin-pyo/) | [GitHub](https://github.com/kevinkiyosepyo)
