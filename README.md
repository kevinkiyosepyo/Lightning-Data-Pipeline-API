# Lightning-Data-Pipeline-API
Real-time lightning strike data pipeline built with Python, FastAPI, PostgreSQL, and Docker. Ingests live data from the Blitzortung's WebSocket feed and stores it in a database.

Features: 
1. Real-time Data Ingestion: WebSocket connection to Blitzortung's global lightning detection network
2. Custom Decoder: Reverse-engineered compression algorithm for binary data format
3. Spatial Database: PostgreSQL with PostGIS-ready schema and optimized spatial indexes
4. RESTful API: FastAPI endpoints for querying lightning strike data
5. Containerized Architecture: Full Docker Compose stack for easy deployment
6. Automatic Reconnection: Resilient connection handling with exponential backoff
7. Statistics Tracking: Real-time ingestion metrics and success rates

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
# Backend:

Python 3.11
FastAPI (async web framework)
websocket-client (WebSocket handling)
psycopg2 (PostgreSQL driver)

Database:

PostgreSQL 15 (Alpine)
Spatial indexes (lat/lon optimization)
Time-series optimized queries

Infrastructure:

Docker & Docker Compose
Multi-service orchestration
Health checks & dependency management

# Quick start
Prerequisites:

Download Docker & Docker Compose & Python

1. Clone the repo:
```git clone https://github.com/kevinkiyosepyo/lightning-data-pipeline.git```
```cd lightning-data-pipeline```

2. Start the services:
   docker-compose up -d

3. Verify the ingestion is working:
   ```docker-compose logs -f ingestion```
   
You should see lightning strikes being processed now

4. Query the API
   ```curl http://localhost:8000/strikes/recent```

To stop: ```docker-compose down```

# API Endpoints
Get recent strikes: 
```GET /strikes/recent?limit=100```
```
Example Response:
[
  {
    "id": 12345,
    "strike_time": 1699564800000,
    "strike_timestamp": "2025-11-09T12:00:00",
    "latitude": 34.0522,
    "longitude": -118.2437,
    "altitude": 150,
    "polarity": "positive",
    "mds": 8,
    "mcg": 12,
    "inserted_at": "2025-11-09T12:00:01"
  }
]
```

```
GET /stats
```
Returns ingestion statistics and system health metrics.

```
Example Response:
{
  "total_received": 15420,
  "total_stored": 12336,
  "total_failed": 3084,
  "success_rate": 80.0,
  "last_strike_time": "2025-11-09T12:30:45",
  "updated_at": "2025-11-09T12:30:45"
}
```
Health Check
```
GET /health
```
Returns API health status.

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

### `ingestion_stats` Table

Tracks real-time ingestion performance metrics.

## Project Structure

```
lightning-data-pipeline/
├── docker-compose.yml      # Service orchestration
├── Dockerfile.ingestion    # Ingestion service container
├── Dockerfile.api          # API service container
├── ingest.py              # WebSocket ingestion & decoding
├── api.py                 # FastAPI REST endpoints
└── README.md              # This file
```

## 🔍 Technical Highlights

### 1. Custom Binary Protocol Decoder

Reverse-engineered Blitzortung's compressed data format by analyzing hex patterns and building a comprehensive substitution mapping system. The decoder handles:
- Multi-byte Unicode sequences
- Digit compression (2-byte → 1-byte)
- JSON structural elements
- Longitude decimal point correction

### 2. Resilient Connection Management

- Automatic reconnection with exponential backoff
- Database connection pooling and retry logic
- Graceful error handling and logging
- Health checks for service dependencies

### 3. Spatial Data Optimization

- Composite indexes for lat/lon queries
- Timestamp-based partitioning ready
- Constraint validation for coordinate bounds
- Optimized for time-series queries

## 📈 Performance Metrics

- **Throughput**: ~200-300 strikes/minute during active storms
- **Decode Success Rate**: ~80% (typical for noisy real-time data)
- **Latency**: <100ms from WebSocket to database insertion
- **Storage**: ~1KB per strike record

## 🚧 Future Roadmap / Things I want to implement 

- [ ] **Geographic Filtering API** - Radius-based and bounding box queries
- [ ] **Real-time Visualization Dashboard** - Live map with WebSocket updates
- [ ] **Historical Data Export** - Parquet/CSV export for analysis
- [ ] **Alert System** - Configurable notifications for proximity-based events
- [ ] **TimescaleDB Integration** - Enhanced time-series performance
- [ ] **Grafana Dashboard** - Real-time monitoring and analytics

## Troubleshooting

### Container keeps restarting
```bash
docker-compose logs ingestion
```
Check for database connection issues or missing dependencies.

### No strikes appearing in database
1. Check ingestion service is connected: Check logs for "Connected to Blitzortung WebSocket"
2. Check if there's active storm activity globally
3. Verify database connection: `docker exec -it lightning_db psql -U lightning_user -d lightning`

### API not responding
```bash
docker-compose ps
```
Ensure all services show "Up" status.

## Contributing

This is an active learning project so contributions are welcome. 

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Acknowledgments

- **Blitzortung.org** - For providing the global lightning detection network and WebSocket API

## Contact

kevinkpyo@gmail.com
