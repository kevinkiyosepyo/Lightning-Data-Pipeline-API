//! Nanosecond-epoch plausibility guard.
//!
//! The LZW path yields exact 19-digit epochs and never needs recovery. This
//! exists so a future protocol change cannot silently corrupt the table:
//! a damaged epoch (wrong magnitude) is rescaled to the power of ten that
//! lands nearest the wall clock — valid because strikes are live — and
//! anything still implausible is rejected by the caller.

use chrono::{DateTime, TimeZone, Utc};

/// Lower bound of a plausible ns epoch (2001-09-09).
pub const NS_EPOCH_MIN: i128 = 1_000_000_000_000_000_000;
/// Upper bound of a plausible ns epoch (2033-05-18).
pub const NS_EPOCH_MAX: i128 = 2_000_000_000_000_000_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Recovery {
    /// Epoch was already a plausible ns value; used as-is.
    Exact,
    /// Epoch was rescaled by the given power of ten to land near `now`.
    Rescaled { power: i32 },
}

/// Convert a raw epoch to a UTC datetime, rescaling if its magnitude is off.
///
/// Returns `None` only for non-positive input.
pub fn normalize(raw: i128, now: DateTime<Utc>) -> Option<(DateTime<Utc>, Recovery)> {
    if raw <= 0 {
        return None;
    }
    if raw > NS_EPOCH_MIN && raw < NS_EPOCH_MAX {
        return Some((ns_to_datetime(raw), Recovery::Exact));
    }

    let now_ns = now.timestamp_nanos_opt()? as i128;
    let mut best: Option<(i128, i128, i32)> = None; // (candidate, err, power)
    for k in -6i32..=12 {
        let cand = if k >= 0 {
            raw.checked_mul(10i128.pow(k as u32))?
        } else {
            raw / 10i128.pow((-k) as u32)
        };
        if cand <= 0 {
            continue;
        }
        let err = (cand - now_ns).abs();
        if best.is_none_or(|(_, e, _)| err < e) {
            best = Some((cand, err, k));
        }
    }
    let (cand, _, power) = best?;
    Some((ns_to_datetime(cand), Recovery::Rescaled { power }))
}

fn ns_to_datetime(ns: i128) -> DateTime<Utc> {
    let secs = (ns / 1_000_000_000) as i64;
    let nanos = (ns % 1_000_000_000) as u32;
    Utc.timestamp_opt(secs, nanos).single().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn now() -> DateTime<Utc> {
        // Fixed "now" matching the fixture capture window so tests are deterministic.
        Utc.timestamp_opt(1_789_361_700, 0).single().unwrap()
    }

    #[test]
    fn exact_epoch_passes_through() {
        let raw = 1_789_361_697_072_402_200i128;
        let (dt, rec) = normalize(raw, now()).unwrap();
        assert_eq!(rec, Recovery::Exact);
        assert_eq!(dt.timestamp(), 1_789_361_697);
        assert_eq!(dt.timestamp_subsec_nanos(), 72_402_200);
    }

    #[test]
    fn recovers_one_lost_trailing_zero() {
        // Real corrupt value from the old decoder: lost 1 trailing zero → was "1975".
        let (dt, rec) = normalize(178_936_169_688_953_400, now()).unwrap();
        assert_eq!(rec, Recovery::Rescaled { power: 1 });
        assert!((dt - now()).abs() < Duration::minutes(2));
    }

    #[test]
    fn recovers_two_lost_trailing_zeros() {
        // Real corrupt value from the old decoder: lost 2 → was "2537".
        let (dt, rec) = normalize(17_893_616_968_894_060, now()).unwrap();
        assert_eq!(rec, Recovery::Rescaled { power: 2 });
        assert!((dt - now()).abs() < Duration::minutes(2));
    }

    #[test]
    fn recovers_seconds_and_millis() {
        let (dt, _) = normalize(1_789_361_696, now()).unwrap();
        assert!((dt - now()).abs() < Duration::minutes(2));
        let (dt, _) = normalize(1_789_361_696_889, now()).unwrap();
        assert!((dt - now()).abs() < Duration::minutes(2));
    }

    #[test]
    fn rejects_non_positive() {
        assert!(normalize(0, now()).is_none());
        assert!(normalize(-5, now()).is_none());
    }
}
