"""Audit what the feed actually sends vs what we store.

Captures live frames, enumerates the COMPLETE key space across all of them,
inspects the sig[] array structure, and checks whether station counts are
being truncated upstream.
"""
import json
import sys
from collections import Counter, defaultdict

import websocket

sys.path.insert(0, "/app")


def lzw_decode(s: str) -> str:
    if not s:
        return ""
    d = {}
    cur = s[0]
    old = cur
    out = [cur]
    code = 256
    for ch in s[1:]:
        cc = ord(ch)
        phrase = ch if cc < 256 else d.get(cc, old + cur)
        out.append(phrase)
        cur = phrase[0]
        d[code] = old + cur
        code += 1
        old = phrase
    return "".join(out)


N = 400
frames = []


def on_data(ws, data, opcode, fin):
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    frames.append(data)
    if len(frames) >= N:
        ws.close()


def on_open(ws):
    ws.send(json.dumps({"a": 111}))


ws = websocket.WebSocketApp("wss://ws7.blitzortung.org/", on_open=on_open, on_data=on_data)
ws.run_forever(ping_interval=30, ping_timeout=10)

print(f"captured {len(frames)} frames\n")

STORED = {"time", "lat", "lon", "alt", "pol", "mds", "mcg", "region", "delay"}

top_keys = Counter()
sig_keys = Counter()
station_counts = []
sig_len_hist = Counter()
failures = 0
key_examples = {}
latc_differs = 0
latc_samples = []
status_vals = Counter()
pol_vals = Counter()

for f in frames:
    try:
        obj = json.loads(lzw_decode(f))
    except Exception as e:
        failures += 1
        continue
    for k, v in obj.items():
        top_keys[k] += 1
        if k not in key_examples:
            key_examples[k] = v
    sig = obj.get("sig") or []
    station_counts.append(len(sig))
    sig_len_hist[len(sig)] += 1
    for s in sig:
        if isinstance(s, dict):
            for k in s:
                sig_keys[k] += 1
    if "status" in obj:
        status_vals[obj["status"]] += 1
    if "pol" in obj:
        pol_vals[obj["pol"]] += 1
    # do the "corrected" coords differ from raw?
    if "latc" in obj and "lat" in obj:
        if obj["latc"] != obj["lat"] or obj.get("lonc") != obj.get("lon"):
            latc_differs += 1
            if len(latc_samples) < 3:
                latc_samples.append(
                    (obj["lat"], obj["latc"], obj["lon"], obj.get("lonc"))
                )

n = len(frames) - failures
print(f"json failures: {failures}/{len(frames)}\n")

print("=== TOP-LEVEL KEYS (present in N frames) ===")
for k, c in top_keys.most_common():
    mark = "STORED " if k in STORED else "DROPPED"
    ex = repr(key_examples[k])
    if len(ex) > 60:
        ex = ex[:57] + "..."
    print(f"  [{mark}] {k:8} {c:4}/{n}  e.g. {ex}")

print(f"\n=== sig[] ARRAY (the per-station detections) ===")
print(f"  keys inside each sig entry: {dict(sig_keys)}")
if station_counts:
    print(f"  stations per strike: min={min(station_counts)} max={max(station_counts)} "
          f"avg={sum(station_counts)/len(station_counts):.1f}")
    print(f"  distribution of top counts: {dict(sig_len_hist.most_common(8))}")
    at_max = sig_len_hist.get(max(station_counts), 0)
    print(f"  frames AT the max ({max(station_counts)}): {at_max}/{n} = {at_max/n*100:.1f}%"
          f"   <-- if high, upstream is TRUNCATING")

print(f"\n=== corrected coords (latc/lonc) ===")
print(f"  differ from raw lat/lon in {latc_differs}/{n} frames")
for s in latc_samples:
    print(f"    lat {s[0]} -> latc {s[1]}   lon {s[2]} -> lonc {s[3]}")

print(f"\n=== status values === {dict(status_vals)}")
print(f"=== pol values === {dict(pol_vals)}")
