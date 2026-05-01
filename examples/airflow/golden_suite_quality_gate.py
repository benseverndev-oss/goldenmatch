"""Quality gate — fail upstream pipelines when data quality regresses.

GoldenCheck used as a *gatekeeper*, not a preprocessor. Drop this DAG into
the front of any pipeline that should refuse to run on garbage. Two ways
it can be wired:

  1. Set this as an upstream dependency in another DAG via TriggerDagRunOperator
     or a Dataset; downstream DAGs that depend on the dataset only run when
     this DAG succeeds.
  2. Run it on a schedule against a watch directory; on failure, page an
     on-call channel.

Failure semantics: if any threshold is breached, the gate task raises and the
DAG run goes red. Downstream Datasets are NOT updated, so any dependent DAGs
stay paused until the next clean run.

Tunable: per-check thresholds + alert webhook. The example is opinionated —
copy and adapt to your data's quality budget.

Requires:
    pip install apache-airflow goldencheck apache-airflow-providers-amazon
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.datasets import Dataset
from airflow.decorators import dag, task
from airflow.models import Variable

WATCH_KEY_TEMPLATE = "raw/customers/{{ ds }}/customers.csv"

# Per-check thresholds — fail if any check exceeds its budget.
THRESHOLDS = {
    "encoding_issues":   0.0,    # zero tolerance
    "null_rate":          0.20,   # max 20% nulls per column
    "duplicate_rate":     0.30,   # max 30% raw duplicates (suite handles below)
    "format_violations":  0.05,   # 5%
    "unicode_issues":     0.01,
}

CLEAN_DATASET = Dataset("s3://golden-suite/quality-gated/customers/")


@dag(
    dag_id="golden_suite_quality_gate",
    description="GoldenCheck-driven gatekeeper. Fails red on quality regression.",
    schedule="0 3 * * *",  # 03:00 UTC, before downstream daily DAGs at 04:00
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 0},  # 0 retries — fail fast
    tags=["golden-suite", "quality-gate", "gatekeeper"],
)
def golden_suite_quality_gate():
    """Gatekeeper for downstream pipelines."""

    @task
    def fetch(key: str) -> str:
        from pathlib import Path
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        local = Path(f"/tmp/golden_suite/gate/{key}")
        local.parent.mkdir(parents=True, exist_ok=True)
        S3Hook(aws_conn_id="aws_default").get_key(
            key, Variable.get("golden_suite_bucket")
        ).download_file(Filename=str(local))
        return str(local)

    @task
    def scan(local_path: str) -> dict[str, Any]:
        import goldencheck
        return goldencheck.scan_file(local_path).to_dict()

    @task
    def gate(scan_result: dict[str, Any]) -> str:
        """Compare findings to thresholds. Raise if any breach."""
        breaches: list[str] = []
        for finding in scan_result.get("findings", []):
            check = finding.get("check")
            rate = finding.get("rate") or finding.get("affected_pct")
            limit = THRESHOLDS.get(check)
            if limit is None or rate is None:
                continue
            if rate > limit:
                breaches.append(
                    f"{check}: {rate:.1%} exceeds threshold {limit:.1%} "
                    f"(column={finding.get('column', '?')})"
                )
        if breaches:
            raise ValueError(
                "Quality gate FAILED. Breaches:\n  - " + "\n  - ".join(breaches)
                + "\n\nDownstream DAGs are blocked. Inspect findings, fix at source, retry."
            )
        return scan_result.get("summary_uri") or scan_result.get("source") or "(no source path)"

    @task(outlets=[CLEAN_DATASET])
    def emit_clean(source: str) -> str:
        """Mark the dataset as updated so downstream DAGs trigger."""
        return source

    @task(trigger_rule="all_failed")
    def alert_on_fail(**context) -> None:
        """Webhook alert when the gate fails. Wire to Slack/PagerDuty as needed."""
        import logging
        # Replace with your team's webhook call (slack_webhook_hook, etc.)
        logging.error(
            "QUALITY GATE FAILED for %s/%s. See task logs for breach details.",
            context["dag"].dag_id, context["run_id"],
        )

    local = fetch(WATCH_KEY_TEMPLATE)
    findings = scan(local)
    source = gate(findings)
    emit_clean(source)
    alert_on_fail()


golden_suite_quality_gate()
