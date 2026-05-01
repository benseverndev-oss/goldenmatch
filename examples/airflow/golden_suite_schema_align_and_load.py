"""New-source onboarding: infermap → goldenflow → upsert into canonical.

When a new partner / system starts feeding you data, the columns rarely
match your canonical schema. This DAG:
  1. profiles a sample of the new source,
  2. uses InferMap to align its columns to the canonical schema,
  3. applies the mapping (rename + drop unmapped),
  4. standardizes via GoldenFlow,
  5. upserts into the canonical store.

Once a source's mapping is established, it's cached in a `mappings` table —
subsequent runs reuse the cached mapping and skip the InferMap step.

Tunable: minimum confidence to auto-accept a mapping; below that, the DAG
fails so a human can review (no silent guessing).

Requires:
    pip install apache-airflow infermap goldenflow \\
                apache-airflow-providers-amazon apache-airflow-providers-postgres polars
"""
from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task

CANONICAL_SCHEMA_FILE = "/opt/airflow/dags/golden_suite/canonical_customers.yaml"
MAPPINGS_TABLE = "warehouse.source_column_mappings"
CANONICAL_TABLE = "warehouse.customers_canonical"

MIN_MAPPING_CONFIDENCE = 0.7  # below this → human-review required, DAG fails


@dag(
    dag_id="golden_suite_schema_align_and_load",
    description="Onboard a new data source: InferMap → GoldenFlow → upsert.",
    schedule=None,  # triggered manually with conf={"source": "...", "key": "..."}
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    params={
        "source_name": "partner_acme",
        "source_key": "incoming/partner_acme/2026-05-01/customers.csv",
    },
    default_args={
        "owner": "data-platform",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["golden-suite", "onboarding", "infermap"],
)
def golden_suite_schema_align_and_load():
    """Schema align a new source, then load into canonical."""

    @task
    def fetch(**context) -> str:
        """Pull the source file to local."""
        from pathlib import Path

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        params = context["params"]
        local = Path(f"/tmp/golden_suite/{params['source_key']}")
        local.parent.mkdir(parents=True, exist_ok=True)
        S3Hook(aws_conn_id="aws_default").get_key(
            params["source_key"], "{{ var.value.golden_suite_bucket }}"
        ).download_file(Filename=str(local))
        return str(local)

    @task
    def get_or_build_mapping(local_path: str, **context) -> dict[str, str]:
        """Reuse a cached mapping for this source, or build one with InferMap."""
        import json

        from airflow.providers.postgres.hooks.postgres import PostgresHook
        import polars as pl

        source_name = context["params"]["source_name"]
        pg = PostgresHook(postgres_conn_id="postgres_default")

        cached = pg.get_first(
            f"SELECT mapping FROM {MAPPINGS_TABLE} WHERE source_name = %(s)s",
            parameters={"s": source_name},
        )
        if cached:
            return json.loads(cached[0])

        # No cache — build with InferMap.
        import infermap

        sample = pl.read_csv(local_path, encoding="utf8-lossy",
                             ignore_errors=True, n_rows=1000)
        result = infermap.map(
            source=sample.to_dicts(),
            schema_file=CANONICAL_SCHEMA_FILE,
        )

        low_confidence = [
            m for m in result.mappings
            if m.confidence < MIN_MAPPING_CONFIDENCE
        ]
        if low_confidence:
            details = ", ".join(
                f"{m.source}→{m.target}({m.confidence:.0%})" for m in low_confidence
            )
            raise ValueError(
                f"Low-confidence column mappings for {source_name}: {details}. "
                f"Review and either widen MIN_MAPPING_CONFIDENCE or build the "
                f"mapping by hand and INSERT into {MAPPINGS_TABLE}."
            )

        mapping = {m.source: m.target for m in result.mappings}
        pg.run(
            f"INSERT INTO {MAPPINGS_TABLE} (source_name, mapping, created_at) "
            "VALUES (%(s)s, %(m)s::jsonb, NOW())",
            parameters={"s": source_name, "m": json.dumps(mapping)},
        )
        return mapping

    @task
    def apply_and_transform(local_path: str, mapping: dict[str, str]) -> str:
        """Rename per mapping, drop unmapped, then GoldenFlow standardize."""
        from pathlib import Path

        import goldenflow
        import polars as pl

        df = pl.read_csv(local_path, encoding="utf8-lossy", ignore_errors=True)

        # Drop columns InferMap couldn't confidently align.
        df = df.select([c for c in df.columns if c in mapping])
        # Rename to canonical names.
        df = df.rename(mapping)

        result = goldenflow.transform_df(df)

        out = Path(local_path).with_name("aligned.parquet")
        result.df.write_parquet(out)
        return str(out)

    @task
    def upsert_canonical(transformed_path: str) -> int:
        """Upsert into canonical store. Returns rows written."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        import polars as pl

        df = pl.read_parquet(transformed_path)
        rows = df.to_dicts()
        if not rows:
            return 0

        pg = PostgresHook(postgres_conn_id="postgres_default")
        cols = list(rows[0].keys())
        placeholders = ", ".join(f"%({c})s" for c in cols)
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
        sql = (
            f"INSERT INTO {CANONICAL_TABLE} ({', '.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {update_clause}"
        )
        pg.insert_rows = None  # noqa: E501 — silence linter; we use run() loop
        for row in rows:
            pg.run(sql, parameters=row)
        return len(rows)

    local = fetch()
    mapping = get_or_build_mapping(local)
    aligned = apply_and_transform(local, mapping)
    upsert_canonical(aligned)


golden_suite_schema_align_and_load()
