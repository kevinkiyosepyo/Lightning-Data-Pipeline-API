"""Decoder tests.

tests/fixtures/frames.json holds real frames captured from the Blitzortung
WebSocket feed — the decoder is exercised against actual production data,
not synthetic examples.
"""

import json
from pathlib import Path

import pytest

from lzw import lzw_decode

FIXTURES = Path(__file__).parent / "fixtures" / "frames.json"


def load_frames() -> list[str]:
    return json.loads(FIXTURES.read_text())


def lzw_encode(text: str) -> str:
    """Reference LZW encoder, used to round-trip arbitrary strings in tests."""
    dict_size = 256
    dictionary = {chr(i): i for i in range(dict_size)}
    current = ""
    out = []
    for char in text:
        candidate = current + char
        if candidate in dictionary:
            current = candidate
        else:
            out.append(chr(dictionary[current]))
            dictionary[candidate] = dict_size
            dict_size += 1
            current = char
    if current:
        out.append(chr(dictionary[current]))
    return "".join(out)


def test_empty_input():
    assert lzw_decode("") == ""


def test_plain_ascii_passthrough():
    # A string with no repeated substrings encodes to itself.
    assert lzw_decode("abc") == "abc"


def test_roundtrip_repetitive_text():
    text = '{"lat": 12.34, "lon": 56.78}' * 20
    assert lzw_decode(lzw_encode(text)) == text


def test_self_referential_code():
    # The cScSc edge case: the decoder receives a code it is about to define.
    text = "ababababa"
    assert lzw_decode(lzw_encode(text)) == text


def test_invalid_code_raises():
    with pytest.raises(ValueError):
        lzw_decode("a" + chr(300))  # code 300 cannot exist after one literal


@pytest.mark.parametrize("frame_index", range(len(load_frames())))
def test_real_frames_decode_to_valid_json(frame_index):
    frame = load_frames()[frame_index]
    strike = json.loads(lzw_decode(frame))

    assert -90 <= strike["lat"] <= 90
    assert -180 <= strike["lon"] <= 180
    assert strike["time"] > 10**17  # nanosecond epoch
    assert isinstance(strike.get("sig", []), list)


def test_all_real_frames_decode():
    frames = load_frames()
    decoded = [json.loads(lzw_decode(f)) for f in frames]
    assert len(decoded) == len(frames)
