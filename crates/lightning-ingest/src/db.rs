//! Postgres schema management and batched strike writes.

use anyhow::{Context, Result};
use lightning_core::Strike;
use tokio_postgres::{types::ToSql, Client, NoTls};
use tracing::{info, warn};

pub struct DbConfig {
    pub host: String,
    pub port: u16,
    pub db: String,
    pub user: String,
    pub password: String,
}

impl DbConfig {
    pub fn from_env() -> Self {
        let get = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
        Self {
            host: get("POSTGRES_HOST", "postgres"),
            port: get("POSTGRES_PORT", "5432").parse().unwrap_or(5432),
            db: get("POSTGRES_DB", "lightning"),
            user: get("POSTGRES_USER", "lightning_user"),
            password: get("POSTGRES_PASSWORD", "lightning_pass"),
        }
    }

    pub fn conn_string(&self) -> String {
        format!(
            "host={} port={} dbname={} user={} password={} application_name=lightning-ingest",
            self.host, self.port, self.db, self.user, self.password
        )
    }
}

/// Connect with retry; spawns the connection driver task.
pub async fn connect(cfg: &DbConfig) -> Result<Client> {
    let mut delay = std::time::Duration::from_secs(2);
    for attempt in 1..=8 {
        match tokio_postgres::connect(&cfg.conn_string(), NoTls).await {
            Ok((client, connection)) => {
                tokio::spawn(async move {
                    if let Err(e) = connection.await {
                        warn!(error = %e, "postgres connection task ended");
                    }
                });
                info!(host = %cfg.host, db = %cfg.db, "connected to postgres");
                return Ok(client);
            }
            Err(e) if attempt < 8 => {
                warn!(attempt, error = %e, "postgres connect failed; retrying in {delay:?}");
                tokio::time::sleep(delay).await;
                delay = (delay * 2).min(std::time::Duration::from_secs(30));
            }
            Err(e) => return Err(e).context("postgres connect failed after retries"),
        }
    }
    unreachable!()
}

