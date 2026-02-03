# Lightning Data Pipeline & API

Real-time lightning strike data pipeline that reverse-engineers Blitzortung's compressed binary WebSocket protocol to process 200-500 strikes per minute with sub-100ms latency.

**The Challenge:** Blitzortung's WebSocket feed uses an undocumented compressed binary format. By analyzing hex patterns and building a custom substitution mapping system, I achieved 80% decode accuracy for multi-byte Unicode sequences and compressed JSON structures.

---

## Why This Exists

Lightning strike data has real-world applications in:
- **Infrastructure Protection** - Early warning systems for power grids and telecommunications
- **IoT Sensor Networks** - Distributed environmental monitoring systems

This pipeline provides a queryable, real-time data source for these applications.

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
Prerequisites:
Have Docker running in the background

1. Clone the repo:
git clone https://github.com/kevinkiyosepyo/lightning-data-pipeline-api.git
cd lightning-data-pipeline-api

2. Start the services:
docker-compose up -d

3. Verify the ingestion is working:
docker-compose logs -f ingestion
   
You should see lightning strikes being processed now
```

**Requirements:** Docker, Docker Compose

---

## Core Features

### 1. Real-time Data Ingestion
Reverse-engineered Blitzortung's compression scheme by analyzing patterns in raw WebSocket data:
- Multi-byte Unicode sequence mapping (C4 88 → '0', C4 89 → '1', etc.)
- 2-byte to 1-byte digit compression
- JSON structure reconstruction
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
| **Decode Success** | ~80% | Typical for undocumented compressed protocols |
| **Insert Latency** | <100ms | WebSocket → Database |
| **Reconnection Time** | <5s | Automatic failover with backoff |

**Current Bottlenecks:** Single-threaded decoder, synchronous database writes. At 10x scale (2,000+ strikes/min), would implement async batch inserts and parallel decoders.

---

## Technical Deep Dive

### Decoder Implementation
The WebSocket returns compressed binary data. I built a character substitution map by:
1. Capturing raw hex output and comparing to expected JSON structure
2. Identifying repeating patterns (e.g., C4 88-C4 91 for digits 0-9)
3. Building a 200+ character mapping table for Unicode sequences
4. Handling edge cases (longitude decimals, null values, special characters)

**Example transformation:**
```
Raw hex:  C4 88 C4 89 C4 8A C4 8B C4 8C
Decoded:  0     1     2     3     4
Result:   {"time":1699564800123456,"lat":34.0522,...}
```

### Why 80% Success Rate?
The remaining 20% failures come from:
- Incomplete/corrupted WebSocket frames (network issues)
- Unknown character mappings for rare edge cases
- Protocol changes from Blitzortung (evolving format)

This is acceptable for real-time processing where volume compensates for individual losses. For critical applications, implementing a validation layer against Blitzortung's map UI would increase accuracy.

---

# 📊 Database Schema


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
[LinkedIn](https://linkedin.com/in/your-profile) | [GitHub](https://github.com/kevinkiyosepyo)
