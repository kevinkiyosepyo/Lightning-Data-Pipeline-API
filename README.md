# Lightning-Data-Pipeline-API
Real-time lightning strike data pipeline built with Python, FastAPI, PostgreSQL, and Docker. Ingests live data from the Blitzortung's WebSocket feed and stores it in a database: https://www.blitzortung.org/

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


1. Fork the repository
2. Create a feature branch (`git checkout -b feature/-feature`)
3. Commit your changes (`git commit -m 'Add feature'`)
4. Push to the branch (`git push origin feature/feature`)
5. Open a Pull Request

## Acknowledgments

- **Blitzortung.org** - For providing the global lightning detection network and WebSocket API

## Contact

kevinkpyo@gmail.com



# Sample Output & Examples

## 📊 Live Ingestion Output

### Console Logs from Ingestion Service

```
2025-11-09 02:41:25,046 - INFO - Successfully connected to database
2025-11-09 02:41:25,047 - INFO - Database tables created/verified
2025-11-09 02:41:25,048 - INFO - Lightning Data Ingestion Service Starting...
2025-11-09 02:41:25,985 - INFO - WebSocket connected
2025-11-09 02:41:25,985 - INFO - Connected to Blitzortung WebSocket
2025-11-09 02:41:27,276 - INFO - Processed: 10 | Stored: 10 | Failed: 0 | Success: 100.0%
2025-11-09 02:41:33,379 - INFO - Processed: 20 | Stored: 13 | Failed: 7 | Success: 65.0%
2025-11-09 02:41:36,787 - INFO - Processed: 30 | Stored: 21 | Failed: 9 | Success: 70.0%
2025-11-09 02:41:38,587 - INFO - Processed: 40 | Stored: 28 | Failed: 12 | Success: 70.0%
2025-11-09 02:41:53,507 - INFO - Processed: 70 | Stored: 45 | Failed: 25 | Success: 64.3%
2025-11-09 02:42:01,615 - INFO - Processed: 80 | Stored: 54 | Failed: 26 | Success: 67.5%
2025-11-09 02:42:17,834 - INFO - Processed: 150 | Stored: 114 | Failed: 36 | Success: 76.0%
2025-11-09 02:42:43,553 - INFO - Processed: 210 | Stored: 162 | Failed: 48 | Success: 77.1%
2025-11-09 02:43:21,691 - INFO - Processed: 300 | Stored: 241 | Failed: 59 | Success: 80.3%
```

**Key Metrics:**
- ~80% decode success rate (typical for real-time compressed data)
- Processing hundreds of strikes per minute during active storms
- Sub-second latency from WebSocket to database

---

## 🔌 API Response Example Output

### 1. Get Recent Strikes

**Request:**
```bash
curl http://localhost:8000/strikes/recent?limit=5
```

**Response:**
```json
[
  {
    "id": 15234,
    "strike_time": 1699564800123456,
    "strike_timestamp": "2025-11-09T18:32:45.123456",
    "latitude": 34.0522,
    "longitude": -118.2437,
    "altitude": 150,
    "polarity": "positive",
    "mds": 8,
    "mcg": 12,
    "inserted_at": "2025-11-09T18:32:45.234567"
  },
  {
    "id": 15233,
    "strike_time": 1699564799876543,
    "strike_timestamp": "2025-11-09T18:32:44.876543",
    "latitude": 40.7128,
    "longitude": -74.0060,
    "altitude": 85,
    "polarity": "negative",
    "mds": 6,
    "mcg": 9,
    "inserted_at": "2025-11-09T18:32:45.123456"
  }
  
```

---

### 2. Get System Statistics

**Request:**
```bash
curl http://localhost:8000/stats
```

**Response:**
```json
{
  "total_received": 15420,
  "total_stored": 12336,
  "total_failed": 3084,
  "success_rate": 80.0,
  "last_strike_time": "2025-11-09T18:35:23.456789",
  "updated_at": "2025-11-09T18:35:23.567890",
  "strikes_per_minute": 245.6,
  "uptime_hours": 2.5
}
```

---

### 3. Health Check

**Request:**
```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "ingestion_service": "active",
  "last_strike_received": "2025-11-09T18:36:01.234567",
  "total_strikes_stored": 12458
}
```

---

## 🗄️ Database Schema Example

### Lightning Strikes Table

```sql
SELECT * FROM lightning_strikes 
ORDER BY strike_timestamp DESC 
LIMIT 5;
```

| id | strike_time | strike_timestamp | latitude | longitude | altitude | polarity | mds | mcg | inserted_at |
|----|------------|-----------------|----------|-----------|----------|----------|-----|-----|-------------|
| 15234 | 1699564800123456 | 2025-11-09 18:32:45.123456 | 34.0522 | -118.2437 | 150 | positive | 8 | 12 | 2025-11-09 18:32:45.234567 |
| 15233 | 1699564799876543 | 2025-11-09 18:32:44.876543 | 40.7128 | -74.0060 | 85 | negative | 6 | 9 | 2025-11-09 18:32:45.123456 |
| 15232 | 1699564798456789 | 2025-11-09 18:32:43.456789 | 51.5074 | -0.1278 | 120 | positive | 10 | 15 | 2025-11-09 18:32:43.987654 |
| 15231 | 1699564797123456 | 2025-11-09 18:32:42.123456 | -33.8688 | 151.2093 | 95 | negative | 7 | 11 | 2025-11-09 18:32:42.456789 |
| 15230 | 1699564796789012 | 2025-11-09 18:32:41.789012 | 35.6762 | 139.6503 | 110 | positive | 9 | 13 | 2025-11-09 18:32:42.123456 |

---

### Ingestion Statistics Table

