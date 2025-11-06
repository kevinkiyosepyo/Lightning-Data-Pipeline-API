import json
import websocket
from websocket import ABNF
import re
import datetime
import psycopg2
from psycopg2.extras import execute_values
import os
import time
from typing import Optional, Dict, List
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LightningDatabase:
    """Handles all database operations for lightning strikes."""
    
    def __init__(self):
        self.conn = None
        self.connect()
        self.create_tables()
    
    def connect(self):
        """Connect to PostgreSQL database."""
        max_retries = 5
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                self.conn = psycopg2.connect(
                    host=os.getenv('POSTGRES_HOST', 'localhost'),
                    database=os.getenv('POSTGRES_DB', 'lightning'),
                    user=os.getenv('POSTGRES_USER', 'lightning_user'),
                    password=os.getenv('POSTGRES_PASSWORD', 'lightning_pass'),
                    port=os.getenv('POSTGRES_PORT', '5432')
                )
                self.conn.autocommit = False
                logger.info("Successfully connected to database")
                return
            except psycopg2.OperationalError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Database connection failed (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error("Failed to connect to database after all retries")
                    raise
    
    def create_tables(self):
        """Create tables if they don't exist."""
        with self.conn.cursor() as cursor:
            # Main strikes table with PostGIS for spatial queries
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lightning_strikes (
                    id BIGSERIAL PRIMARY KEY,
                    strike_time BIGINT NOT NULL,
                    strike_timestamp TIMESTAMP NOT NULL,
                    latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL,
                    altitude INTEGER,
                    polarity VARCHAR(50),
                    mds INTEGER,
                    mcg INTEGER,
                    inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT valid_latitude CHECK (latitude >= -90 AND latitude <= 90),
                    CONSTRAINT valid_longitude CHECK (longitude >= -180 AND longitude <= 180)
                );
                
                CREATE INDEX IF NOT EXISTS idx_strike_timestamp ON lightning_strikes(strike_timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_location ON lightning_strikes(latitude, longitude);
                CREATE INDEX IF NOT EXISTS idx_inserted_at ON lightning_strikes(inserted_at DESC);
            """)
            
            # Statistics table for tracking ingestion
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_stats (
                    id SERIAL PRIMARY KEY,
                    total_received INTEGER DEFAULT 0,
                    total_stored INTEGER DEFAULT 0,
                    total_failed INTEGER DEFAULT 0,
                    last_strike_time TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                INSERT INTO ingestion_stats (total_received, total_stored, total_failed)
                SELECT 0, 0, 0
                WHERE NOT EXISTS (SELECT 1 FROM ingestion_stats);
            """)
            
            self.conn.commit()
            logger.info("Database tables created/verified")
    
    def insert_strike(self, strike_data: Dict) -> bool:
        """Insert a single lightning strike."""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO lightning_strikes 
                    (strike_time, strike_timestamp, latitude, longitude, altitude, polarity, mds, mcg)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    strike_data['time'],
                    strike_data['timestamp'],
                    strike_data['lat'],
                    strike_data['lon'],
                    strike_data.get('alt'),
                    strike_data.get('pol'),
                    strike_data.get('mds'),
                    strike_data.get('mcg')
                ))
                self.conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to insert strike: {e}")
            self.conn.rollback()
            return False
    
    def update_stats(self, received: int = 0, stored: int = 0, failed: int = 0):
        """Update ingestion statistics."""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE ingestion_stats SET
                        total_received = total_received + %s,
                        total_stored = total_stored + %s,
                        total_failed = total_failed + %s,
                        last_strike_time = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                """, (received, stored, failed))
                self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to update stats: {e}")
            self.conn.rollback()
    
    def get_stats(self) -> Dict:
        """Get current ingestion statistics."""
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT * FROM ingestion_stats LIMIT 1")
            row = cursor.fetchone()
            if row:
                return {
                    'total_received': row[1],
                    'total_stored': row[2],
                    'total_failed': row[3],
                    'last_strike_time': row[4],
                    'updated_at': row[5]
                }
        return {}
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")


class BlitzortungDecoder:
    """Decodes compressed Blitzortung lightning data."""
    
    def __init__(self, database: LightningDatabase):
        self.db = database
        self.substitutions = {
            # Digit mappings
            b'\xc4\x88': b'0', b'\xc4\x89': b'1', b'\xc4\x8a': b'2', b'\xc4\x8b': b'3',
            b'\xc4\x8c': b'4', b'\xc4\x8d': b'5', b'\xc4\x8e': b'6', b'\xc4\x8f': b'7',
            b'\xc4\x90': b'8', b'\xc4\x91': b'9', b'\xc4\x92': b'2', b'\xc4\x93': b'3',
            b'\xc4\x94': b'4', b'\xc4\x95': b'5', b'\xc4\x96': b'6', b'\xc4\x97': b'7',
            b'\xc4\x98': b'8', b'\xc4\x99': b'9', b'\xc4\x9a': b'0', b'\xc4\x9b': b'1',
            b'\xc4\xa0': b'0', b'\xc4\xa1': b'1', b'\xc4\xa2': b'2', b'\xc4\xa3': b'3',
            b'\xc4\xa4': b'4', b'\xc4\xa5': b'5', b'\xc4\xa6': b'6', b'\xc4\xa7': b'7',
            b'\xc4\xa8': b'8', b'\xc4\xa9': b'9',
            
            # JSON structural elements
            b'\xc4\x86': b': ', b'\xc4\x87': b'.',
            
            # Quotes
            b'\xc4\xb8': b'"', b'\xc4\xb9': b'"', b'\xc4\xba': b'"', b'\xc4\xbb': b'"',
            b'\xc4\xbc': b'"', b'\xc4\xbd': b'"', b'\xc4\x9c': b'"', b'\xc4\x9d': b'"',
            b'\xc4\x9e': b'"', b'\xc4\x9f': b'"', b'\xc4\xb0': b'"', b'\xc4\xb1': b'"',
            b'\xc4\xb2': b'"', b'\xc4\xb3': b'"', b'\xc4\xb4': b'"', b'\xc4\xb5': b'"',
            
            # Special characters
            b'\xc4\xac': b'', b'\xc5\x86': b'6', b'\xc4\xab': b'', b'\xc4\xaa': b'',
            b'\xc4\xb6': b'', b'\xc5\x80': b'', b'\xc5\x84': b'4', b'\xc5\x81': b'',
            b'\xc4\xad': b'', b'\xc5\x85': b'5', b'\xc5\x89': b'9', b'\xc5\x88': b'8',
            b'\xc4\x80': b'', b'\xc4\x8e': b'6', b'\xc5\x9b': b'', b'\xc5\x8d': b'',
            b'\xc4\x81': b'', b'\xc4\x83': b'', b'\xc4\x85': b'', b'\xc4\xae': b'',
            b'\xc4\xbe': b'',
        }
        
        self.sample_count = 0
        self.successful_decodes = 0
        self.failed_decodes = 0
        self.last_stats_update = time.time()
    
    def decode(self, data: bytes) -> Optional[Dict]:
        """Decode lightning strike data."""
        self.sample_count += 1
        
        try:
            # Apply substitutions
            result = bytearray(data)
            for compressed, original in self.substitutions.items():
                result = result.replace(compressed, original)
            
            decoded = result.decode('utf-8', errors='replace')
            strike_data = self._extract_fields(decoded)
            
            if strike_data and self._validate_strike(strike_data):
                self.successful_decodes += 1
                return strike_data
            else:
                self.failed_decodes += 1
                return None
                
        except Exception as e:
            logger.error(f"Decode error: {e}")
            self.failed_decodes += 1
            return None
    
    def _extract_fields(self, text: str) -> Optional[Dict]:
        """Extract fields from decoded text."""
        try:
            # Extract timestamp
            time_match = re.search(r'time[":]+(\d+)', text)
            if not time_match:
                return None
            
            timestamp_raw = int(time_match.group(1))
            
            # Convert to datetime
            if timestamp_raw > 1e15:
                timestamp = datetime.datetime.fromtimestamp(timestamp_raw / 1000000)
            elif timestamp_raw > 1e12:
                timestamp = datetime.datetime.fromtimestamp(timestamp_raw / 1000)
            else:
                timestamp = datetime.datetime.fromtimestamp(timestamp_raw)
            
            # Extract coordinates
            lat_match = re.search(r'lat[:\s]*([0-9.-]+)', text)
            lon_match = re.search(r'lon[:\s]*([0-9.-]+)', text)
            
            if not lat_match or not lon_match:
                return None
            
            lat = float(lat_match.group(1))
            lon = self._fix_longitude(lon_match.group(1))
            
            # Extract optional fields
            alt_match = re.search(r'"?al"?[:\s]*([0-9.-]+)', text)
            alt = int(float(alt_match.group(1))) if alt_match else None
            
            pol_match = re.search(r'pol[:\s]*"?([^"]+)"?', text)
            pol = pol_match.group(1) if pol_match and pol_match.group(1) != 'mds' else None
            
            mds_match = re.search(r'mds[:\s]*([0-9.-]+)', text)
            mds = int(float(mds_match.group(1))) if mds_match else None
            
            mcg_match = re.search(r'mcg[:\s]*([0-9.-]+)', text)
            mcg = int(float(mcg_match.group(1))) if mcg_match else None
            
            return {
                'time': timestamp_raw,
                'timestamp': timestamp,
                'lat': lat,
                'lon': lon,
                'alt': alt,
                'pol': pol,
                'mds': mds,
                'mcg': mcg
            }
        except Exception as e:
            logger.debug(f"Field extraction error: {e}")
            return None
    
    def _fix_longitude(self, lon_str: str) -> float:
        """Fix longitude decimal point issues."""
        try:
            value = float(lon_str)
            if abs(value) > 1000:
                str_val = str(abs(int(value)))
                if len(str_val) >= 6:
                    if len(str_val) == 8:
                        corrected = float(str_val[:2] + '.' + str_val[2:])
                    elif len(str_val) == 7:
                        corrected = float(str_val[:2] + '.' + str_val[2:])
                    else:
                        for i in range(1, len(str_val)):
                            test_val = float(str_val[:i] + '.' + str_val[i:])
                            if test_val <= 180:
                                corrected = test_val
                                break
                        else:
                            corrected = value
                    
                    if value < 0:
                        corrected = -corrected
                    return corrected
            return value
        except:
            return None
    
    def _validate_strike(self, strike: Dict) -> bool:
        """Validate strike data."""
        if not strike:
            return False
        
        lat = strike.get('lat')
        lon = strike.get('lon')
        
        if lat is None or lon is None:
            return False
        
        if abs(lat) > 90 or abs(lon) > 180:
            logger.warning(f"Invalid coordinates: {lat}, {lon}")
            return False
        
        return True
    
    def print_stats(self):
        """Print current statistics."""
        success_rate = (self.successful_decodes / self.sample_count * 100) if self.sample_count > 0 else 0
        logger.info(f"Processed: {self.sample_count} | Stored: {self.successful_decodes} | Failed: {self.failed_decodes} | Success: {success_rate:.1f}%")


# WebSocket event handlers
db = LightningDatabase()
decoder = BlitzortungDecoder(db)

def on_data(ws, data, opcode, fin):
    """Handle incoming WebSocket data."""
    if opcode == ABNF.OPCODE_BINARY:
        raw = data
    else:
        if isinstance(data, str):
            raw = data.encode('utf-8', errors='replace')
        else:
            raw = data
    
    # Decode strike
    strike = decoder.decode(raw)
    
    if strike:
        # Store in database
        if db.insert_strike(strike):
            db.update_stats(received=1, stored=1)
        else:
            db.update_stats(received=1, failed=1)
    else:
        db.update_stats(received=1, failed=1)
    
    # Print stats every 10 strikes
    if decoder.sample_count % 10 == 0:
        decoder.print_stats()

def on_open(ws):
    """Handle WebSocket connection open."""
    logger.info("Connected to Blitzortung WebSocket")
    ws.send('{"a":111}')

def on_error(ws, e):
    """Handle WebSocket errors."""
    logger.error(f"WebSocket error: {e}")

def on_close(ws, *args):
    """Handle WebSocket connection close."""
    logger.info("WebSocket connection closed")
    decoder.print_stats()

def main():
    """Main ingestion loop."""
    logger.info("Lightning Data Ingestion Service Starting...")
    
    global db, decoder
    
    while True:
        try:
            # Recreate database connection on each loop iteration
            db = LightningDatabase()
            decoder = BlitzortungDecoder(db)
            
            websocket.enableTrace(False)
            ws = websocket.WebSocketApp(
                "wss://ws7.blitzortung.org/",
                on_open=on_open,
                on_data=on_data,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever()
            
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            db.close()
            break
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.info("Reconnecting in 5 seconds...")
            if db:
                db.close()
            time.sleep(5)