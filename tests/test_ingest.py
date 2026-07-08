"""Tests for frame -> strike record transformation in the ingestion service."""

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "frames.json"


@pytest.fixture
def producer():
    with patch("ingest.Producer", return_value=MagicMock()):
        from ingest import StrikeProducer

        return StrikeProducer()


def load_frames() -> list[str]:
    return json.loads(FIXTURES.read_text())


def test_decode_frame_produces_full_record(producer):
    strike = producer.decode_frame(load_frames()[0])

    assert set(strike) == {
        "strike_time",
        "strike_timestamp",
        "latitude",
        "longitude",
        "altitude",
        "polarity",
        "mds",
        "mcg",
        "status",
        "region",
        "station_count",
    }
    assert strike["station_count"] > 0
    # Timestamp must be ISO-8601 UTC.
    parsed = datetime.datetime.fromisoformat(strike["strike_timestamp"])
    assert parsed.tzinfo is not None


def test_decode_frame_rejects_bad_coordinates(producer):
    import lzw
    from tests.test_lzw import lzw_encode

    bad = json.dumps({"time": 1783502092045360000, "lat": 999.0, "lon": 0.0})
    frame = lzw_encode(bad)
    assert json.loads(lzw.lzw_decode(frame))["lat"] == 999.0

    with pytest.raises(ValueError):
        producer.decode_frame(frame)


def test_handle_frame_dead_letters_garbage(producer):
    producer.handle_frame("not an lzw json frame \x00\x01")
    assert producer.dead_lettered == 1
    assert producer.published == 0
    # The DLQ produce call carries the original frame.
    topics = [call.args[0] for call in producer.producer.produce.call_args_list]
    assert topics == ["lightning.strikes.dlq"]


def test_handle_frame_publishes_good_frames(producer):
    for frame in load_frames()[:5]:
        producer.handle_frame(frame)
    assert producer.published == 5
    assert producer.dead_lettered == 0


def test_normalize_epoch_units():
    from ingest import normalize_epoch

    expected = datetime.datetime(2026, 7, 8, tzinfo=datetime.UTC)
    seconds = int(expected.timestamp())
    for ts in (seconds, seconds * 10**3, seconds * 10**6, seconds * 10**9):
        assert normalize_epoch(ts) == expected
