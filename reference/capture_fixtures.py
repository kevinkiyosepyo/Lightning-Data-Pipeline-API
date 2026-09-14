"""Capture raw Blitzortung frames + their decoded JSON as Rust test fixtures."""
import json
import sys
import time
import websocket

sys.path.insert(0, "/app")
from ingest import BlitzortungDecoder  # noqa: E402

N = 40
frames = []


def on_data(ws, data, opcode, fin):
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    frames.append(data)
    if len(frames) >= N:
        ws.close()


def on_open(ws):
    ws.send(json.dumps({"a": 111}))


ws = websocket.WebSocketApp("wss://ws7.blitzortung.org/", on_open=on_open, on_data=on_data)
ws.run_forever(ping_interval=30, ping_timeout=10)

out = []
for f in frames:
    decoded = BlitzortungDecoder.lzw_decode(f)
    obj = json.loads(decoded)
    out.append({"compressed": f, "decoded": decoded, "time": obj["time"],
                "lat": obj["lat"], "lon": obj["lon"], "stations": len(obj.get("sig", []))})

json.dump(out, open("/out/frames.json", "w"), ensure_ascii=False, indent=1)
print(f"wrote {len(out)} fixtures; all time 19-digit: {all(len(str(o['time']))==19 for o in out)}")
