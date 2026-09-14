//! Strike payload model and decode-to-record pipeline.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::epoch::{self, Recovery};
use crate::geo;
use crate::lzw;

/// Blitzortung caps the `sig` array at this many station detections. A strike
/// reporting exactly this many was almost certainly detected by more, so the
/// count is right-censored and must not be read as "detected by exactly 40".
pub const STATION_CAP: usize = 40;

/// One station's detection of a strike, as sent inside `sig[]`.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct StationHit {
    /// Blitzortung station id.
    pub sta: i32,
    /// Station-local arrival time offset (feed units, not an epoch).
    pub time: i64,
    pub lat: f64,
    pub lon: f64,
    #[serde(default)]
    pub alt: Option<f64>,
    #[serde(default)]
    pub status: Option<i32>,
}

/// Raw Blitzortung payload as it appears after LZW decompression.
///
/// Every key the feed is known to send is modeled here; `#[serde(flatten)]`
/// on `extra` captures anything new so a protocol addition is visible in the
/// logs instead of silently dropped.
#[derive(Debug, Deserialize)]
pub struct RawStrike {
    pub time: i128,
    pub lat: f64,
    pub lon: f64,
    pub alt: Option<f64>,
    pub pol: Option<serde_json::Value>,
    pub mds: Option<f64>,
    pub mcg: Option<f64>,
    pub region: Option<f64>,
    pub delay: Option<f64>,
    /// Solver status flag (0/1/2 observed).
    pub status: Option<f64>,
    /// "Corrected" coordinates. Observed as 0 on every live frame; kept so
    /// we notice if the feed ever starts populating them.
    pub latc: Option<f64>,
    pub lonc: Option<f64>,
    #[serde(default)]
    pub sig: Vec<StationHit>,
    #[serde(flatten)]
    pub extra: std::collections::BTreeMap<String, serde_json::Value>,
}

/// Normalized strike ready to store or serve.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Strike {
    /// Original epoch value from the feed (nanoseconds).
    pub strike_time: i64,
    pub strike_timestamp: DateTime<Utc>,
    pub latitude: f64,
    pub longitude: f64,
    pub altitude: Option<i32>,
    pub polarity: Option<String>,
    pub mds: Option<i32>,
    pub mcg: Option<i32>,
    pub stations: Option<i16>,
    pub region: Option<i16>,
    pub delay_s: Option<f32>,
    /// Solver status flag from the feed.
    pub status: Option<i16>,
    /// True when `stations == STATION_CAP`, i.e. the count is right-censored.
    pub stations_censored: bool,
    /// Largest angular gap between detecting stations, degrees. Lower is a
    /// better-constrained fix; > 180° means the strike sits outside the ring.
    pub azimuthal_gap_deg: Option<f32>,
    /// Distance to the closest detecting station, km.
    pub nearest_station_km: Option<f32>,
    /// Distance to the farthest detecting station, km.
    pub farthest_station_km: Option<f32>,
    /// Full per-station detection list as received.
    pub station_hits: Vec<StationHit>,
}

impl Strike {
    /// Fix-quality score in 0–100, derived from station geometry.
    ///
    /// Driven by azimuthal gap (how well the strike is surrounded), which the
    /// live feed shows is only weakly related to raw station count: strikes
    /// with ≥38 stations still had a median gap of 139°, so counting
    /// detectors alone overstates confidence.
    pub fn confidence(&self) -> Option<f32> {
        let gap = self.azimuthal_gap_deg?;
        // 0° gap → 100; 180°+ → 0. Linear in between.
        let geom = ((180.0 - gap.min(180.0)) / 180.0) * 100.0;
        // Small bonus for redundancy, capped so it can't mask bad geometry.
        let n = self.stations.unwrap_or(0) as f32;
        let redundancy = (n / STATION_CAP as f32).min(1.0) * 15.0;
        Some((geom * 0.85 + redundancy).clamp(0.0, 100.0))
    }
}