/// Idempotent schema setup. Matches the Python-era schema exactly so the
/// existing volume and any external consumers keep working.
pub async fn ensure_schema(client: &Client) -> Result<()> {
    client
        .batch_execute(
            r#"
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
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS stations SMALLINT;
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS region SMALLINT;
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS delay_s REAL;
            -- Full-capture fields: everything the feed sends, plus derived
            -- geometry. stations alone is right-censored at 40 and weakly
            -- predicts fix quality, so gap/distances carry the real signal.
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS status SMALLINT;
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS stations_censored BOOLEAN;
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS azimuthal_gap_deg REAL;
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS nearest_station_km REAL;
            ALTER TABLE lightning_strikes ADD COLUMN IF NOT EXISTS farthest_station_km REAL;

            -- Per-station detections (the sig[] array), normalized.
            CREATE TABLE IF NOT EXISTS strike_stations (
                strike_id BIGINT NOT NULL REFERENCES lightning_strikes(id) ON DELETE CASCADE,
                station_id INTEGER NOT NULL,
                station_time BIGINT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                altitude INTEGER,
                status SMALLINT,
                PRIMARY KEY (strike_id, station_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ss_strike ON strike_stations(strike_id);
            CREATE INDEX IF NOT EXISTS idx_ss_station ON strike_stations(station_id);

            CREATE TABLE IF NOT EXISTS ingestion_stats (
                id SERIAL PRIMARY KEY,
                total_received INTEGER DEFAULT 0,
                total_stored INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                last_strike_time TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO ingestion_stats (total_received, total_stored, total_failed)
            SELECT 0, 0, 0 WHERE NOT EXISTS (SELECT 1 FROM ingestion_stats);
            "#,
        )
        .await
        .context("ensure schema")?;
    info!("schema verified");
    Ok(())
}

const COLS: usize = 16;

/// Insert a batch of strikes plus their per-station detections.
///
/// Both writes share one transaction: a strike and its station rows are
/// committed together or not at all, so `strike_stations` can never contain
/// orphans or miss rows for a stored strike. Returns rows written.
pub async fn insert_batch(client: &mut Client, strikes: &[Strike]) -> Result<u64> {
    if strikes.is_empty() {
        return Ok(0);
    }

    let tx = client.transaction().await.context("begin tx")?;

    let mut sql = String::with_capacity(260 + strikes.len() * 60);
    sql.push_str(
        "INSERT INTO lightning_strikes \
         (strike_time, strike_timestamp, latitude, longitude, altitude, \
          polarity, mds, mcg, stations, region, delay_s, status, \
          stations_censored, azimuthal_gap_deg, nearest_station_km, \
          farthest_station_km) VALUES ",
    );
    let mut params: Vec<&(dyn ToSql + Sync)> = Vec::with_capacity(strikes.len() * COLS);
    // Naive UTC timestamps to match the TIMESTAMP (without tz) column.
    let naive: Vec<chrono::NaiveDateTime> = strikes
        .iter()
        .map(|s| s.strike_timestamp.naive_utc())
        .collect();

    for (i, s) in strikes.iter().enumerate() {
        if i > 0 {
            sql.push(',');
        }
        let base = i * COLS;
        sql.push('(');
        for j in 0..COLS {
            if j > 0 {
                sql.push(',');
            }
            sql.push('$');
            sql.push_str(&(base + j + 1).to_string());
        }
        sql.push(')');

        params.push(&s.strike_time);
        params.push(&naive[i]);
        params.push(&s.latitude);
        params.push(&s.longitude);
        params.push(&s.altitude);
        params.push(&s.polarity);
        params.push(&s.mds);
        params.push(&s.mcg);
        params.push(&s.stations);
        params.push(&s.region);
        params.push(&s.delay_s);
        params.push(&s.status);
        params.push(&s.stations_censored);
        params.push(&s.azimuthal_gap_deg);
        params.push(&s.nearest_station_km);
        params.push(&s.farthest_station_km);
    }
    sql.push_str(" RETURNING id");

    let rows = tx
        .query(sql.as_str(), &params)
        .await
        .context("batch insert")?;
    let ids: Vec<i64> = rows.iter().map(|r| r.get::<_, i64>("id")).collect();

    insert_station_hits(&tx, &ids, strikes).await?;

    tx.commit().await.context("commit tx")?;
    Ok(ids.len() as u64)
}

const SCOLS: usize = 7;
/// Postgres caps a statement at 65535 parameters; stay well under.
const MAX_STATION_ROWS: usize = 2000;

/// One `strike_stations` row, flattened and owned so the values outlive the
/// borrows held in the query parameter vector.
/// `(strike_id, station_id, station_time, lat, lon, alt, status)`
type StationRow = (i64, i32, i64, f64, f64, Option<i32>, Option<i16>);

/// Write the sig[] detections for each strike, chunked to respect the
/// parameter limit (50 strikes x 40 stations = 2000 rows x 7 params).
async fn insert_station_hits(
    tx: &tokio_postgres::Transaction<'_>,
    ids: &[i64],
    strikes: &[Strike],
) -> Result<()> {
    let mut flat: Vec<StationRow> = Vec::new();
    for (id, s) in ids.iter().zip(strikes) {
        for h in &s.station_hits {
            flat.push((
                *id,
                h.sta,
                h.time,
                h.lat,
                h.lon,
                h.alt.map(|v| v as i32),
                h.status.map(|v| v as i16),
            ));
        }
    }
    if flat.is_empty() {
        return Ok(());
    }

    for chunk in flat.chunks(MAX_STATION_ROWS) {
        let mut sql = String::with_capacity(160 + chunk.len() * 30);
        sql.push_str(
            "INSERT INTO strike_stations \
             (strike_id, station_id, station_time, latitude, longitude, altitude, status) VALUES ",
        );
        let mut params: Vec<&(dyn ToSql + Sync)> = Vec::with_capacity(chunk.len() * SCOLS);
        for (i, row) in chunk.iter().enumerate() {
            if i > 0 {
                sql.push(',');
            }
            let base = i * SCOLS;
            sql.push('(');
            for j in 0..SCOLS {
                if j > 0 {
                    sql.push(',');
                }
                sql.push('$');
                sql.push_str(&(base + j + 1).to_string());
            }
            sql.push(')');
            params.push(&row.0);
            params.push(&row.1);
            params.push(&row.2);
            params.push(&row.3);
            params.push(&row.4);
            params.push(&row.5);
            params.push(&row.6);
        }
        // A station can legitimately appear twice for one strike in the feed;
        // keep the first rather than aborting the batch.
        sql.push_str(" ON CONFLICT (strike_id, station_id) DO NOTHING");
        tx.execute(sql.as_str(), &params)
            .await
            .context("station hits insert")?;
    }
    Ok(())
}

/// Add to the running ingestion counters.
pub async fn bump_stats(client: &Client, received: i32, stored: i32, failed: i32) -> Result<()> {
    client
        .execute(
            "UPDATE ingestion_stats SET \
             total_received = total_received + $1, \
             total_stored = total_stored + $2, \
             total_failed = total_failed + $3, \
             last_strike_time = CURRENT_TIMESTAMP, \
             updated_at = CURRENT_TIMESTAMP",
            &[&received, &stored, &failed],
        )
        .await
        .context("bump stats")?;
    Ok(())
}
