# The Bible

Complete export of every lightning strike this pipeline has stored.

**Data file:** [`bible.csv`](bible.csv) — 190,029 rows, 22 MB, CSV with header.

---

## What's in it

| | |
|---|---|
| Rows | **190,029** |
| Ingestion window | 2026-02-06 01:48 → 2026-09-14 14:32 UTC |
| Coordinate integrity | 0 out-of-range lat/lon |
| Timestamp integrity | 100% in 2026 (post-cleanup) |
| Centroid | 32.03°N, 46.10°W |

### Regional distribution

| Region | Strikes | Share |
|---|---:|---:|
| N. America | 125,820 | 66.2% |
| Africa | 25,814 | 13.6% |
| Asia | 17,703 | 9.3% |
| Europe | 14,706 | 7.7% |
| Oceania | 2,926 | 1.5% |
| Ocean / other | 2,644 | 1.4% |
| S. America | 547 | 0.3% |

North America dominates because Blitzortung's station density is highest
there — this reflects **detector coverage**, not where lightning actually
strikes. Genuine global hotspots (Lake Maracaibo, the Congo basin) are
under-represented for the same reason.

---

## Two eras in one file

The export spans both decoder generations, and you can tell them apart:

| | Legacy rows | LZW rows |
|---|---|---|
| Count | 17,668 | 172,361 |
| `stations` / `region` / `delay_s` | empty | populated |
| Decoder | v1 substitution table | v2/v3 real LZW |

Legacy rows (February, ids in the low tens of thousands) came from the
substitution-table decoder and carry its damage. Row `id=35` is the museum
piece:

```csv
35,1770342503916380,2026-02-06 01:48:23.91638,-7.314338,15,7,ķmds: 81078mcg: 65,81078,65,,,,...
```

`polarity` = `ķmds: 81078mcg: 65` — field boundaries dissolved because the
table deleted the delimiter bytes, and `longitude` = `15` with
`altitude` = `7` are shifted remnants. Rows written after the LZW rewrite
are clean: exact 19-digit epochs, real station counts, no mojibake.

The ~32,000 worst legacy rows (timestamps in 1975 / 2537) were deleted
before this export and are archived separately under `backups/`.

---

## Schema

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | Primary key, insertion order |
| `strike_time` | bigint | Raw feed epoch, nanoseconds (19 digits on LZW rows) |
| `strike_timestamp` | timestamp | Decoded UTC instant |
| `latitude` | float | −90…90, constraint-checked |
| `longitude` | float | −180…180, constraint-checked |
| `altitude` | int | Meters; nearly always 0 from this feed |
| `polarity` | text | Feed `pol` field; garbled on legacy rows |
| `mds` | int | Feed deviation metric |
| `mcg` | int | Feed deviation metric |
| `stations` | smallint | Detecting stations — **LZW rows only**, avg 32.5 |
| `region` | smallint | Blitzortung region id — LZW rows only |
| `delay_s` | real | Feed-reported network delay — LZW rows only |
| `inserted_at` | timestamp | When this pipeline wrote the row |

`stations` is the useful confidence signal: more stations triangulating a
strike means a more precise fix. Median is 35, ceiling is 40.

---

## Loading it

```python
import pandas as pd

df = pd.read_csv("bible.csv", parse_dates=["strike_timestamp", "inserted_at"])

# clean subset only (LZW-decoded rows)
clean = df[df.stations.notna()]

# feed latency: how long from strike to stored
(clean.inserted_at - clean.strike_timestamp).dt.total_seconds().median()  # ~20s

# high-confidence strikes
clean[clean.stations >= 35]
```

```bash
# regenerate this export from the live database
docker exec lightning_db psql -U lightning_user -d lightning \
  -c "\copy (SELECT * FROM lightning_strikes ORDER BY id) TO '/tmp/bible.csv' CSV HEADER"
docker cp lightning_db:/tmp/bible.csv ./bible.csv
```

---

*Snapshot taken 2026-09-14. The pipeline keeps running — regenerate to catch up.*