#[derive(Debug, thiserror::Error)]
pub enum DecodeError {
    #[error("frame is not valid JSON after LZW decode: {0}")]
    Json(#[from] serde_json::Error),
    #[error("non-positive epoch {0}")]
    BadEpoch(i128),
    #[error("coordinates out of range: lat={lat} lon={lon}")]
    BadCoordinates { lat: f64, lon: f64 },
    #[error("strike time {ts} is {drift_s}s from now; rejecting as implausible")]
    ImplausibleTime { ts: DateTime<Utc>, drift_s: i64 },
    #[error("epoch {0} does not fit in i64")]
    EpochOverflow(i128),
}

/// Maximum |strike_time − now| we accept. Anything beyond means the epoch
/// survived recovery with a garbage value.
pub const MAX_DRIFT_SECS: i64 = 86_400;

/// Decode one WebSocket frame into a validated [`Strike`].
///
/// Returns the strike plus whether epoch recovery had to fire (for metrics).
pub fn decode_frame(frame: &str, now: DateTime<Utc>) -> Result<(Strike, Recovery), DecodeError> {
    let json = lzw::decode(frame);
    let raw: RawStrike = serde_json::from_str(&json)?;
    from_raw(raw, now)
}

pub fn from_raw(raw: RawStrike, now: DateTime<Utc>) -> Result<(Strike, Recovery), DecodeError> {
    let (ts, recovery) = epoch::normalize(raw.time, now).ok_or(DecodeError::BadEpoch(raw.time))?;
    if !(-90.0..=90.0).contains(&raw.lat) || !(-180.0..=180.0).contains(&raw.lon) {
        return Err(DecodeError::BadCoordinates {
            lat: raw.lat,
            lon: raw.lon,
        });
    }

    let drift_s = (ts - now).num_seconds().abs();
    if drift_s > MAX_DRIFT_SECS {
        return Err(DecodeError::ImplausibleTime { ts, drift_s });
    }

    let strike_time = i64::try_from(raw.time).map_err(|_| DecodeError::EpochOverflow(raw.time))?;

    let polarity = raw.pol.map(|v| match v {
        serde_json::Value::String(s) => s,
        other => other.to_string(),
    });

    // Station geometry: only meaningful when we have real coordinates.
    let coords: Vec<(f64, f64)> = raw
        .sig
        .iter()
        .filter(|s| s.lat.abs() <= 90.0 && s.lon.abs() <= 180.0)
        .map(|s| (s.lat, s.lon))
        .collect();

    let (gap, near, far) = if coords.is_empty() {
        (None, None, None)
    } else {
        let gap = geo::azimuthal_gap_deg(raw.lat, raw.lon, &coords) as f32;
        let mut dists: Vec<f64> = coords
            .iter()
            .map(|&(la, lo)| geo::haversine_km(raw.lat, raw.lon, la, lo))
            .collect();
        dists.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        (
            Some(gap),
            Some(dists[0] as f32),
            Some(dists[dists.len() - 1] as f32),
        )
    };

    let n_stations = raw.sig.len();

    Ok((
        Strike {
            strike_time,
            strike_timestamp: ts,
            latitude: raw.lat,
            longitude: raw.lon,
            altitude: raw.alt.map(|v| v as i32),
            polarity,
            mds: raw.mds.map(|v| v as i32),
            mcg: raw.mcg.map(|v| v as i32),
            stations: Some(n_stations.min(i16::MAX as usize) as i16),
            region: raw.region.map(|v| v as i16),
            delay_s: raw.delay.map(|v| v as f32),
            status: raw.status.map(|v| v as i16),
            stations_censored: n_stations >= STATION_CAP,
            azimuthal_gap_deg: gap,
            nearest_station_km: near,
            farthest_station_km: far,
            station_hits: raw.sig,
        },
        recovery,
    ))
}
