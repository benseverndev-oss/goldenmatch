"""GoldenMatch materialization for dbt.

Usage in dbt model:
    {{ config(materialized='goldenmatch_dedupe', match_config='match.yaml') }}
    SELECT * FROM {{ ref('raw_customers') }}
"""
from __future__ import annotations

from pathlib import Path

import duckdb
from goldenmatch.config.loader import load_config
from goldenmatch.core.pipeline import run_dedupe


def run_goldenmatch_dedupe(
    input_table: str,
    config_path: str,
    output_table: str,
    database: str = ":memory:",
    memory_db_path: str | None = None,
    dataset: str | None = None,
) -> dict:
    """Run GoldenMatch dedupe on a DuckDB table and write results back.

    Args:
        input_table: Source table name in DuckDB
        config_path: Path to GoldenMatch YAML config
        output_table: Destination table name
        database: DuckDB database path
        memory_db_path: Optional MemoryStore SQLite path. When set, any
            field-level corrections (decision='field_correct') matching
            this run's dataset key are applied to the golden output as
            override rows. v1.18.x Phase 3 (#437 surface sync).
        dataset: Optional dataset key for filtering field-level
            corrections. Defaults to ``input_table`` when memory_db_path
            is set but dataset is None.

    Returns:
        Summary dict with record counts, match rate, and (when
        memory_db_path is set) an ``applied_corrections`` count.
    """
    conn = duckdb.connect(database)

    # Read input
    df = conn.execute(f"SELECT * FROM {input_table}").pl()

    # Write to temp CSV for GoldenMatch ingest
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        tmp_path = f.name
        df.write_csv(tmp_path)

    cfg = load_config(config_path)
    result = run_dedupe([(tmp_path, "source")], cfg)

    # Write results to DuckDB
    output_df = result.get("golden") or result.get("output")

    # v1.18.x Phase 3: optional field-level correction overrides from
    # MemoryStore. Iterate field-level corrections for this dataset and
    # patch the golden output. Missing corrections (cluster_id no longer
    # in golden output) are surfaced via the `stale_corrections` counter.
    applied = 0
    stale = 0
    if memory_db_path and output_df is not None:
        from goldenmatch.core.memory.store import MemoryStore

        ds = dataset if dataset is not None else input_table
        try:
            store = MemoryStore(backend="sqlite", path=memory_db_path)
            corrections = list(store.get_corrections(dataset=ds))
            store.close()
        except Exception:  # pragma: no cover -- best-effort, never blocks dedupe
            corrections = []

        field_level = [
            c for c in corrections
            if getattr(c, "decision", None) == "field_correct"
            and getattr(c, "field_name", None)
            and getattr(c, "corrected_value", None) is not None
        ]
        if field_level and "__cluster_id__" in output_df.columns:
            import polars as pl

            # Build a Python-side patch map then materialize.
            patches: dict[tuple[int, str], str] = {}
            for c in field_level:
                key = (int(c.id_a), c.field_name)
                # Latest-write-wins within a dataset (already enforced by
                # MemoryStore trust+recency upsert; we just take the last).
                patches[key] = c.corrected_value

            # Apply patches column-by-column. For each touched column we
            # build an array of overrides aligned to output_df rows.
            cluster_ids = output_df["__cluster_id__"].to_list()
            for fname in {f for _, f in patches.keys()}:
                if fname not in output_df.columns:
                    stale += sum(1 for k in patches if k[1] == fname)
                    continue
                col_vals = output_df[fname].to_list()
                changed = False
                for i, cid in enumerate(cluster_ids):
                    new = patches.get((int(cid), fname))
                    if new is not None:
                        col_vals[i] = new
                        applied += 1
                        changed = True
                if changed:
                    output_df = output_df.with_columns(
                        pl.Series(name=fname, values=col_vals)
                    )

    if output_df is not None:
        conn.execute(f"DROP TABLE IF EXISTS {output_table}")
        conn.execute(f"CREATE TABLE {output_table} AS SELECT * FROM output_df")

    Path(tmp_path).unlink(missing_ok=True)

    stats = result.get("stats", {})
    conn.close()
    return {
        "input_rows": df.height,
        "output_rows": output_df.height if output_df is not None else 0,
        "clusters": stats.get("total_clusters", 0),
        "applied_corrections": applied,
        "stale_corrections": stale,
    }
