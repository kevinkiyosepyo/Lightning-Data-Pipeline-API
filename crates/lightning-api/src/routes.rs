//! Route handlers. One function per endpoint, mirroring the Python API's
//! paths, parameters, and response shapes exactly.

use std::collections::HashMap;

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{Html, IntoResponse, Response},
    Json,
};
use chrono::{Duration, NaiveDateTime, Utc};
use lightning_core::geo;
use serde::Deserialize;
use serde_json::json;
use tokio_postgres::types::ToSql;
use tracing::error;

use crate::models::{parse_ts, Health, IngestionStats, StationOut, StrikeOut, StrikeStats};
use crate::AppState;

pub struct ApiError(StatusCode, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "detail": self.1 }))).into_response()
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(e: anyhow::Error) -> Self {
        error!(error = %e, "request failed");
        ApiError(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Database error: {e}"),
        )
    }
}
impl From<deadpool_postgres::PoolError> for ApiError {
    fn from(e: deadpool_postgres::PoolError) -> Self {
        anyhow::Error::from(e).into()
    }
}
impl From<tokio_postgres::Error> for ApiError {
    fn from(e: tokio_postgres::Error) -> Self {
        anyhow::Error::from(e).into()
    }
}

fn bad(msg: impl Into<String>) -> ApiError {
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, msg.into())
}

fn clamp_limit(v: Option<i64>, default: i64) -> i64 {
    v.unwrap_or(default).clamp(1, 1000)
}
fn clamp_minutes(v: Option<i64>) -> i64 {
    v.unwrap_or(60).clamp(1, 1440)
}

pub async fn root() -> Json<serde_json::Value> {
    Json(json!({
        "message": "Lightning Strike API",
        "version": env!("CARGO_PKG_VERSION"),
        "runtime": "rust/axum",
        "endpoints": {
            "/strikes": "Get lightning strikes with filters",
            "/strikes/recent": "Get most recent strikes",
            "/strikes/nearby": "Get strikes near a location",
            "/strikes/stats": "Get strike statistics",
            "/health": "Health check",
            "/ingestion/stats": "Ingestion service statistics",
            "/live": "Live dashboard"
        }
    }))
}

pub async fn health(State(st): State<AppState>) -> Result<Json<Health>, ApiError> {
    let c = st.pool.get().await?;
    let row = c
        .query_one("SELECT COUNT(*) AS count FROM lightning_strikes", &[])
        .await?;
    Ok(Json(Health {
        status: "healthy",
        database: "connected",
        total_strikes: row.get("count"),
        service: "lightning-api",
        version: env!("CARGO_PKG_VERSION"),
    }))
}

#[derive(Deserialize)]
pub struct StrikesQuery {
    limit: Option<i64>,
    offset: Option<i64>,
    since: Option<String>,
    until: Option<String>,
    min_lat: Option<f64>,
    max_lat: Option<f64>,
    min_lon: Option<f64>,
    max_lon: Option<f64>,
}

pub async fn strikes(
    State(st): State<AppState>,
    Query(q): Query<StrikesQuery>,
) -> Result<Json<Vec<StrikeOut>>, ApiError> {
    let mut sql = String::from("SELECT * FROM lightning_strikes WHERE 1=1");
    let mut params: Vec<Box<dyn ToSql + Sync + Send>> = Vec::new();

    let mut push = |sql: &mut String, clause: &str, v: Box<dyn ToSql + Sync + Send>| {
        params.push(v);
        sql.push_str(&format!(" AND {clause} ${}", params.len()));
    };

    if let Some(s) = q.since.as_deref() {
        let ts = parse_ts(s).ok_or_else(|| bad("since: invalid timestamp"))?;
        push(&mut sql, "strike_timestamp >=", Box::new(ts));
    }
    if let Some(s) = q.until.as_deref() {
        let ts = parse_ts(s).ok_or_else(|| bad("until: invalid timestamp"))?;
        push(&mut sql, "strike_timestamp <=", Box::new(ts));
    }
    for (name, val, col, op) in [
        ("min_lat", q.min_lat, "latitude", ">="),
        ("max_lat", q.max_lat, "latitude", "<="),
        ("min_lon", q.min_lon, "longitude", ">="),
        ("max_lon", q.max_lon, "longitude", "<="),
    ] {
        if let Some(v) = val {
            let lim = if col == "latitude" { 90.0 } else { 180.0 };
            if v.abs() > lim {
                return Err(bad(format!("{name}: out of range")));
            }
            push(&mut sql, &format!("{col} {op}"), Box::new(v));
        }
    }

    let limit = clamp_limit(q.limit, 100);
    let offset = q.offset.unwrap_or(0).max(0);
    params.push(Box::new(limit));
    params.push(Box::new(offset));
    sql.push_str(&format!(
        " ORDER BY strike_timestamp DESC LIMIT ${} OFFSET ${}",
        params.len() - 1,
        params.len()
    ));

    let refs: Vec<&(dyn ToSql + Sync)> = params.iter().map(|p| p.as_ref() as _).collect();
    let c = st.pool.get().await?;
    let rows = c.query(sql.as_str(), &refs).await?;
    Ok(Json(rows.iter().map(StrikeOut::from).collect()))
}

