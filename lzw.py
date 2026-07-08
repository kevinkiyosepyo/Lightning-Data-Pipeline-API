"""LZW decoder for Blitzortung's WebSocket feed.

Blitzortung compresses each strike's JSON payload with LZW and transmits the
code stream as text, one Unicode code point per LZW code. Codes 0-255 are
literal characters; codes >= 256 are dictionary entries built incrementally
as the message is decoded, so no dictionary ever needs to be transmitted.

This replaces the earlier static substitution table, which was a snapshot of
the dictionary state for a "typical" message and therefore lossy whenever a
message built different entries (the source of the ~1% decode failures and
the misplaced-decimal-point longitude bug).
"""

from __future__ import annotations


def lzw_decode(data: str) -> str:
    """Decompress an LZW code stream where each character is one code.

    Raises ValueError on a code that cannot exist yet, which indicates a
    truncated or corrupted frame.
    """
    if not data:
        return ""

    dict_size = 256
    dictionary: dict[int, str] = {i: chr(i) for i in range(dict_size)}

    prev = data[0]
    result = [prev]

    for char in data[1:]:
        code = ord(char)
        if code in dictionary:
            entry = dictionary[code]
        elif code == dict_size:
            # The one special case in LZW: the encoder used a dictionary
            # entry it created on this very step (pattern of form cScSc).
            entry = prev + prev[0]
        else:
            raise ValueError(f"invalid LZW code {code} (dictionary size {dict_size})")

        result.append(entry)
        dictionary[dict_size] = prev + entry[0]
        dict_size += 1
        prev = entry

    return "".join(result)
