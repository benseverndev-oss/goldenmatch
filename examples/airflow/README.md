# Airflow DAGs for the Golden Suite

Drop-in DAG examples that wire the Golden Suite into a real Airflow deployment. Copy the file you want into your Airflow `dags/` folder, adjust the knobs at the top, and ship.

These are **examples**, not a published DAG library. Read them, adapt them, and own them — every team's data shape, connection set, and SLAs are different.

## DAGs

| File | What it does | Cadence | Trigger |
|---|---|---|---|
| `golden_suite_daily_dedupe.py` | Pull CSV from S3 → GoldenCheck scan → GoldenFlow standardize → GoldenMatch dedupe → write golden records back to S3 + summary metrics row to Postgres. | Daily at 04:00 UTC | Scheduled |
| `golden_suite_incremental_match.py` | Poll an `incoming/` S3 prefix every 15 min. For each new file, match each record against the canonical store using `goldenmatch.match_one`. Auto-merge ≥0.95, queue for review 0.75–0.95, append as new <0.75. | Every 15 min | Scheduled |
| `golden_suite_pprl_linkage.py` | Trusted-third-party PPRL across two parties. Each party uploads encoded Bloom-filter shards; the DAG runs `goldenmatch.pprl_link` and writes match pairs to a results prefix. Raw PII never crosses the boundary. | Weekly | Scheduled |
| `golden_suite_schema_align_and_load.py` | Onboard a new partner / source. InferMap aligns its columns to your canonical schema (cached after first run), GoldenFlow standardizes, then upsert into canonical. Fails loudly if column-mapping confidence is below threshold. | None | Manual (`conf` overrides source name + key) |

## Prerequisites

Each DAG declares its own pip deps in its docstring. Common stack:

```bash
pip install apache-airflow>=2.7 polars
pip install goldenpipe[full] goldenmatch[postgres,pprl] goldencheck goldenflow infermap
pip install apache-airflow-providers-amazon apache-airflow-providers-postgres
```

Compatible with Airflow 2.7+ via TaskFlow API. Tested at 2.10. Should run on 3.x — the only known caveat is HITL operators (not used here).

## Connections (Airflow UI → Admin → Connections)

| Conn ID | Type | Used by |
|---|---|---|
| `aws_default` | Amazon Web Services | All four (S3 read/write) |
| `postgres_default` | Postgres | daily_dedupe (metrics), incremental_match (canonical store + review queue), pprl_linkage (audit), schema_align (mappings + canonical) |

## Variables (Airflow UI → Admin → Variables)

| Key | Example | Used by |
|---|---|---|
| `golden_suite_bucket` | `acme-data-lake` | All four DAGs |

## Tables you'll need

```sql
-- Daily run summary (golden_suite_daily_dedupe)
CREATE TABLE analytics.golden_suite_runs (
    run_id text, ds date, total_records int, total_clusters int,
    duplicates int, match_rate float, golden_uri text,
    created_at timestamptz DEFAULT now()
);

-- Canonical store (incremental_match, schema_align)
CREATE TABLE warehouse.customers_canonical (
    id bigserial primary key,
    -- … your canonical columns here …
    last_seen timestamptz DEFAULT now(),
    merge_count int DEFAULT 0
);

-- Human review queue (incremental_match)
CREATE TABLE warehouse.customers_review_queue (
    id bigserial primary key,
    payload jsonb,
    candidate_id bigint references warehouse.customers_canonical(id),
    score float,
    queued_at timestamptz,
    decided_at timestamptz,
    decision text  -- approve | reject
);

-- PPRL audit (pprl_linkage)
CREATE TABLE audit.pprl_runs (
    run_id text, ds date,
    party_a_rows int, party_b_rows int, match_count int, match_rate_a float,
    threshold float, security_level text, result_uri text,
    created_at timestamptz DEFAULT now()
);

-- InferMap mapping cache (schema_align)
CREATE TABLE warehouse.source_column_mappings (
    source_name text primary key,
    mapping jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz
);
```

## Knobs you'll tune

Each DAG file has a constants block at the top. Most common edits:

- **`MATCH_*` thresholds** (daily / incremental) — lower for high recall, higher for high precision. Run on a labeled sample first.
- **`AUTO_MERGE_THRESHOLD` / `REVIEW_THRESHOLD`** (incremental) — depends on your tolerance for false-positive merges. Most teams start at 0.95 / 0.75.
- **`SECURITY_LEVEL`** (PPRL) — `standard` for fastest, `paranoid` for highest privacy guarantees. `high` is a good default.
- **`MIN_MAPPING_CONFIDENCE`** (schema align) — raise this to force humans to validate any uncertain mapping.

## Pattern notes

- Every task is **idempotent or marker-protected** (the incremental DAG renames processed S3 keys to `*.processed`). Safe to retry.
- DAGs **fail loudly on empty input** — silent zero-row processing has bitten enough data teams that I'd rather a red square than a missed dedupe.
- `goldenflow.transform_df` is called on **already-loaded Polars DataFrames**, not file paths. Lets you compose with whatever in-memory transforms you want before/after.
- **No cross-DAG XComs of large payloads** — outputs go through S3 / Postgres so each task can be retried independently and the scheduler isn't ferrying parquet blobs.
- **Connection / Variable names are conventional defaults** (`aws_default`, `postgres_default`, `golden_suite_bucket`). Rename to match your platform's standards.

## Suite-wide alternative: GoldenPipe

If your pipeline is "Check → Flow → Match" with no intermediate custom logic, GoldenPipe collapses the whole thing into one declarative config:

```python
import goldenpipe as gp
result = gp.Pipeline.from_yaml("pipeline.yaml").run("data.csv")
```

You'd wrap that in a single `@task` and skip the per-stage breakdown the example DAGs demonstrate. Use the explicit-stage pattern when you need stage-level retries, mid-pipeline branching, or to inject your own logic between Suite tools.

See [`packages/python/goldenpipe`](../../packages/python/goldenpipe/README.md) for GoldenPipe details.

## Status reporting

If you want each DAG run to push status to the team:
- **Slack on success/failure** — wire `on_success_callback` / `on_failure_callback` at the DAG level.
- **Cluster-quality alerting** — `goldenmatch` ships per-cluster confidence; surface "weak" clusters from the daily run as a separate task that posts to a review channel.

These hooks are intentionally *not* in the examples — they vary too much per org. Add them where you need them.
