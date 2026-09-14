import json
import websocket
from websocket import ABNF
import datetime
import psycopg2
import os
import time
from typing import Optional, Dict, Any
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Nanosecond-epoch bounds considered plausible for a live feed
# (2001-09-09 .. 2033-05-18). Anything outside means the value was damaged.
NS_EPOCH_MIN = 10**18
NS_EPOCH_MAX = 2 * 10**18


def normalize_epoch(ts: int) -> datetime.datetime:
    """Safety net for damaged epochs: recover the closest plausible instant.

    The primary decode path (LZW -> json.loads) yields exact 19-digit
    nanosecond epochs and never needs this. It exists only to rescue values
    whose magnitude was corrupted (e.g. lost trailing zeros): strikes are
    live, so the best power-of-ten rescaling is the one landing nearest now.
    """
    if ts <= 0:
        raise ValueError(f"non-positive epoch: {ts}")

    now_ns = time.time_ns()
    best_ns = None
    best_err = None
    for k in range(-6, 13):
        cand = ts * (10 ** k) if k >= 0 else ts // (10 ** -k)
        if cand <= 0:
            continue
        err = abs(cand - now_ns)
        if best_err is None or err < best_err:
            best_ns, best_err = cand, err

    if best_ns is None:
        raise ValueError(f"could not normalize epoch: {ts}")

    return datetime.datetime.fromtimestamp(best_ns / 1_000_000_000)


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
                    host=os.getenv('POSTGRES_HOST', 'postgres'),
                    database=os.getenv('POSTGRES_DB', 'lightning'),
                    user=os.getenv('POSTGRES_USER', 'lightning_user'),
                    password=os.getenv('POSTGRES_PASSWORD', 'lightning_pass'),
                    port=os.getenv('POSTGRES_PORT', '5432')
                )
                self.conn.autocommit = False
                logger.info("Successfully connected to database")
                return
            except psycopg2.OperationalError:
                if attempt < max_retries - 1:
                    logger.warning(f"Database connection failed (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error("Failed to connect to database after all retries")
                    raise

    def create_tables(self):
        """Create tables if they don't exist."""
        with self.conn.cursor() as cursor:
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

                -- Fields surfaced by the LZW decoder (older schema lacked them)
                ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS stations SMALLINT;
                ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS region SMALLINT;
                ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS delay_s REAL;
            """)

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
                    (strike_time, strike_timestamp, latitude, longitude, altitude,
                     polarity, mds, mcg, stations, region, delay_s)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    strike_data['time'],
                    strike_data['timestamp'],
                    strike_data['lat'],
                    strike_data['lon'],
                    strike_data.get('alt'),
                    strike_data.get('pol'),
                    strike_data.get('mds'),
                    strike_data.get('mcg'),
                    strike_data.get('stations'),
                    strike_data.get('region'),
                    strike_data.get('delay'),
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
    """Decodes Blitzortung's LZW-compressed WebSocket frames.

    Each frame is an LZW-compressed JSON document: every character of the
    frame is a compression code, and codepoints >= 256 are references into
    a phrase dictionary built incrementally during decompression.

    The previous implementation modeled those dictionary codes as a FIXED
    byte-substitution table (0xC4 0x88 -> '0', ...). Dictionary codes are
    positional, not fixed, so that table was only ever approximately right:
    codes it mapped to '' silently deleted digits — most visibly trailing
    zeros of the nanosecond epoch — producing strikes dated 1975 or 2537.
    Verified against the live feed: real LZW decodes 100% of frames to
    exact JSON with full 19-digit timestamps.
    """

    def __init__(self, database: LightningDatabase):
        self.db = database
        self.sample_count = 0
        self.successful_decodes = 0
        self.failed_decodes = 0
        self.recovered_epochs = 0
        self.last_stats_update = time.time()

    @staticmethod
    def lzw_decode(compressed: str) -> str:
        """Standard LZW over unicode codepoints (Blitzortung wire format)."""
        if not compressed:
            return ""

        phrases: Dict[int, str] = {}
        current = compressed[0]
        previous = current
        out = [current]
        next_code = 256

        for ch in compressed[1:]:
            code = ord(ch)
            if code < 256:
                phrase = ch
            else:
                # Unknown code = the cScSc special case in LZW
                phrase = phrases.get(code, previous + current)
            out.append(phrase)
            current = phrase[0]
            phrases[next_code] = previous + current
            next_code += 1
            previous = phrase

        return "".join(out)

    def decode(self, data) -> Optional[Dict]:
        """Decode one WebSocket frame into a strike dict, or None."""
        self.sample_count += 1

        try:
            if isinstance(data, (bytes, bytearray)):
                data = data.decode('utf-8', errors='strict')

            payload = json.loads(self.lzw_decode(data))
            strike = self._extract_fields(payload)

            if strike and self._validate_strike(strike):
                self.successful_decodes += 1
                return strike

            self.failed_decodes += 1
            return None

        except Exception as e:
            logger.error(f"Decode error: {e}")
            self.failed_decodes += 1
            return None

    def _extract_fields(self, obj: Dict[str, Any]) -> Optional[Dict]:
        """Map a decoded Blitzortung payload onto our storage schema."""
        ts_raw = obj.get('time')
        lat = obj.get('lat')
        lon = obj.get('lon')
        if ts_raw is None or lat is None or lon is None:
            return None

        ts_raw = int(ts_raw)
        if NS_EPOCH_MIN < ts_raw < NS_EPOCH_MAX:
            timestamp = datetime.datetime.fromtimestamp(ts_raw / 1_000_000_000)
        else:
            # Should not happen with the LZW path; recover rather than drop.
            timestamp = normalize_epoch(ts_raw)
            self.recovered_epochs += 1
            logger.warning(f"Implausible epoch {ts_raw}, recovered as {timestamp} "
                           f"(total recovered: {self.recovered_epochs})")

        sig = obj.get('sig') or []
        pol = obj.get('pol')
        delay = obj.get('delay')

        return {
            'time': ts_raw,
            'timestamp': timestamp,
            'lat': float(lat),
            'lon': float(lon),
            'alt': int(obj['alt']) if obj.get('alt') is not None else None,
            'pol': str(pol) if pol is not None else None,
            'mds': int(obj['mds']) if obj.get('mds') is not None else None,
            'mcg': int(obj['mcg']) if obj.get('mcg') is not None else None,
            'stations': len(sig),
            'region': int(obj['region']) if obj.get('region') is not None else None,
            'delay': float(delay) if delay is not None else None,
        }

    def _validate_strike(self, strike: Dict) -> bool:
        """Validate strike data before storage."""
        lat = strike.get('lat')
        lon = strike.get('lon')
        if lat is None or lon is None:
            return False
        if abs(lat) > 90 or abs(lon) > 180:
            logger.warning(f"Invalid coordinates: {lat}, {lon}")
            return False

        # A strike more than a day from the wall clock survived epoch
        # recovery with a garbage value; refuse to store it.
        drift = abs((strike['timestamp'] - datetime.datetime.now()).total_seconds())
        if drift > 86_400:
            logger.warning(f"Rejecting strike with implausible timestamp {strike['timestamp']}")
            return False

        return True

    def print_stats(self):
        """Print current statistics."""
        success_rate = (self.successful_decodes / self.sample_count * 100) if self.sample_count > 0 else 0
        recovered = f" | Recovered epochs: {self.recovered_epochs}" if self.recovered_epochs else ""
        logger.info(f"Processed: {self.sample_count} | Stored: {self.successful_decodes} | "
                    f"Failed: {self.failed_decodes} | Success: {success_rate:.1f}%{recovered}")


# WebSocket event handlers
db = None
decoder = None

def on_data(ws, data, opcode, fin):
    """Handle incoming WebSocket data."""
    strike = decoder.decode(data)

    if strike:
        if db.insert_strike(strike):
            db.update_stats(received=1, stored=1)
        else:
            db.update_stats(received=1, failed=1)
    else:
        db.update_stats(received=1, failed=1)

    if decoder.sample_count % 10 == 0:
        decoder.print_stats()

def on_open(ws):
    logger.info("WebSocket opened")
    subscribe_msg = json.dumps({"a": 111})
    ws.send(subscribe_msg)
    logger.info("Subscribed to lightning feed")

def on_error(ws, error):
    logger.exception(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.warning(f"WebSocket closed: code={close_status_code} msg={close_msg}")

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

            result = ws.run_forever(ping_interval=30, ping_timeout=10)
            logger.warning(f"run_forever returned: {result} (reconnecting in 5s)")

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            db.close()
            break
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.info("Reconnecting in 5 seconds...")
            time.sleep(5)
            if db:
                db.close()

if __name__ == "__main__":
    main()
