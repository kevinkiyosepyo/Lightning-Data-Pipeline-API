"""Dagster definitions: assets, quality checks, and the hourly schedule."""

from dagster import AssetSelection, Definitions, ScheduleDefinition, define_asset_job

from orchestration.assets import (
    coordinates_in_bounds,
    duckdb_analytics,
    hourly_strike_aggregates,
    no_duplicate_strikes,
    strikes_are_fresh,
    strikes_parquet,
)

refresh_job = define_asset_job(
    name="refresh_all_analytics",
    selection=AssetSelection.all(),
)

hourly_schedule = ScheduleDefinition(
    job=refresh_job,
    cron_schedule="5 * * * *",  # five past every hour, once the hour partition is complete
    name="hourly_analytics_refresh",
)

defs = Definitions(
    assets=[hourly_strike_aggregates, strikes_parquet, duckdb_analytics],
    asset_checks=[strikes_are_fresh, coordinates_in_bounds, no_duplicate_strikes],
    jobs=[refresh_job],
    schedules=[hourly_schedule],
)
