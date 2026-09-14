#!/usr/bin/env bash
# End-to-end smoke test for the Rust lightning stack.
# Exercises every endpoint, checks JSON shape, and confirms data is flowing.
set -uo pipefail

API="${API_URL:-http://localhost:8000}"
PASS=0; FAIL=0
J=/usr/bin/python3

ok()   { PASS=$((PASS+1)); printf '  ✓ %s\n' "$1"; }
fail() { FAIL=$((FAIL+1)); printf '  ✗ %s\n' "$1"; }

check_json() { # name url python-predicate
  local body code
  body=$(curl -s --max-time 10 -w '\n%{http_code}' "$2")
  code=${body##*$'\n'}; body=${body%$'\n'*}
  if [[ "$code" != "200" ]]; then fail "$1 → HTTP $code"; return; fi
  if printf '%s' "$body" | $J -c "import json,sys; d=json.load(sys.stdin); assert ($3), 'predicate failed'" 2>/dev/null; then
    ok "$1"
  else
    fail "$1 → shape/predicate failed: $(printf '%s' "$body" | head -c 200)"
  fi
}

echo "== Smoke: $API =="

check_json "GET /"                 "$API/"                              "d['message']=='Lightning Strike API' and 'endpoints' in d and d.get('runtime')=='rust/axum'"
check_json "GET /health"           "$API/health"                        "d['status']=='healthy' and d['database']=='connected' and d['total_strikes']>0"
check_json "GET /ingestion/stats"  "$API/ingestion/stats"               "d['total_received']>=0 and 0<=d['success_rate']<=100"
check_json "GET /strikes/stats"    "$API/strikes/stats"                 "d['total_strikes']>0 and d['time_range_end'] is not None"
check_json "GET /strikes/recent"   "$API/strikes/recent?limit=5&minutes=30" "isinstance(d,list) and len(d)>0 and all({'id','strike_timestamp','latitude','longitude','inserted_at'}<=set(s) for s in d)"
check_json "recent has new cols"   "$API/strikes/recent?limit=5&minutes=30" "all('stations' in s and 'region' in s and 'delay_s' in s for s in d)"
check_json "recent ordered by ingest" "$API/strikes/recent?limit=20&minutes=30" "[s['inserted_at'] for s in d]==sorted((s['inserted_at'] for s in d), reverse=True)"
check_json "GET /strikes (bbox)"   "$API/strikes?limit=10&min_lat=-90&max_lat=90&min_lon=-180&max_lon=180" "isinstance(d,list) and len(d)>0"
check_json "GET /strikes (since)"  "$API/strikes?limit=5&since=2026-01-01T00:00:00" "isinstance(d,list)"
check_json "GET /strikes/nearby"   "$API/strikes/nearby?lat=35&lon=-95&radius=500&minutes=60&limit=10" "isinstance(d,list) and all(s.get('distance_km',0)<=500 for s in d)"
check_json "nearby sorted by dist" "$API/strikes/nearby?lat=35&lon=-95&radius=500&minutes=60&limit=20" "[s['distance_km'] for s in d]==sorted(s['distance_km'] for s in d)"

# --- validation errors are 4xx, not 500 ---
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API/strikes/nearby?lat=999&lon=0")
[[ "$code" == "422" ]] && ok "nearby lat=999 → 422" || fail "nearby lat=999 → $code (want 422)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API/strikes?since=garbage")
[[ "$code" == "422" ]] && ok "strikes since=garbage → 422" || fail "strikes since=garbage → $code (want 422)"

# --- /live dashboard served from binary ---
code=$(curl -s -o /tmp/live_check.html -w '%{http_code}' --max-time 10 "$API/live")
if [[ "$code" == "200" ]] && grep -q "Lightning strike pipeline" /tmp/live_check.html; then ok "GET /live serves dashboard"; else fail "GET /live → $code"; fi

# --- CORS ---
if curl -s -D - -o /dev/null --max-time 10 -H "Origin: http://example.com" "$API/health" | grep -qi "access-control-allow-origin"; then ok "CORS header present"; else fail "CORS header missing"; fi

# --- data is actually flowing: count must grow over 8s ---
a=$(curl -s --max-time 10 "$API/health" | $J -c "import json,sys; print(json.load(sys.stdin)['total_strikes'])")
sleep 8
b=$(curl -s --max-time 10 "$API/health" | $J -c "import json,sys; print(json.load(sys.stdin)['total_strikes'])")
if [[ "$b" -gt "$a" ]]; then ok "live ingestion: +$((b-a)) strikes in 8s"; else fail "no new strikes in 8s ($a → $b)"; fi

# --- latency ---
t=$( { /usr/bin/time -p curl -s -o /dev/null "$API/strikes/recent?limit=100&minutes=30"; } 2>&1 | awk '/real/{print $2}')
ok "recent?limit=100 round-trip ${t}s"

echo
echo "== $PASS passed, $FAIL failed =="
[[ $FAIL -eq 0 ]]
