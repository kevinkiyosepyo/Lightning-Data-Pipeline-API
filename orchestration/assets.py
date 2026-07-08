"""Dagster assets: warehouse aggregates, Parquet lake export, DuckDB analytics.

Downstream of the streaming pipeline (WebSocket -> Kafka -> Postgres), this
layer produces:

  hourly_strike_aggregates  Postgres rollup table, upserted idempotently
  strikes_parquet           hour-partitioned Parquet files (the "lake")
  duckdb_analytics          DuckDB warehouse built on top of the Parquet lake

plus data-quality checks (freshness, coordinate bounds, duplicates) that daily
runs surface directly in the Dagster UI.
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
import psycopg2
from dagster import AssetCheckResult, AssetExecutionContext, MetadataValue, asset, asset_check

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
LOOKBACK_HOURS = int(os.getenv("AGGREGATE_LOOKBACK_HOURS", "24"))


def pg_connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "lightning"),
        user=os.getenv("POSTGRES_USER", "lightning_user"),
        password=os.getenv("POSTGRES_PASSWORD", "lightning_pass"),
        port=os.getenv("POSTGRES_PORT", "5432"),
    )


@asset(group_name="warehouse", compute_kind="postgres")
def hourly_strike_aggregates(context: AssetExecutionContext):
    """Hourly rollups per region, upserted into Postgres.

    Recomputes the trailing window each run, so late-arriving strikes are
    folded in and reruns are idempotent.
    """
    with pg_connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS strike_aggregates_hourly (
                hour TIMESTAMPTZ NOT NULL,
                region SMALLINT,
                strike_count BIGINT NOT NULL,
                avg_mds DOUBLE PRECISION,
                avg_station_count DOUBLE PRECISION,
                min_latitude DOUBLE PRECISION,
                max_latitude DOUBLE PRECISION,
                computed_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (hour, region)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO strike_aggregates_hourly
                (hour, region, strike_count, avg_mds, avg_station_count,
                 min_latitude, max_latitude)
            SELECT
                date_trunc('hour', strike_timestamp) AS hour,
                coalesce(region, -1) AS region,
                count(*),
                avg(mds),
                avg(station_count),
                min(latitude),
                max(latitude)
            FROM lightning_strikes
            WHERE strike_timestamp >= now() - make_interval(hours => %s)
            GROUP BY 1, 2
            ON CONFLICT (hour, region) DO UPDATE SET
                strike_count = EXCLUDED.strike_count,
                avg_mds = EXCLUDED.avg_mds,
                avg_station_count = EXCLUDED.avg_station_count,
                min_latitude = EXCLUDED.min_latitude,
                max_latitude = EXCLUDED.max_latitude,
                computed_at = now()
            """,
            (LOOKBACK_HOURS,),
        )
        upserted = cursor.rowcount
        cursor.execute("SELECT count(*) FROM strike_aggregates_hourly")
        total_rows = cursor.fetchone()[0]

    context.add_output_metadata(
        {"rows_upserted": upserted, "table_total_rows": total_rows}
    )


@asset_check(asset=hourly_strike_aggregates, blocking=False)
def strikes_are_fresh() -> AssetCheckResult:
    """The newest strike should be recent — a stale feed means ingestion is down."""
    with pg_connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT extract(epoch FROM now() - max(strike_timestamp)) FROM lightning_strikes"
        )
        lag_seconds = cursor.fetchone()[0]
    if lag_seconds is None:
        return AssetCheckResult(passed=False, metadata={"reason": "no strikes stored yet"})
    lag_seconds = float(lag_seconds)  # psycopg2 returns Decimal
    return AssetCheckResult(
        passed=lag_seconds < 900,
        metadata={"lag_seconds": round(lag_seconds, 1), "threshold_seconds": 900},
    )


