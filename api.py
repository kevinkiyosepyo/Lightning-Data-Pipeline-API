from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import math

app = FastAPI(
    title="Lightning Strike API",
    description="Query real-time and historical lightning strike data",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database connection
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        database=os.getenv('POSTGRES_DB', 'lightning'),
        user=os.getenv('POSTGRES_USER', 'lightning_user'),
        password=os.getenv('POSTGRES_PASSWORD', 'lightning_pass'),
        port=os.getenv('POSTGRES_PORT', '5432'),
        cursor_factory=RealDictCursor
    )

# Models
class LightningStrike(BaseModel):
    id: int
    strike_time: int
    strike_timestamp: datetime
    latitude: float
    longitude: float
    altitude: Optional[int] = None
    polarity: Optional[str] = None
    mds: Optional[int] = None
    mcg: Optional[int] = None
    inserted_at: datetime

class StrikeStats(BaseModel):
    total_strikes: int
    time_range_start: Optional[datetime]
    time_range_end: Optional[datetime]
    avg_latitude: Optional[float]
    avg_longitude: Optional[float]

class IngestionStats(BaseModel):
    total_received: int
    total_stored: int
    total_failed: int
    last_strike_time: Optional[datetime]
    updated_at: datetime
    success_rate: float

# Utility functions
def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers using Haversine formula."""
    R = 6371  # Earth's radius in kilometers
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    
    return R * c

# Endpoints
@app.get("/")
def root():
    return {
        "message": "Lightning Strike API",
        "version": "1.0.0",
        "endpoints": {
            "/strikes": "Get lightning strikes with filters",
            "/strikes/recent": "Get most recent strikes",
            "/strikes/nearby": "Get strikes near a location",
            "/strikes/stats": "Get strike statistics",
            "/health": "Health check",
            "/ingestion/stats": "Ingestion service statistics"
        }
    }

@app.get("/health")
def health_check():
    """Check if API and database are healthy."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM lightning_strikes")
            result = cursor.fetchone()
        conn.close()
        
        return {
            "status": "healthy",
            "database": "connected",
            "total_strikes": result['count']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/strikes", response_model=List[LightningStrike])
def get_strikes(
    limit: int = Query(100, ge=1, le=1000, description="Number of strikes to return"),
    offset: int = Query(0, ge=0, description="Number of strikes to skip"),
    since: Optional[datetime] = Query(None, description="Get strikes after this time"),
    until: Optional[datetime] = Query(None, description="Get strikes before this time"),
    min_lat: Optional[float] = Query(None, ge=-90, le=90, description="Minimum latitude"),
    max_lat: Optional[float] = Query(None, ge=-90, le=90, description="Maximum latitude"),
    min_lon: Optional[float] = Query(None, ge=-180, le=180, description="Minimum longitude"),
    max_lon: Optional[float] = Query(None, ge=-180, le=180, description="Maximum longitude")
):
    """Get lightning strikes with optional filters."""
    query = "SELECT * FROM lightning_strikes WHERE 1=1"
    params = []
    
    if since:
        query += " AND strike_timestamp >= %s"
        params.append(since)
    
    if until:
        query += " AND strike_timestamp <= %s"
        params.append(until)
    
    if min_lat is not None:
        query += " AND latitude >= %s"
        params.append(min_lat)
    
    if max_lat is not None:
        query += " AND latitude <= %s"
        params.append(max_lat)
    
    if min_lon is not None:
        query += " AND longitude >= %s"
        params.append(min_lon)
    
    if max_lon is not None:
        query += " AND longitude <= %s"
        params.append(max_lon)
    
    query += " ORDER BY strike_timestamp DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            results = cursor.fetchall()
        conn.close()
        
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/strikes/recent", response_model=List[LightningStrike])
def get_recent_strikes(
    minutes: int = Query(60, ge=1, le=1440, description="Get strikes from last N minutes"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of strikes")
):
    """Get most recent lightning strikes."""
    since = datetime.utcnow() - timedelta(minutes=minutes)
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM lightning_strikes
                WHERE strike_timestamp >= %s
                ORDER BY strike_timestamp DESC
                LIMIT %s
            """, (since, limit))
            results = cursor.fetchall()
        conn.close()
        
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/strikes/nearby", response_model=List[LightningStrike])
def get_nearby_strikes(
    lat: float = Query(..., ge=-90, le=90, description="Latitude of center point"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude of center point"),
    radius: float = Query(50, ge=1, le=500, description="Search radius in kilometers"),
    minutes: int = Query(60, ge=1, le=1440, description="Time window in minutes"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of strikes")
):
    """Get lightning strikes near a specific location."""
    since = datetime.utcnow() - timedelta(minutes=minutes)
    
    # Calculate bounding box (approximate, faster than exact distance calculation)
    # 1 degree latitude ≈ 111 km
    # 1 degree longitude varies by latitude
    lat_delta = radius / 111.0
    lon_delta = radius / (111.0 * math.cos(math.radians(lat)))
    
    min_lat = lat - lat_delta
    max_lat = lat + lat_delta
    min_lon = lon - lon_delta
    max_lon = lon + lon_delta
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM lightning_strikes
                WHERE strike_timestamp >= %s
                AND latitude BETWEEN %s AND %s
                AND longitude BETWEEN %s AND %s
                ORDER BY strike_timestamp DESC
                LIMIT %s
            """, (since, min_lat, max_lat, min_lon, max_lon, limit * 2))  # Get more for filtering
            results = cursor.fetchall()
        conn.close()
        
        # Filter by exact distance
        filtered_results = []
        for strike in results:
            distance = haversine_distance(lat, lon, strike['latitude'], strike['longitude'])
            if distance <= radius:
                strike['distance_km'] = round(distance, 2)
                filtered_results.append(strike)
        
        # Sort by distance and limit
        filtered_results.sort(key=lambda x: x.get('distance_km', float('inf')))
        return filtered_results[:limit]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/strikes/stats", response_model=StrikeStats)
def get_strike_stats(
    since: Optional[datetime] = Query(None, description="Calculate stats from this time"),
    until: Optional[datetime] = Query(None, description="Calculate stats until this time")
):
    """Get statistics about lightning strikes."""
    query = """
        SELECT 
            COUNT(*) as total_strikes,
            MIN(strike_timestamp) as time_range_start,
            MAX(strike_timestamp) as time_range_end,
            AVG(latitude) as avg_latitude,
            AVG(longitude) as avg_longitude
        FROM lightning_strikes
        WHERE 1=1
    """
    params = []
    
    if since:
        query += " AND strike_timestamp >= %s"
        params.append(since)
    
    if until:
        query += " AND strike_timestamp <= %s"
        params.append(until)
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            result = cursor.fetchone()
        conn.close()
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/ingestion/stats", response_model=IngestionStats)
def get_ingestion_stats():
    """Get statistics from the ingestion service."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM ingestion_stats LIMIT 1")
            result = cursor.fetchone()
        conn.close()
        
        if not result:
            raise HTTPException(status_code=404, detail="No ingestion stats found")
        
        total = result['total_received']
        stored = result['total_stored']
        success_rate = (stored / total * 100) if total > 0 else 0
        
        return {
            **result,
            'success_rate': round(success_rate, 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)