#[derive(Deserialize)]
pub struct RecentQuery {
    minutes: Option<i64>,
    limit: Option<i64>,
}

/// Ordered by `inserted_at`, not `strike_timestamp`: a single bad far-future
/// strike time must never pin the top of a live feed.
pub async fn recent(
    State(st): State<AppState>,
    Query(q): Query<RecentQuery>,
) -> Result<Json<Vec<StrikeOut>>, ApiError> {
    let since = Utc::now().naive_utc() - Duration::minutes(clamp_minutes(q.minutes));
    let limit = clamp_limit(q.limit, 100);
    let c = st.pool.get().await?;
    let rows = c
        .query(
            "SELECT * FROM lightning_strikes WHERE inserted_at >= $1 \
             ORDER BY inserted_at DESC LIMIT $2",
            &[&since, &limit],
        )
        .await?;
    Ok(Json(rows.iter().map(StrikeOut::from).collect()))
}

#[derive(Deserialize)]
pub struct NearbyQuery {
    lat: f64,
    lon: f64,
    radius: Option<f64>,
    minutes: Option<i64>,
    limit: Option<i64>,
}

pub async fn nearby(
    State(st): State<AppState>,
    Query(q): Query<NearbyQuery>,
) -> Result<Json<Vec<StrikeOut>>, ApiError> {
    if q.lat.abs() > 90.0 || q.lon.abs() > 180.0 {
        return Err(bad("lat/lon out of range"));
    }
    let radius = q.radius.unwrap_or(50.0).clamp(1.0, 500.0);
    let limit = clamp_limit(q.limit, 100);
    let since = Utc::now().naive_utc() - Duration::minutes(clamp_minutes(q.minutes));
    let (min_lat, max_lat, min_lon, max_lon) = geo::bounding_box(q.lat, q.lon, radius);
    let prefetch = limit * 2;

    let c = st.pool.get().await?;
    let rows = c
        .query(
            "SELECT * FROM lightning_strikes \
             WHERE strike_timestamp >= $1 \
               AND latitude BETWEEN $2 AND $3 \
               AND longitude BETWEEN $4 AND $5 \
             ORDER BY strike_timestamp DESC LIMIT $6",
            &[&since, &min_lat, &max_lat, &min_lon, &max_lon, &prefetch],
        )
        .await?;

    let mut out: Vec<StrikeOut> = rows
        .iter()
        .map(StrikeOut::from)
        .filter_map(|mut s| {
            let d = geo::haversine_km(q.lat, q.lon, s.latitude, s.longitude);
            (d <= radius).then(|| {
                s.distance_km = Some((d * 100.0).round() / 100.0);
                s
            })
        })
        .collect();
    out.sort_by(|a, b| a.distance_km.partial_cmp(&b.distance_km).unwrap());
    out.truncate(limit as usize);
    Ok(Json(out))
}

