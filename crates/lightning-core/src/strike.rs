//! Strike payload model and decode-to-record pipeline.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::epoch::{self, Recovery};
use crate::lzw;

/// Raw Blitzortung payload as it appears after LZW decompression.
///
/// Only the fields we use are modeled; unknown keys are ignored. `sig` is a
/// list of station detections — we only need its length.
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
    #[serde(default)]
    pub sig: Vec<serde_json::Value>,
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
            stations: Some(raw.sig.len().min(i16::MAX as usize) as i16),
            region: raw.region.map(|v| v as i16),
            delay_s: raw.delay.map(|v| v as f32),
        },
        recovery,
    ))
}
