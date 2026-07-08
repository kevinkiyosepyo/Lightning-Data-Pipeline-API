"""Lightning Strike REST API.

Read-side of the pipeline: serves strikes, spatial queries (PostGIS), and
ingestion metrics out of PostgreSQL via a shared connection pool.
"""

import os
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from pydantic import BaseModel

pool: ThreadedConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "lightning"),
        user=os.getenv("POSTGRES_USER", "lightning_user"),
        password=os.getenv("POSTGRES_PASSWORD", "lightning_pass"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        cursor_factory=RealDictCursor,
    )
    yield
    pool.closeall()


app = FastAPI(
    title="Lightning Strike API",
    description="Query real-time and historical lightning strike data",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def db_cursor():
    conn = pool.getconn()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.rollback()  # read-only endpoints; release any snapshot
    finally:
        pool.putconn(conn)


def run_query(query: str, params=None, fetch_one: bool = False):
    try:
        with db_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() if fetch_one else cursor.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e


class LightningStrike(BaseModel):
    id: int
    strike_time: int
    strike_timestamp: datetime
    latitude: float
    longitude: float
    altitude: int | None = None
    polarity: int | None = None
    mds: int | None = None
    mcg: int | None = None
    status: int | None = None
    region: int | None = None
    station_count: int | None = None
    inserted_at: datetime
    distance_km: float | None = None


class StrikeStats(BaseModel):
    total_strikes: int
    time_range_start: datetime | None
    time_range_end: datetime | None
    strikes_last_hour: int
    avg_station_count: float | None


class IngestionStats(BaseModel):
    total_received: int
    total_stored: int
    total_failed: int
    last_strike_time: datetime | None
    updated_at: datetime
    success_rate: float


STRIKE_COLUMNS = """
    id, strike_time, strike_timestamp, latitude, longitude, altitude,
    polarity, mds, mcg, status, region, station_count, inserted_at
"""


@app.get("/")
def root():
    return {
        "message": "Lightning Strike API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "/strikes": "Get lightning strikes with filters",
            "/strikes/recent": "Get most recent strikes",
            "/strikes/nearby": "Get strikes near a location (PostGIS)",
            "/strikes/stats": "Get strike statistics",
            "/ingestion/stats": "Ingestion pipeline statistics",
            "/health": "Health check",
        },
    }


@app.get("/map", include_in_schema=False)
def live_map():
    """Live world map of incoming strikes (Leaflet, polls /strikes/recent)."""
    return FileResponse(Path(__file__).parent / "map.html", media_type="text/html")


@app.get("/health")
def health_check():
    result = run_query("SELECT count(*) AS count FROM lightning_strikes", fetch_one=True)
    return {"status": "healthy", "database": "connected", "total_strikes": result["count"]}


@app.get("/strikes", response_model=list[LightningStrike])
def get_strikes(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    since: datetime | None = Query(None, description="Strikes at or after this time"),
    until: datetime | None = Query(None, description="Strikes at or before this time"),
    min_lat: float | None = Query(None, ge=-90, le=90),
    max_lat: float | None = Query(None, ge=-90, le=90),
    min_lon: float | None = Query(None, ge=-180, le=180),
    max_lon: float | None = Query(None, ge=-180, le=180),
):
    """Get lightning strikes with optional time and bounding-box filters."""
    filters = {
        "strike_timestamp >= %s": since,
        "strike_timestamp <= %s": until,
        "latitude >= %s": min_lat,
        "latitude <= %s": max_lat,
        "longitude >= %s": min_lon,
        "longitude <= %s": max_lon,
    }
    clauses = [clause for clause, value in filters.items() if value is not None]
    params = [value for value in filters.values() if value is not None]

    query = f"SELECT {STRIKE_COLUMNS} FROM lightning_strikes"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY strike_timestamp DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return run_query(query, params)


@app.get("/strikes/recent", response_model=list[LightningStrike])
def get_recent_strikes(
    minutes: int = Query(60, ge=1, le=1440),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get strikes from the last N minutes."""
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    return run_query(
        f"""
        SELECT {STRIKE_COLUMNS} FROM lightning_strikes
        WHERE strike_timestamp >= %s
        ORDER BY strike_timestamp DESC
        LIMIT %s
        """,
        (since, limit),
    )


@app.get("/strikes/nearby", response_model=list[LightningStrike])
def get_nearby_strikes(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(50, ge=1, le=1000, description="Search radius in kilometers"),
    minutes: int = Query(60, ge=1, le=1440),
    limit: int = Query(100, ge=1, le=1000),
):
    """Get strikes within a radius of a point, exact and index-accelerated.

    Uses PostGIS ST_DWithin on a geography column with a GiST index — correct
    at the poles and across the antimeridian, no bounding-box approximation.
    """
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    return run_query(
        f"""
        SELECT {STRIKE_COLUMNS},
               round((ST_Distance(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                      / 1000.0)::numeric, 2) AS distance_km
        FROM lightning_strikes
        WHERE strike_timestamp >= %s
          AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
        ORDER BY distance_km ASC
        LIMIT %s
        """,
        (lon, lat, since, lon, lat, radius * 1000, limit),
    )


@app.get("/strikes/stats", response_model=StrikeStats)
def get_strike_stats(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
):
    """Get aggregate statistics about stored strikes."""
    query = """
        SELECT
            count(*) AS total_strikes,
            min(strike_timestamp) AS time_range_start,
            max(strike_timestamp) AS time_range_end,
            count(*) FILTER (WHERE strike_timestamp >= now() - interval '1 hour') AS strikes_last_hour,
            avg(station_count) AS avg_station_count
        FROM lightning_strikes
    """
    clauses, params = [], []
    if since is not None:
        clauses.append("strike_timestamp >= %s")
        params.append(since)
    if until is not None:
        clauses.append("strike_timestamp <= %s")
        params.append(until)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    return run_query(query, params, fetch_one=True)


@app.get("/ingestion/stats", response_model=IngestionStats)
def get_ingestion_stats():
    """Get pipeline throughput statistics maintained by the storage consumer."""
    result = run_query("SELECT * FROM ingestion_stats LIMIT 1", fetch_one=True)
    if not result:
        raise HTTPException(status_code=404, detail="No ingestion stats found")

    total = result["total_received"]
    success_rate = (result["total_stored"] / total * 100) if total > 0 else 0.0
    return {**result, "success_rate": round(success_rate, 2)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
