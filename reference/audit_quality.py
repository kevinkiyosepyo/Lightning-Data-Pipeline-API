"""Deep-dive: is `stations` censored, and what's a real confidence metric?

Checks (a) whether sig[] is truncated at 40 upstream, (b) whether mds/mcg
carry quality signal, (c) whether station GEOMETRY (azimuthal gap) is
computable — the standard proxy for fix quality in multilateration.
"""
import json
import math
import sys
from collections import Counter

import websocket


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


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def azimuthal_gap(lat, lon, sig):
    """Largest angular gap between consecutive stations, viewed from strike.

    Standard multilateration quality proxy: <90 deg = strike well-surrounded;
    >180 deg = strike OUTSIDE the station ring, position poorly constrained.
    """
    bs = sorted(bearing(lat, lon, s["lat"], s["lon"]) for s in sig
                if isinstance(s, dict) and "lat" in s)
    if len(bs) < 2:
        return 360.0
    gaps = [(bs[i + 1] - bs[i]) for i in range(len(bs) - 1)]
    gaps.append(360 - bs[-1] + bs[0])
    return max(gaps)


N = 500
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

rows = []
for f in frames:
    try:
        o = json.loads(lzw_decode(f))
    except Exception:
        continue
    sig = [s for s in (o.get("sig") or []) if isinstance(s, dict) and "lat" in s]
    if not sig:
        continue
    gap = azimuthal_gap(o["lat"], o["lon"], sig)
    dists = sorted(haversine(o["lat"], o["lon"], s["lat"], s["lon"]) for s in sig)
    rows.append({
        "n": len(sig), "gap": gap, "mds": o.get("mds"), "mcg": o.get("mcg"),
        "status": o.get("status"), "dmin": dists[0], "dmax": dists[-1],
        "delay": o.get("delay"),
    })

print(f"analyzed {len(rows)} strikes\n")

# --- censoring check ---
cnt = Counter(r["n"] for r in rows)
mx = max(cnt)
print(f"=== IS `stations` CENSORED? ===")
print(f"  max observed = {mx}; frames at max = {cnt[mx]}/{len(rows)} = {cnt[mx]/len(rows)*100:.1f}%")
print(f"  counts 38-40: {[(k, cnt.get(k,0)) for k in (38,39,40,41,42)]}")
print("  -> a hard pile-up at exactly 40 with nothing above = upstream truncation")

# --- geometry ---
gaps = sorted(r["gap"] for r in rows)
print(f"\n=== AZIMUTHAL GAP (real fix-quality signal) ===")
for q, lbl in ((0.5, "p50"), (0.9, "p90"), (0.99, "p99")):
    print(f"  {lbl}: {gaps[int(len(gaps)*q)-1]:6.1f} deg")
well = sum(1 for g in gaps if g < 90)
poor = sum(1 for g in gaps if g > 180)
print(f"  well-surrounded (<90 deg): {well}/{len(gaps)} = {well/len(gaps)*100:.0f}%")
print(f"  outside network (>180 deg): {poor}/{len(gaps)} = {poor/len(gaps)*100:.0f}%")

# --- does station count predict geometry? ---
import statistics
hi = [r["gap"] for r in rows if r["n"] >= 38]
lo = [r["gap"] for r in rows if r["n"] <= 25]
if hi and lo:
    print(f"\n=== DOES STATION COUNT PREDICT QUALITY? ===")
    print(f"  many stations (>=38): median gap {statistics.median(hi):.1f} deg  (n={len(hi)})")
    print(f"  few stations  (<=25): median gap {statistics.median(lo):.1f} deg  (n={len(lo)})")
    print("  -> if these are close, station COUNT alone is a weak confidence signal")

# --- mds / mcg ---
print(f"\n=== mds / mcg ranges ===")
for k in ("mds", "mcg"):
    v = sorted(r[k] for r in rows if r[k] is not None)
    if v:
        print(f"  {k}: min={v[0]} p50={v[len(v)//2]} max={v[-1]}")
print(f"\n=== station distance from strike (km) ===")
dmin = sorted(r["dmin"] for r in rows)
dmax = sorted(r["dmax"] for r in rows)
print(f"  nearest station:  p50={dmin[len(dmin)//2]:.0f} km   max={dmin[-1]:.0f} km")
print(f"  farthest station: p50={dmax[len(dmax)//2]:.0f} km   max={dmax[-1]:.0f} km")
print(f"\n=== status ===", Counter(r["status"] for r in rows))