```sql
SELECT * FROM ingestion_stats;
```

| id | total_received | total_stored | total_failed | last_strike_time | updated_at |
|----|----------------|--------------|--------------|------------------|------------|
| 1 | 15420 | 12336 | 3084 | 2025-11-09 18:35:23.456789 | 2025-11-09 18:35:23.567890 |

---

## 🔍 Raw WebSocket Data Examples

### Compressed Binary Data (Hex)

This is what comes directly from the Blitzortung WebSocket before decoding:

```
c4 88 c4 89 c4 8a c4 8b c4 8c c4 8d c4 8e c4 8f
c4 90 c4 91 c4 86 c4 87 c4 b8 c4 b9 c4 ba c4 bb
```

### After Substitution Mapping

```
0123456789: ."time":1699564800123456,"lat":34.0522,"lon":-118.2437
```

### Final Decoded JSON

```json
{
  "time": 1699564800123456,
  "lat": 34.0522,
  "lon": -118.2437,
  "alt": 150,
  "pol": "positive",
  "mds": 8,
  "mcg": 12
}
```

---

## 📈 Performance Metrics

### Typical System Performance

| Metric | Value | Notes |
|--------|-------|-------|
| **Throughput** | 200-500 strikes/min | During active storms |
| **Decode Success Rate** | ~80% | Normal for compressed real-time data |
| **Database Insert Latency** | <50ms | Per strike |
| **WebSocket Reconnection** | <5s | Automatic with backoff |
| **Memory Usage** | ~150MB | Ingestion service |
| **Storage per Strike** | ~1KB | Including indexes |

---

## 🌍 Geographic Distribution Example

**Sample query showing global coverage:**

```sql
SELECT 
  CASE 
    WHEN latitude >= 0 THEN 'Northern Hemisphere'
    ELSE 'Southern Hemisphere'
  END as hemisphere,
  COUNT(*) as strike_count,
  AVG(latitude) as avg_lat,
  AVG(longitude) as avg_lon
FROM lightning_strikes
WHERE strike_timestamp > NOW() - INTERVAL '1 hour'
GROUP BY hemisphere;
```

| hemisphere | strike_count | avg_lat | avg_lon |
|------------|-------------|---------|---------|
| Northern Hemisphere | 8234 | 42.3567 | -95.2341 |
| Southern Hemisphere | 4102 | -28.4523 | 135.6789 |

---

## 🐳 Docker Compose Status

```bash
docker-compose ps
```

```
NAME                  IMAGE                COMMAND                  SERVICE     CREATED          STATUS                    PORTS
lightning_api         coding-api           "uvicorn api:app --h…"   api         15 minutes ago   Up 15 minutes (healthy)   0.0.0.0:8000->8000/tcp
lightning_db          postgres:15-alpine   "docker-entrypoint.s…"   postgres    15 minutes ago   Up 15 minutes (healthy)   0.0.0.0:5432->5432/tcp
lightning_ingestion   coding-ingestion     "python ingest.py"       ingestion   15 minutes ago   Up 15 minutes             
```

---

## 📊📊 Query Examples 📊📊

### Find strikes near a location (San Diego)

```sql
SELECT 
  id,
  strike_timestamp,
  latitude,
  longitude,
  SQRT(
    POW(69.1 * (latitude - 32.7157), 2) + 
    POW(69.1 * (-117.1611 - longitude) * COS(latitude / 57.3), 2)
  ) AS distance_miles
FROM lightning_strikes
WHERE strike_timestamp > NOW() - INTERVAL '1 hour'
  AND latitude BETWEEN 30 AND 35
  AND longitude BETWEEN -120 AND -115
ORDER BY distance_miles
LIMIT 10;
```

### Hourly strike activity

```sql
SELECT 
  DATE_TRUNC('hour', strike_timestamp) as hour,
  COUNT(*) as strike_count,
  AVG(CASE WHEN polarity = 'positive' THEN 1 ELSE 0 END) * 100 as pct_positive
FROM lightning_strikes
WHERE strike_timestamp > NOW() - INTERVAL '24 hours'
GROUP BY hour
ORDER BY hour DESC;
```

---

## 🎨 Visualization I might implement

1. **Real-time Strike Map**: Plot strikes on a world map as they occur
2. **Heatmap**: Show lightning density by geographic region
3. **Time Series**: Graph strike frequency over time
4. **Storm Tracking**: Follow cluster movements across regions
5. **Polarity Analysis**: Visualize positive vs negative strikes

---

## 🔗 Integration Examples

### Python Client

```python
import requests
import time

# Poll for new strikes
def monitor_lightning():
    while True:
        response = requests.get('http://localhost:8000/strikes/recent?limit=10')
        strikes = response.json()
        
        for strike in strikes:
            print(f"⚡ Strike detected at ({strike['latitude']}, {strike['longitude']}) "
                  f"at {strike['strike_timestamp']}")
        
        time.sleep(5)

monitor_lightning()
```

### JavaScript/Node.js Client

```javascript
const axios = require('axios');

async function getRecentStrikes() {
  try {
    const response = await axios.get('http://localhost:8000/strikes/recent?limit=5');
    console.log('Recent Lightning Strikes:', response.data);
  } catch (error) {
    console.error('Error fetching strikes:', error);
  }
}

// Poll every 10 seconds
setInterval(getRecentStrikes, 10000);
```

---

## 📝 Notes

- All timestamps are in UTC
- Coordinates use WGS84 datum (standard GPS coordinates)
- Strike polarity indicates charge (positive/negative)
- MDS = Multi-sensor detection score (higher = more sensors detected it)
- MCG = Multi-sensor cloud-to-ground confidence
- Altitude is in meters above sea level where available

---

[← Back to Main README](README.md)