@asset_check(asset=hourly_strike_aggregates)
def coordinates_in_bounds() -> AssetCheckResult:
    with pg_connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM lightning_strikes
            WHERE latitude NOT BETWEEN -90 AND 90
               OR longitude NOT BETWEEN -180 AND 180
            """
        )
        bad = cursor.fetchone()[0]
    return AssetCheckResult(passed=bad == 0, metadata={"out_of_bounds_rows": bad})


@asset_check(asset=hourly_strike_aggregates)
def no_duplicate_strikes() -> AssetCheckResult:
    with pg_connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM (
                SELECT strike_time, latitude, longitude
                FROM lightning_strikes
                GROUP BY 1, 2, 3
                HAVING count(*) > 1
            ) dupes
            """
        )
        dupes = cursor.fetchone()[0]
    return AssetCheckResult(passed=dupes == 0, metadata={"duplicate_natural_keys": dupes})


@asset(group_name="lake", compute_kind="parquet")
def strikes_parquet(context: AssetExecutionContext):
    """Hour-partitioned Parquet export of the raw strikes table.

    Layout: <DATA_DIR>/parquet/date=YYYY-MM-DD/hour=HH/strikes.parquet
    Each completed-hour partition in the lookback window is rewritten in
    full, so the export is idempotent and safe to backfill.
    """
    query = """
        SELECT id, strike_time, strike_timestamp, latitude, longitude, altitude,
               polarity, mds, mcg, status, region, station_count, inserted_at
        FROM lightning_strikes
        WHERE strike_timestamp >= date_trunc('hour', now() - make_interval(hours => %s))
    """
    with pg_connect() as conn:
        df = pd.read_sql(query, conn, params=(LOOKBACK_HOURS,))

    if df.empty:
        context.add_output_metadata({"partitions_written": 0, "rows_exported": 0})
        return

    df["date"] = df["strike_timestamp"].dt.strftime("%Y-%m-%d")
    df["hour"] = df["strike_timestamp"].dt.strftime("%H")

    partitions = 0
    for (date, hour), part in df.groupby(["date", "hour"]):
        out_dir = DATA_DIR / "parquet" / f"date={date}" / f"hour={hour}"
        out_dir.mkdir(parents=True, exist_ok=True)
        part.drop(columns=["date", "hour"]).to_parquet(out_dir / "strikes.parquet", index=False)
        partitions += 1

    context.add_output_metadata(
        {
            "partitions_written": partitions,
            "rows_exported": len(df),
            "lake_path": MetadataValue.path(str(DATA_DIR / "parquet")),
        }
    )


@asset(deps=[strikes_parquet], group_name="analytics", compute_kind="duckdb")
def duckdb_analytics(context: AssetExecutionContext):
    """DuckDB warehouse over the Parquet lake: daily/region/storm-cell tables."""
    lake_glob = str(DATA_DIR / "parquet" / "*" / "*" / "*.parquet")
    db_path = DATA_DIR / "analytics.duckdb"

    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            f"CREATE OR REPLACE VIEW strikes AS "
            f"SELECT * FROM read_parquet('{lake_glob}', hive_partitioning=true)"
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE daily_summary AS
            SELECT
                date_trunc('day', strike_timestamp) AS day,
                count(*) AS strikes,
                avg(station_count) AS avg_stations,
                avg(mds) AS avg_mds,
                count(DISTINCT region) AS active_regions
            FROM strikes GROUP BY 1 ORDER BY 1
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE region_activity AS
            SELECT
                region,
                count(*) AS strikes,
                min(strike_timestamp) AS first_seen,
                max(strike_timestamp) AS last_seen
            FROM strikes GROUP BY 1 ORDER BY strikes DESC
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE storm_cells AS
            SELECT
                round(latitude, 0) AS lat_bin,
                round(longitude, 0) AS lon_bin,
                date_trunc('hour', strike_timestamp) AS hour,
                count(*) AS strikes
            FROM strikes
            GROUP BY 1, 2, 3
            HAVING count(*) >= 10
            ORDER BY strikes DESC
            """
        )
        total = con.execute("SELECT count(*) FROM strikes").fetchone()[0]
        cells = con.execute("SELECT count(*) FROM storm_cells").fetchone()[0]
    finally:
        con.close()

    context.add_output_metadata(
        {
            "strikes_in_lake": total,
            "storm_cells_detected": cells,
            "warehouse_path": MetadataValue.path(str(db_path)),
        }
    )