pub async fn stats(
    State(st): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<StrikeStats>, ApiError> {
    let mut sql = String::from(
        "SELECT COUNT(*) AS total_strikes, \
                MIN(strike_timestamp) AS time_range_start, \
                MAX(strike_timestamp) AS time_range_end, \
                AVG(latitude) AS avg_latitude, \
                AVG(longitude) AS avg_longitude \
         FROM lightning_strikes WHERE 1=1",
    );
    let mut params: Vec<NaiveDateTime> = Vec::new();
    if let Some(s) = q.get("since") {
        params.push(parse_ts(s).ok_or_else(|| bad("since: invalid timestamp"))?);
        sql.push_str(&format!(" AND strike_timestamp >= ${}", params.len()));
    }
    if let Some(s) = q.get("until") {
        params.push(parse_ts(s).ok_or_else(|| bad("until: invalid timestamp"))?);
        sql.push_str(&format!(" AND strike_timestamp <= ${}", params.len()));
    }
    let refs: Vec<&(dyn ToSql + Sync)> = params.iter().map(|p| p as _).collect();
    let c = st.pool.get().await?;
    let r = c.query_one(sql.as_str(), &refs).await?;
    Ok(Json(StrikeStats {
        total_strikes: r.get("total_strikes"),
        time_range_start: r.get("time_range_start"),
        time_range_end: r.get("time_range_end"),
        avg_latitude: r.get("avg_latitude"),
        avg_longitude: r.get("avg_longitude"),
    }))
}

pub async fn ingestion_stats(State(st): State<AppState>) -> Result<Json<IngestionStats>, ApiError> {
    let c = st.pool.get().await?;
    let r = c
        .query_opt("SELECT * FROM ingestion_stats LIMIT 1", &[])
        .await?
        .ok_or_else(|| ApiError(StatusCode::NOT_FOUND, "No ingestion stats found".into()))?;
    let received: i32 = r.get("total_received");
    let stored: i32 = r.get("total_stored");
    let rate = if received > 0 {
        stored as f64 / received as f64 * 100.0
    } else {
        0.0
    };
    Ok(Json(IngestionStats {
        total_received: received,
        total_stored: stored,
        total_failed: r.get("total_failed"),
        last_strike_time: r.get("last_strike_time"),
        updated_at: r.get("updated_at"),
        success_rate: (rate * 100.0).round() / 100.0,
    }))
}

/// Dashboard, compiled into the binary so the image ships one artifact.
pub async fn live() -> Html<&'static str> {
    Html(include_str!("../../../live.html"))
}

/// Per-station detections behind one strike — the raw multilateration inputs.
pub async fn strike_stations(
    State(st): State<AppState>,
    axum::extract::Path(id): axum::extract::Path<i64>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let c = st.pool.get().await?;
    let strike = c
        .query_opt(
            "SELECT latitude, longitude, stations, stations_censored, \
                    azimuthal_gap_deg, nearest_station_km, farthest_station_km \
             FROM lightning_strikes WHERE id = $1",
            &[&id],
        )
        .await?
        .ok_or_else(|| ApiError(StatusCode::NOT_FOUND, format!("strike {id} not found")))?;

    let (slat, slon): (f64, f64) = (strike.get("latitude"), strike.get("longitude"));
    let rows = c
        .query(
            "SELECT station_id, station_time, latitude, longitude, altitude, status \
             FROM strike_stations WHERE strike_id = $1 ORDER BY station_time",
            &[&id],
        )
        .await?;

    let stations: Vec<StationOut> = rows
        .iter()
        .map(|r| {
            let la: Option<f64> = r.get("latitude");
            let lo: Option<f64> = r.get("longitude");
            let (d, b) = match (la, lo) {
                (Some(la), Some(lo)) => (
                    Some((geo::haversine_km(slat, slon, la, lo) * 100.0).round() / 100.0),
                    Some((geo::bearing_deg(slat, slon, la, lo) * 10.0).round() / 10.0),
                ),
                _ => (None, None),
            };
            StationOut {
                station_id: r.get("station_id"),
                station_time: r.get("station_time"),
                latitude: la,
                longitude: lo,
                altitude: r.get("altitude"),
                status: r.get("status"),
                distance_km: d,
                bearing_deg: b,
            }
        })
        .collect();

    let gap: Option<f32> = strike.get("azimuthal_gap_deg");
    let n: Option<i16> = strike.get("stations");
    Ok(Json(json!({
        "strike_id": id,
        "latitude": slat,
        "longitude": slon,
        "stations_reported": n,
        "stations_stored": stations.len(),
        "stations_censored": strike.get::<_, Option<bool>>("stations_censored"),
        "azimuthal_gap_deg": gap,
        "nearest_station_km": strike.get::<_, Option<f32>>("nearest_station_km"),
        "farthest_station_km": strike.get::<_, Option<f32>>("farthest_station_km"),
        "confidence": crate::models::public_confidence(gap, n),
        "stations": stations,
    })))
}
