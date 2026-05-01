"""Privacy-preserving record linkage (PPRL) across two parties.

Each party encodes their records into Bloom filters locally — raw PII never
leaves either side. This DAG runs at the trusted-third-party position:
  1. fetches each party's encoded shards from their delivery S3 prefix,
  2. runs goldenmatch.pprl_link to score and cluster across the two encoded sets,
  3. writes ID-pair match results back to a shared S3 prefix each party can read,
  4. emits an audit row.

Tunable: bloom-filter security level, threshold, encoding contract.

Requires:
    pip install apache-airflow goldenmatch[pprl] \\
                apache-airflow-providers-amazon apache-airflow-providers-postgres polars
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

PARTY_A_PREFIX = "pprl/inbound/party_a/"
PARTY_B_PREFIX = "pprl/inbound/party_b/"
RESULTS_PREFIX = "pprl/results/{{ ds }}/"
AUDIT_TABLE = "audit.pprl_runs"

PPRL_THRESHOLD = 0.85
SECURITY_LEVEL = "high"  # standard | high | paranoid
LINKAGE_FIELDS = ["first_name", "last_name", "dob", "zip"]


@dag(
    dag_id="golden_suite_pprl_linkage",
    description="Two-party privacy-preserving record linkage. Trusted-third-party model.",
    schedule="@weekly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-platform",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["golden-suite", "pprl", "privacy"],
)
def golden_suite_pprl_linkage():
    """PPRL across party A and party B."""

    @task
    def fetch_encoded(prefix: str, label: str) -> str:
        """Pull all encoded shards for one party from their inbound prefix."""
        from pathlib import Path

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        local_dir = Path(f"/tmp/golden_suite/pprl/{label}")
        local_dir.mkdir(parents=True, exist_ok=True)

        hook = S3Hook(aws_conn_id="aws_default")
        keys = hook.list_keys(bucket_name=Variable.get("golden_suite_bucket"),
                              prefix=prefix) or []
        if not keys:
            raise ValueError(f"No encoded shards for {label} under {prefix}.")

        for key in keys:
            local = local_dir / Path(key).name
            hook.get_key(key, Variable.get("golden_suite_bucket")).download_file(
                Filename=str(local)
            )
        return str(local_dir)

    @task
    def link(party_a_dir: str, party_b_dir: str) -> dict[str, Any]:
        """Run PPRL linkage. Returns a results dict with match pairs."""
        import glob

        import polars as pl

        from goldenmatch.pprl.protocol import PPRLConfig, run_pprl

        a = pl.concat([pl.read_parquet(f) for f in glob.glob(f"{party_a_dir}/*.parquet")])
        b = pl.concat([pl.read_parquet(f) for f in glob.glob(f"{party_b_dir}/*.parquet")])

        config = PPRLConfig(
            fields=LINKAGE_FIELDS,
            threshold=PPRL_THRESHOLD,
            security_level=SECURITY_LEVEL,
        )
        result = run_pprl(party_a=a, party_b=b, config=config)
        return {
            "matches": result.matches,  # list[(a_id, b_id, score)]
            "stats": {
                "party_a_rows": a.height,
                "party_b_rows": b.height,
                "match_count": len(result.matches),
                "match_rate_a": len(result.matches) / max(a.height, 1),
            },
        }

    @task
    def publish_results(linkage: dict[str, Any], results_prefix: str) -> str:
        """Write match pairs to the shared results prefix in S3."""
        from pathlib import Path

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import polars as pl

        match_df = pl.DataFrame(
            linkage["matches"],
            schema=["party_a_id", "party_b_id", "score"],
            orient="row",
        )
        local = Path("/tmp/golden_suite/pprl/matches.parquet")
        match_df.write_parquet(local)

        s3 = S3Hook(aws_conn_id="aws_default")
        out_key = results_prefix.rstrip("/") + "/matches.parquet"
        s3.load_file(filename=str(local),
                     key=out_key,
                     bucket_name=Variable.get("golden_suite_bucket"),
                     replace=True)
        return f"s3://{Variable.get("golden_suite_bucket")}/{out_key}"

    @task
    def audit(linkage: dict[str, Any], result_uri: str, **context) -> None:
        """Audit row: who, when, how many, where the result lives."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        PostgresHook(postgres_conn_id="postgres_default").run(
            f"""
            INSERT INTO {AUDIT_TABLE}
                (run_id, ds, party_a_rows, party_b_rows, match_count,
                 match_rate_a, threshold, security_level, result_uri)
            VALUES (%(run_id)s, %(ds)s, %(a)s, %(b)s, %(m)s, %(rate)s,
                    %(thr)s, %(sec)s, %(uri)s)
            """,
            parameters={
                "run_id": context["run_id"],
                "ds": context["ds"],
                "a": linkage["stats"]["party_a_rows"],
                "b": linkage["stats"]["party_b_rows"],
                "m": linkage["stats"]["match_count"],
                "rate": linkage["stats"]["match_rate_a"],
                "thr": PPRL_THRESHOLD,
                "sec": SECURITY_LEVEL,
                "uri": result_uri,
            },
        )

    a_dir = fetch_encoded(PARTY_A_PREFIX, "party_a")
    b_dir = fetch_encoded(PARTY_B_PREFIX, "party_b")
    linkage = link(a_dir, b_dir)
    uri = publish_results(linkage, RESULTS_PREFIX)
    audit(linkage, uri)


golden_suite_pprl_linkage()
