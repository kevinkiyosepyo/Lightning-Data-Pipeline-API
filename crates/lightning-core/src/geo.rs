//! Geospatial helpers.

/// Earth's mean radius in kilometers.
pub const EARTH_RADIUS_KM: f64 = 6371.0;

/// Great-circle distance between two points in kilometers (Haversine).
pub fn haversine_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let (lat1, lat2) = (lat1.to_radians(), lat2.to_radians());
    let dlat = lat2 - lat1;
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (dlon / 2.0).sin().powi(2);
    2.0 * EARTH_RADIUS_KM * a.sqrt().asin()
}

/// Bounding box `(min_lat, max_lat, min_lon, max_lon)` around a point.
///
/// Approximate — meant as a cheap SQL prefilter before exact Haversine.
/// 1° latitude ≈ 111 km; longitude degrees shrink with `cos(lat)`.
pub fn bounding_box(lat: f64, lon: f64, radius_km: f64) -> (f64, f64, f64, f64) {
    let lat_delta = radius_km / 111.0;
    let cos_lat = lat.to_radians().cos().max(1e-6);
    let lon_delta = radius_km / (111.0 * cos_lat);
    (
        (lat - lat_delta).max(-90.0),
        (lat + lat_delta).min(90.0),
        (lon - lon_delta).max(-180.0),
        (lon + lon_delta).min(180.0),
    )
}

/// Great-circle initial bearing from point 1 to point 2, in degrees (0–360).
pub fn bearing_deg(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let (p1, p2) = (lat1.to_radians(), lat2.to_radians());
    let dl = (lon2 - lon1).to_radians();
    let y = dl.sin() * p2.cos();
    let x = p1.cos() * p2.sin() - p1.sin() * p2.cos() * dl.cos();
    (y.atan2(x).to_degrees() + 360.0) % 360.0
}

/// Largest angular gap between consecutive detecting stations, seen from the
/// strike. The standard fix-quality proxy for multilateration.
///
/// A gap under 90 degrees means the strike is well surrounded and the
/// position is tightly constrained. Above 180 degrees the strike lies
/// OUTSIDE the station ring, so its position is poorly constrained along the
/// open bearing no matter how many stations reported.
///
/// Returns 360.0 for fewer than two stations (no constraint at all).
pub fn azimuthal_gap_deg(lat: f64, lon: f64, stations: &[(f64, f64)]) -> f64 {
    if stations.len() < 2 {
        return 360.0;
    }
    let mut b: Vec<f64> = stations
        .iter()
        .map(|&(sl, so)| bearing_deg(lat, lon, sl, so))
        .collect();
    b.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));

    let mut max_gap = 360.0 - b[b.len() - 1] + b[0]; // wrap-around gap
    for w in b.windows(2) {
        max_gap = max_gap.max(w[1] - w[0]);
    }
    max_gap
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_distance() {
        assert!(haversine_km(32.88, -117.23, 32.88, -117.23).abs() < 1e-9);
    }

    #[test]
    fn ucsd_to_lax_about_180km() {
        // UCSD (32.8801, -117.2340) → LAX (33.9416, -118.4085) ≈ 163 km
        let d = haversine_km(32.8801, -117.2340, 33.9416, -118.4085);
        assert!((d - 163.0).abs() < 5.0, "got {d}");
    }

    #[test]
    fn bbox_clamps_to_valid_range() {
        let (a, b, c, d) = bounding_box(89.9, 179.9, 500.0);
        assert!(a >= -90.0 && b <= 90.0 && c >= -180.0 && d <= 180.0);
    }

    #[test]
    fn bearing_cardinal_directions() {
        assert!((bearing_deg(0.0, 0.0, 10.0, 0.0) - 0.0).abs() < 1e-6); // north
        assert!((bearing_deg(0.0, 0.0, 0.0, 10.0) - 90.0).abs() < 1e-6); // east
        assert!((bearing_deg(0.0, 0.0, -10.0, 0.0) - 180.0).abs() < 1e-6); // south
    }

    #[test]
    fn gap_surrounded_is_small() {
        // Four stations at N/E/S/W → perfectly surrounded, 90° gaps.
        let s = [(10.0, 0.0), (0.0, 10.0), (-10.0, 0.0), (0.0, -10.0)];
        let g = azimuthal_gap_deg(0.0, 0.0, &s);
        assert!((g - 90.0).abs() < 1.0, "got {g}");
    }

    #[test]
    fn gap_one_sided_is_large() {
        // All stations clustered north → huge open gap to the south.
        let s = [(10.0, -1.0), (10.0, 0.0), (10.0, 1.0)];
        let g = azimuthal_gap_deg(0.0, 0.0, &s);
        assert!(g > 180.0, "got {g}");
    }

    #[test]
    fn gap_too_few_stations() {
        assert_eq!(azimuthal_gap_deg(0.0, 0.0, &[]), 360.0);
        assert_eq!(azimuthal_gap_deg(0.0, 0.0, &[(1.0, 1.0)]), 360.0);
    }
}
