"""Minimal Dagster wiring: one asset that runs `sam-tracker check` daily.

Untested against a live Dagster install; kept deliberately small.
    pip install dagster dagster-webserver
    dagster dev -f examples/dagster_defs.py
"""
from dagster import AssetExecutionContext, Definitions, ScheduleDefinition, asset, define_asset_job

from sam_tracker.cli import main


@asset
def sam_changes(context: AssetExecutionContext) -> int:
    """Re-snapshot every tracked solicitation and record changes. Returns the CLI exit code
    (0 = nothing changed, 2 = changes recorded, 3 = SAM.gov quota exhausted)."""
    code = main(["check", "--json"])
    context.log.info("sam-tracker check exit code %s", code)
    return code


defs = Definitions(
    assets=[sam_changes],
    schedules=[ScheduleDefinition(job=define_asset_job("daily_check", selection=[sam_changes]), cron_schedule="0 13 * * *")],
)
