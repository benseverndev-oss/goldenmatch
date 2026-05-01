"""Schema drift alarm — alert when an upstream source's columns change.

Picks up where `schema_align_and_load` leaves off. Once a source's mapping is
cached, this DAG periodically re-profiles the latest sample and compares to
the cached mapping. Three drift signals worth alarming on:

  1. **New columns** in source — possibly carrying signal you should map.
  2. **Removed columns** — current pipelines silently drop them.
  3. **Renamed columns** — mapping confidence shifts to a different target.

Always human-in-the-loop: never auto-update the cached mapping. The alarm
just files a ticket / fires a webhook so a steward looks at it.

Requires:
    pip install apache-airflow infermap apache-airflow-providers-amazon \\
                apache-airflow-providers-postgres polars
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

CANONICAL_SCHEMA_FILE = "/opt/airflow/dags/golden_suite/canonical_customers.yaml"
MAPPINGS_TABLE = "warehouse.source_column_mappings"
DRIFT_LOG_TABLE = "audit.schema_drift_events"

# Confidence delta below which a column's mapping is considered stable.
DRIFT_CONFIDENCE_DELTA = 0.15


@dag(
    dag_id="golden_suite_schema_drift_alarm",
    description="Compare current source columns to cached InferMap mappings; alert on drift.",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["golden-suite", "drift", "monitoring"],
)
def golden_suite_schema_drift_alarm():
    """Detect upstream schema drift against cached mappings."""

    @task
    def list_sources_with_cached_mapping() -> list[dict]:
        """Pull every source we have a cached mapping for."""
        import json

        from airflow.providers.postgres.hooks.postgres import PostgresHook

        rows = PostgresHook(postgres_conn_id="postgres_default").get_records(
            f"SELECT source_name, mapping FROM {MAPPINGS_TABLE}"
        )
        return [{"name": r[0], "mapping": json.loads(r[1])} for r in rows]

    @task
    def check_drift(source: dict) -> dict[str, Any]:
        """Re-profile the latest sample for one source, compare to cached mapping."""
        from pathlib import Path

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import infermap
        import polars as pl

        # Convention: latest_sample is at incoming/<source>/_latest_sample.csv.
        # If you don't keep one, replace this with whatever path makes sense
        # for your inbound layout.
        local = Path(f"/tmp/golden_suite/drift/{source['name']}_sample.csv")
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            S3Hook(aws_conn_id="aws_default").get_key(
                f"incoming/{source['name']}/_latest_sample.csv",
                Variable.get("golden_suite_bucket"),
            ).download_file(Filename=str(local))
        except Exception as exc:  # noqa: BLE001
            return {"source": source["name"], "skipped": True, "reason": str(exc)}

        sample = pl.read_csv(local, encoding="utf8-lossy", ignore_errors=True, n_rows=1000)
        current = sample.columns
        cached_keys = set(source["mapping"].keys())

        new_columns = sorted(set(current) - cached_keys)
        removed_columns = sorted(cached_keys - set(current))

        # Rebuild mapping fresh and compare confidence per column.
        result = infermap.map(source=sample.head(500).to_dicts(),
                              schema_file=CANONICAL_SCHEMA_FILE)
        renamed: list[dict] = []
        for m in result.mappings:
            cached_target = source["mapping"].get(m.source)
            if cached_target and cached_target != m.target:
                renamed.append({
                    "column": m.source,
                    "cached_target": cached_target,
                    "current_target": m.target,
                    "confidence": m.confidence,
                })

        return {
            "source": source["name"],
            "skipped": False,
            "new_columns": new_columns,
            "removed_columns": removed_columns,
            "renamed": renamed,
            "drifted": bool(new_columns or removed_columns or renamed),
        }

    @task
    def alert_and_log(report: dict[str, Any]) -> None:
        """Log to drift table and fire a webhook if anything drifted."""
        import json
        import logging

        if report.get("skipped") or not report.get("drifted"):
            return

        from airflow.providers.postgres.hooks.postgres import PostgresHook
        PostgresHook(postgres_conn_id="postgres_default").run(
            f"""
            INSERT INTO {DRIFT_LOG_TABLE}
                (source_name, new_columns, removed_columns, renamed, detected_at)
            VALUES (%(s)s, %(n)s, %(r)s, %(rn)s::jsonb, NOW())
            """,
            parameters={
                "s": report["source"],
                "n": report["new_columns"],
                "r": report["removed_columns"],
                "rn": json.dumps(report["renamed"]),
            },
        )

        # Replace this with your team's webhook (slack_webhook_hook, etc.)
        logging.warning(
            "SCHEMA DRIFT detected for %s: new=%s removed=%s renamed=%d items",
            report["source"], report["new_columns"], report["removed_columns"],
            len(report["renamed"]),
        )

    sources = list_sources_with_cached_mapping()
    reports = check_drift.expand(source=sources)
    alert_and_log.expand(report=reports)


golden_suite_schema_drift_alarm()
