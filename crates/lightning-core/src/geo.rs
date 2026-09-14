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
}
