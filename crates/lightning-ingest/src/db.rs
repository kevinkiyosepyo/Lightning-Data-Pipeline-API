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

const COLS: usize = 11;

/// Insert a batch of strikes in a single multi-row statement.
/// Returns the number of rows written.
pub async fn insert_batch(client: &Client, strikes: &[Strike]) -> Result<u64> {
    if strikes.is_empty() {
        return Ok(0);
    }

    let mut sql = String::with_capacity(200 + strikes.len() * 40);
    sql.push_str(
        "INSERT INTO lightning_strikes \
         (strike_time, strike_timestamp, latitude, longitude, altitude, \
          polarity, mds, mcg, stations, region, delay_s) VALUES ",
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
    }

    let n = client
        .execute(sql.as_str(), &params)
        .await
        .context("batch insert")?;
    Ok(n)
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
