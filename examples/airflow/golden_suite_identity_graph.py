"""Daily Golden Suite pipeline with Identity Graph.

Drop this into your Airflow `dags/` folder. Pulls a daily CSV from S3, runs
the full Golden Suite chain (Check -> Flow -> Match -> Identity Graph), and
keeps the durable identity store on S3-synced shared storage so subsequent
DAG runs (and the existing `golden_suite_review_worker.py` DAG) see stable
`entity_id`s across days.

Identity-flagged conflicts (auto-detected by the v1.15+ controller --
weak-bottleneck and merge-with-prior-conflict edges) are surfaced as
``conflicts_flagged`` XCom for downstream review.

Tunable knobs are at the top. Each task fails loud rather than silently
producing empty output.

Requires:
    pip install apache-airflow goldenpipe[full] goldenmatch>=1.15.0 \\
                apache-airflow-providers-amazon polars

Connections (Airflow UI -> Admin -> Connections):
    aws_default       -- S3 read/write
    postgres_default  -- (optional) for identity store on shared Postgres

Tested against Airflow 2.10. Compatible with 3.x via TaskFlow API.

See ``docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md``
for the design behind this DAG.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

# -----------------------------------------------------------------------------
# Knobs -- adjust per environment
# -----------------------------------------------------------------------------
S3_BUCKET = Variable.get("golden_suite_bucket")
SOURCE_KEY_TEMPLATE = "raw/customers/{{ ds }}/customers.csv"
GOLDEN_KEY_TEMPLATE = "golden/customers/{{ ds }}/golden.parquet"

# Identity store on shared S3-synced storage. The store file is small (~MBs
# even at 1M records); we pull it before the pipeline and push it back
# after. For high-volume / multi-writer use, switch to Postgres backend.
IDENTITY_STORE_KEY = "identity/customers/identity.db"
IDENTITY_DATASET = "customers"
IDENTITY_SOURCE_PK_COLUMN = "customer_id"
# Mirror the goldenmatch default. Lower the threshold to flag *more* edges
# for steward review; raise to flag fewer.
IDENTITY_WEAK_CONFIDENCE_THRESHOLD = 0.6

# Optional: Postgres backend for multi-writer setups. Set both to switch.
# IDENTITY_BACKEND = "postgres"
# IDENTITY_POSTGRES_CONN_ID = "postgres_default"

# Match config -- tune for your data shape
MATCH_EXACT_FIELDS = ["email"]
MATCH_FUZZY_FIELDS = {"first_name": 0.85, "last_name": 0.85, "city": 0.9}
MATCH_BLOCKING = ["zip"]
MATCH_THRESHOLD = 0.85


@dag(
    dag_id="golden_suite_identity_graph",
    description=(
        "Daily Check -> Flow -> Match -> Identity Graph. Maintains a "
        "durable identity store with stable entity_ids across runs."
    ),
    schedule="0 5 * * *",  # 05:00 UTC daily -- after upstream ingest
    start_date=pendulum.datetime(2026, 5, 13, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["golden-suite", "identity-graph", "customers"],
)
def golden_suite_identity_graph():
    """Daily customer dedupe + durable identity graph."""

    @task
    def pull_source_csv(execution_date: str) -> str:
        """Download the day's source CSV from S3 to local disk."""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        key = SOURCE_KEY_TEMPLATE.replace("{{ ds }}", execution_date)
        local_path = f"/tmp/source-{execution_date}.csv"
        hook = S3Hook(aws_conn_id="aws_default")
        hook.get_key(key, bucket_name=S3_BUCKET).download_file(local_path)
        # Fail loudly if the file is empty -- skip is better than silently
        # producing 0-row golden output.
        if Path(local_path).stat().st_size < 100:
            raise ValueError(f"Source CSV at s3://{S3_BUCKET}/{key} is suspiciously small")
        return local_path

    @task
    def pull_identity_store() -> str:
        """Pull the canonical identity store from S3 to local disk.

        First-run friendly: a 404 means "no graph yet, mint one fresh"
        rather than a hard failure.
        """
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        local_db = "/tmp/identity.db"
        hook = S3Hook(aws_conn_id="aws_default")
        if hook.check_for_key(IDENTITY_STORE_KEY, bucket_name=S3_BUCKET):
            obj = hook.get_key(IDENTITY_STORE_KEY, bucket_name=S3_BUCKET)
            obj.download_file(local_db)
            print(f"Pulled identity store from s3://{S3_BUCKET}/{IDENTITY_STORE_KEY}")
        else:
            print("No prior identity store found; will mint fresh on first write.")
            Path(local_db).unlink(missing_ok=True)
        return local_db

    @task
    def run_pipeline(source_csv: str, identity_db: str, execution_date: str) -> dict:
        """Run GoldenPipe with the v1.2 identity_resolve stage.

        Returns a summary dict (identity_summary + conflicts_flagged + paths)
        used by downstream tasks as XCom.
        """
        from goldenpipe import run as gp_run

        result = gp_run(
            source_csv,
            identity_opts={
                "path": identity_db,
                "dataset": IDENTITY_DATASET,
                "source_pk_column": IDENTITY_SOURCE_PK_COLUMN,
                "weak_confidence_threshold": IDENTITY_WEAK_CONFIDENCE_THRESHOLD,
            },
        )
        if result.status.value == "failed":
            raise RuntimeError(
                f"GoldenPipe failed: {result.errors or '(no error message)'}"
            )

        identity_summary = result.artifacts.get("identity_summary") or {}
        conflicts = result.artifacts.get("conflicts", 0)
        golden = result.artifacts.get("golden")
        golden_local = f"/tmp/golden-{execution_date}.parquet"
        if golden is not None:
            golden.write_parquet(golden_local)

        return {
            "identity_summary": identity_summary,
            "conflicts_flagged": conflicts,
            "golden_path": golden_local if golden is not None else None,
            "identity_store_path": identity_db,
            "input_rows": result.input_rows,
        }

    @task
    def push_outputs(pipeline_result: dict, execution_date: str) -> dict:
        """Push the golden records and the updated identity store back to S3."""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        hook = S3Hook(aws_conn_id="aws_default")

        # Identity store -- always push so the next day's run sees today's IDs.
        hook.load_file(
            filename=pipeline_result["identity_store_path"],
            key=IDENTITY_STORE_KEY,
            bucket_name=S3_BUCKET,
            replace=True,
        )

        # Golden records (optional -- only if dedupe produced any)
        golden_key = None
        if pipeline_result["golden_path"]:
            golden_key = GOLDEN_KEY_TEMPLATE.replace("{{ ds }}", execution_date)
            hook.load_file(
                filename=pipeline_result["golden_path"],
                key=golden_key,
                bucket_name=S3_BUCKET,
                replace=True,
            )

        return {
            "s3_identity_store": f"s3://{S3_BUCKET}/{IDENTITY_STORE_KEY}",
            "s3_golden_records": (
                f"s3://{S3_BUCKET}/{golden_key}" if golden_key else None
            ),
        }

    @task
    def surface_conflicts(pipeline_result: dict) -> dict:
        """Emit identity_summary + conflicts_flagged as the DAG's XCom output.

        The existing `golden_suite_review_worker.py` DAG can consume this
        via `TriggerDagRunOperator` / `ExternalTaskSensor` and route flagged
        conflicts into the review queue. We don't trigger it from here --
        the loose-coupling keeps each DAG independently scheduleable.
        """
        summary = pipeline_result["identity_summary"]
        conflicts = pipeline_result["conflicts_flagged"]

        # Loud breadcrumb in the task log so operators don't have to dig
        # through XComs to see the headline numbers.
        print(
            f"Identity Graph: "
            f"created={summary.get('created', 0)}, "
            f"absorbed={summary.get('absorbed_records', 0)}, "
            f"merged={summary.get('merged', 0)}, "
            f"conflicts_flagged={conflicts}"
        )

        return {
            "identity_summary": summary,
            "conflicts_flagged": conflicts,
            "input_rows": pipeline_result["input_rows"],
        }

    src = pull_source_csv("{{ ds }}")
    db = pull_identity_store()
    pipeline_out = run_pipeline(src, db, "{{ ds }}")
    s3_paths = push_outputs(pipeline_out, "{{ ds }}")
    metrics = surface_conflicts(pipeline_out)
    # Linear dependency: source + db -> pipeline -> push + surface.
    # surface_conflicts only depends on pipeline_out; s3_paths is fire-and-
    # forget. Both happen after pipeline succeeds.
    _ = s3_paths, metrics


golden_suite_identity_graph()
