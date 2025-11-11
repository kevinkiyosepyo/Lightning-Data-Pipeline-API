# Lightning-Data-Pipeline-API
Real-time lightning strike data pipeline built with Python, FastAPI, PostgreSQL, and Docker. Ingests live data from the Blitzortung's WebSocket feed and stores it in a database.

Features: 
1. Real-time Data Ingestion: WebSocket connection to Blitzortung's global lightning detection network
2. Custom Protocol Decoder: Reverse-engineered compression algorithm for binary data format
3. Spatial Database: PostgreSQL with PostGIS-ready schema and optimized spatial indexes
4. RESTful API: FastAPI endpoints for querying lightning strike data
5. Containerized Architecture: Full Docker Compose stack for easy deployment
6. Automatic Reconnection: Resilient connection handling with exponential backoff
7. Statistics Tracking: Real-time ingestion metrics and success rates

Architecture:
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

# Tech stack
Backend:

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
Prerequisites

Docker & Docker Compose
