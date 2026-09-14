"""Prove Blitzortung frames are LZW-compressed strings: decode N live frames, validate JSON."""
import json
import sys
import time

import websocket


def lzw_decode(s: str) -> str:
    """Standard Blitzortung LZW: each char is a code; >=256 are dictionary phrases."""
    if not s:
        return ""
    dict_ = {}
    curr_char = s[0]
    old_phrase = curr_char
    out = [curr_char]
    code = 256
    for ch in s[1:]:
        cc = ord(ch)
        if cc < 256:
            phrase = ch
        else:
            phrase = dict_.get(cc, old_phrase + curr_char)
        out.append(phrase)
        curr_char = phrase[0]
        dict_[code] = old_phrase + curr_char
        code += 1
        old_phrase = phrase
    return "".join(out)


N_TARGET = 45
frames = []
opcodes = []


def on_data(ws, data, opcode, fin):
    opcodes.append(opcode)
    frames.append(data)
    if len(frames) >= N_TARGET:
        ws.close()


def on_open(ws):
    ws.send(json.dumps({"a": 111}))


ws = websocket.WebSocketApp(
    "wss://ws7.blitzortung.org/", on_open=on_open, on_data=on_data
)
t0 = time.time()
ws.run_forever(ping_interval=30, ping_timeout=10)
elapsed = time.time() - t0

print(f"collected {len(frames)} frames in {elapsed:.1f}s; opcodes={sorted(set(opcodes))}")

ok = bad_json = bad_time = 0
digit_hist = {}
sample_shown = 0
for f in frames:
    if isinstance(f, bytes):
        f = f.decode("utf-8", errors="replace")
    decoded = lzw_decode(f)
    try:
        obj = json.loads(decoded)
    except Exception:
        bad_json += 1
        continue
    ts = obj.get("time")
    nd = len(str(ts)) if ts else 0
    digit_hist[nd] = digit_hist.get(nd, 0) + 1
    if not ts or nd != 19:
        bad_time += 1
        continue
    ok += 1
    if sample_shown < 3:
        keys = sorted(obj.keys())
        sig = obj.get("sig", [])
        print(f"  sample: time={ts} lat={obj.get('lat')} lon={obj.get('lon')} "
              f"stations={len(sig)} keys={keys}")
        sample_shown += 1

print(f"\nRESULT: {ok}/{len(frames)} perfect JSON with 19-digit ns epoch")
print(f"bad_json={bad_json} bad_time={bad_time} digit_histogram={digit_hist}")
sys.exit(0 if ok == len(frames) and len(frames) > 0 else 1)
