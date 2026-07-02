"""Unit tests for the Blitzortung decoder. No database required."""
import datetime

import pytest

from ingest import BlitzortungDecoder, normalize_epoch


@pytest.fixture
def decoder():
    return BlitzortungDecoder()


class TestNormalizeEpoch:
    BASE = datetime.datetime(2026, 7, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def test_seconds(self):
        ts = int(self.BASE.timestamp())
        assert normalize_epoch(ts) == self.BASE

    def test_milliseconds(self):
        ts = int(self.BASE.timestamp() * 1_000)
        assert normalize_epoch(ts) == self.BASE

    def test_microseconds(self):
        ts = int(self.BASE.timestamp() * 1_000_000)
        assert normalize_epoch(ts) == self.BASE

    def test_nanoseconds(self):
        ts = int(self.BASE.timestamp() * 1_000_000_000)
        assert normalize_epoch(ts) == self.BASE

    def test_returns_utc_aware(self):
        dt = normalize_epoch(1_700_000_000)
        assert dt.tzinfo == datetime.timezone.utc


class TestFixLongitude:
    def test_normal_value_untouched(self, decoder):
        assert decoder._fix_longitude("-118.24") == pytest.approx(-118.24)

    def test_eight_digit_value_corrected(self, decoder):
        # 11824368 -> 11.824368
        assert decoder._fix_longitude("11824368") == pytest.approx(11.824368)

    def test_seven_digit_value_corrected(self, decoder):
        assert decoder._fix_longitude("1182436") == pytest.approx(11.82436)

    def test_negative_sign_preserved(self, decoder):
        assert decoder._fix_longitude("-11824368") == pytest.approx(-11.824368)

    def test_six_digit_value_searches_for_valid_split(self, decoder):
        result = decoder._fix_longitude("118243")
        assert result is not None
        assert abs(result) <= 180

    def test_garbage_returns_none(self, decoder):
        assert decoder._fix_longitude("not-a-number") is None

    def test_no_unbound_local_on_any_input(self, decoder):
        # Regression: previously a bare `except` could mask an unassigned branch
        for s in ["999999", "123456789012", "-999999"]:
            decoder._fix_longitude(s)  # must not raise


class TestExtractFields:
    def test_valid_payload(self, decoder):
        text = '{"time": 1700000000123, "lat": 34.05, "lon": -118.24, "alt": 0, "mds": 5000, "mcg": 120}'
        result = decoder._extract_fields(text)
        assert result is not None
        assert result['lat'] == pytest.approx(34.05)
        assert result['lon'] == pytest.approx(-118.24)
        assert result['mds'] == 5000
        assert result['mcg'] == 120
        assert result['timestamp'].tzinfo == datetime.timezone.utc

    def test_missing_time_returns_none(self, decoder):
        assert decoder._extract_fields('{"lat": 34.0, "lon": -118.0}') is None

    def test_missing_coordinates_returns_none(self, decoder):
        assert decoder._extract_fields('{"time": 1700000000}') is None


class TestDecode:
    def test_substitution_decoding(self, decoder):
        # \xc4\x89 -> '1', \xc4\x8a -> '2', \xc4\x87 -> '.'
        raw = b'{"time": \xc4\x891700000000, "lat": 34\xc4\x8705, "lon": -118\xc4\x872\xc4\x8a}'
        result = decoder.decode(raw)
        assert result is not None
        assert result['lat'] == pytest.approx(34.05)
        assert result['lon'] == pytest.approx(-118.22)

    def test_invalid_coordinates_rejected(self, decoder):
        raw = b'{"time": 1700000000, "lat": 95.0, "lon": 0.0}'
        assert decoder.decode(raw) is None
        assert decoder.failed_decodes == 1

    def test_stats_counters(self, decoder):
        decoder.decode(b'{"time": 1700000000, "lat": 10.0, "lon": 20.0}')
        decoder.decode(b'garbage')
        assert decoder.sample_count == 2
        assert decoder.successful_decodes == 1
        assert decoder.failed_decodes == 1


class TestCorruptedFeedData:
    """Regression tests for real corruption patterns seen on the live feed."""

    def test_seventeen_digit_timestamp_clamped(self):
        # Observed live: extra digit made a microsecond epoch decode to year 2534
        dt = normalize_epoch(17829648891540200)
        assert dt.year == 2026

    def test_eighteen_digit_timestamp_clamped(self):
        # Observed live: 100-nanosecond epochs decoded to 1975 under magnitude buckets
        dt = normalize_epoch(178296498997912070)
        assert dt.year == 2026

    def test_pol_does_not_swallow_adjacent_fields(self):
        d = BlitzortungDecoder()
        text = 'time: 1700000000 lat: 42.93 lon: 10.16 alt: 8 polmds: 6399mcg: 269'
        result = d._extract_fields(text)
        assert result is not None
        assert result['pol'] is None
        assert result['mds'] == 6399
        assert result['mcg'] == 269
