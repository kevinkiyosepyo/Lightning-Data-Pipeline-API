//! Row → DTO mapping and shared response types.

use chrono::{DateTime, NaiveDateTime, Utc};
use serde::Serialize;
use tokio_postgres::Row;

/// One stored strike as served by the API. Field names match the Python
/// API exactly so the dashboard and any external consumers are unaffected.
#[derive(Debug, Serialize)]
pub struct StrikeOut {
    pub id: i64,
    pub strike_time: i64,
    pub strike_timestamp: NaiveDateTime,
    pub latitude: f64,
    pub longitude: f64,
    pub altitude: Option<i32>,
    pub polarity: Option<String>,
    pub mds: Option<i32>,
    pub mcg: Option<i32>,
    pub stations: Option<i16>,
    pub region: Option<i16>,
    pub delay_s: Option<f32>,
    pub inserted_at: NaiveDateTime,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub distance_km: Option<f64>,
}

impl From<&Row> for StrikeOut {
    fn from(r: &Row) -> Self {
        Self {
            id: r.get("id"),
            strike_time: r.get("strike_time"),
            strike_timestamp: r.get("strike_timestamp"),
            latitude: r.get("latitude"),
            longitude: r.get("longitude"),
            altitude: r.get("altitude"),
            polarity: r.get("polarity"),
            mds: r.get("mds"),
            mcg: r.get("mcg"),
            stations: r.get("stations"),
            region: r.get("region"),
            delay_s: r.get("delay_s"),
            inserted_at: r.get("inserted_at"),
            distance_km: None,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct StrikeStats {
    pub total_strikes: i64,
    pub time_range_start: Option<NaiveDateTime>,
    pub time_range_end: Option<NaiveDateTime>,
    pub avg_latitude: Option<f64>,
    pub avg_longitude: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct IngestionStats {
    pub total_received: i32,
    pub total_stored: i32,
    pub total_failed: i32,
    pub last_strike_time: Option<NaiveDateTime>,
    pub updated_at: NaiveDateTime,
    pub success_rate: f64,
}

#[derive(Debug, Serialize)]
pub struct Health {
    pub status: &'static str,
    pub database: &'static str,
    pub total_strikes: i64,
    pub service: &'static str,
    pub version: &'static str,
}

/// Parse an ISO-8601 query param into a naive UTC timestamp.
/// Accepts `2026-09-14T05:00:00`, `...Z`, or `...+00:00`.
pub fn parse_ts(s: &str) -> Option<NaiveDateTime> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(s) {
        return Some(dt.with_timezone(&Utc).naive_utc());
    }
    NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S"))
        .or_else(|_| NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S"))
        .ok()
}
