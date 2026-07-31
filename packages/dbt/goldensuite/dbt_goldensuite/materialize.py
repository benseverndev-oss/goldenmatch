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
    config_path: str | None = None,
    output_table: str | None = None,
    database: str = ":memory:",
    memory_db_path: str | None = None,
    dataset: str | None = None,
    *,
    probabilistic: bool = False,
) -> dict:
    """Run GoldenMatch dedupe on a DuckDB table and write results back.

    Args:
        input_table: Source table name in DuckDB
        config_path: Path to GoldenMatch YAML config. Omit (and pass
            ``probabilistic=True`` or leave the default deterministic
            auto-config) to run with zero config file.
        output_table: Destination table name
        database: DuckDB database path
        probabilistic: When True (and no ``config_path``), build a
            Fellegi-Sunter config via ``auto_configure_probabilistic_df``
            with no config file. Mutually exclusive with ``config_path``.
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
    if output_table is None:
        raise TypeError("run_goldenmatch_dedupe() requires output_table")
    if config_path is not None and probabilistic:
        raise ValueError("pass either config_path or probabilistic=True, not both")

    conn = duckdb.connect(database)

    # Read input
    df = conn.execute(f"SELECT * FROM {input_table}").pl()

    # Write to temp CSV for GoldenMatch ingest
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        tmp_path = f.name
        df.write_csv(tmp_path)

    if config_path is not None:
        cfg = load_config(config_path)
    else:
        from goldenmatch.core.autoconfig import (
            auto_configure_df,
            auto_configure_probabilistic_df,
        )
        cfg = (auto_configure_probabilistic_df(df) if probabilistic
               else auto_configure_df(df))
    result = run_dedupe([(tmp_path, "source")], cfg)

    # Write results to DuckDB. NB: `a or b` triggers DataFrame.__bool__
    # ("truth value of a DataFrame is ambiguous") on polars frames — the result
    # values are DataFrames, so use an explicit None check, not `or`.
    output_df = result.get("golden")
    if output_df is None:
        output_df = result.get("output")
    # D3: the pipeline dict emits pa.Table; tests (and any legacy caller)
    # may still hand a polars frame -- normalize to Arrow here.
    if output_df is not None and not hasattr(output_df, "num_rows"):
        output_df = output_df.to_arrow()

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
        if field_level and "__cluster_id__" in output_df.column_names:
            # D3 (arrow descent): the internal dict emits pa.Table; the patch
            # loop and the duckdb registration run on Arrow (duckdb's
            # replacement scan reads pa.Table natively).
            import pyarrow as pa

            # Build a Python-side patch map then materialize.
            patches: dict[tuple[int, str], str] = {}
            for c in field_level:
                key = (int(c.id_a), c.field_name)
                # Latest-write-wins within a dataset (already enforced by
                # MemoryStore trust+recency upsert; we just take the last).
                patches[key] = c.corrected_value

            # Apply patches column-by-column. For each touched column we
            # build an array of overrides aligned to output_df rows.
            cluster_ids = output_df["__cluster_id__"].to_pylist()
            for fname in {f for _, f in patches.keys()}:
                if fname not in output_df.column_names:
                    stale += sum(1 for k in patches if k[1] == fname)
                    continue
                col_vals = output_df[fname].to_pylist()
                changed = False
                for i, cid in enumerate(cluster_ids):
                    new = patches.get((int(cid), fname))
                    if new is not None:
                        col_vals[i] = new
                        applied += 1
                        changed = True
                if changed:
                    idx = output_df.column_names.index(fname)
                    output_df = output_df.set_column(
                        idx, fname, pa.array(col_vals, type=output_df.schema.field(fname).type)
                    )

    if output_df is not None:
        conn.execute(f"DROP TABLE IF EXISTS {output_table}")
        conn.execute(f"CREATE TABLE {output_table} AS SELECT * FROM output_df")

    Path(tmp_path).unlink(missing_ok=True)

    stats = result.get("stats", {})
    conn.close()
    return {
        "input_rows": df.height,
        "output_rows": output_df.num_rows if output_df is not None else 0,
        "clusters": stats.get("total_clusters", 0),
        "applied_corrections": applied,
        "stale_corrections": stale,
    }


def run_goldenmatch_crosswalk(
    input_table: str,
    source_pk: str,
    output_table: str | None = None,
    database: str = ":memory:",
    source_name: str = "source",
    store_path: str | None = None,
    dataset: str | None = None,
    config_path: str | None = None,
) -> dict:
    """Materialize a durable ``{source, source_pk, resolved_entity_id}`` crosswalk.

    Runs GoldenMatch wedge-B (``goldenmatch.semantic.build_resolved_crosswalk``):
    resolves identity once and writes the *conformed join key every metric can
    group by* — the durable control-plane ``entity_id`` (UUIDv7), NOT the
    run-local cluster id the ``goldenmatch_dedupe`` materialization emits. Point
    a semantic layer's joins at ``resolved_entity_id`` and every metric inherits
    correct, conformed joins ("resolve once, every metric inherits correct joins").

    Unlike ``run_goldenmatch_dedupe``, the crosswalk has no pure-SQL warehouse UDF
    equivalent: the resolved id comes from the stateful Identity Control Plane
    (an IdentityStore), so this is a Python-helper materialization (call it from a
    dbt Python model or out-of-band).

    Usage in a dbt Python model::

        def model(dbt, session):
            from dbt_goldensuite.materialize import run_goldenmatch_crosswalk
            run_goldenmatch_crosswalk(
                input_table="raw_customers", source_pk="customer_id",
                output_table="customer_crosswalk",
                database=session.execute("PRAGMA database_list").fetchone()[2],
                store_path="/warehouse/.goldenmatch/identity.db",  # durable ids
            )
            return session.sql("SELECT * FROM customer_crosswalk")

    Args:
        input_table: source table name in DuckDB.
        source_pk: the column holding each record's source primary key.
        output_table: destination table name (the crosswalk). Required.
        database: DuckDB database path.
        source_name: logical source name (the ``{source}`` half of the record id).
        store_path: IdentityStore SQLite path. Pass a stable path to make entity
            ids durable across runs ("resolve once"); omit for an ephemeral run.
        dataset: identity-graph dataset scope (defaults to ``source_name``).
        config_path: optional GoldenMatch YAML config; zero-config otherwise.

    Returns:
        Summary dict: ``input_rows``, ``n_records``, ``n_entities``, ``unmapped``,
        ``reduction_ratio``, ``store_path`` (echoed when durable), ``durable``.
    """
    if output_table is None:
        raise TypeError("run_goldenmatch_crosswalk() requires output_table")

    from goldenmatch.semantic import build_resolved_crosswalk

    conn = duckdb.connect(database)
    df = conn.execute(f"SELECT * FROM {input_table}").pl()  # noqa: F841 (used below)

    cfg = load_config(config_path) if config_path else None
    crosswalk = build_resolved_crosswalk(
        df,
        source_pk=source_pk,
        source_name=source_name,
        dataset=dataset,
        store_path=store_path,
        config=cfg,
    )
    # `crosswalk_table` is referenced by DuckDB's replacement scan in the SQL
    # string below (zero-copy pa.Table view), so it is intentionally "unused".
    crosswalk_table = crosswalk.table  # noqa: F841
    conn.execute(f"DROP TABLE IF EXISTS {output_table}")
    conn.execute(f"CREATE TABLE {output_table} AS SELECT * FROM crosswalk_table")
    conn.close()

    return {
        "input_rows": df.height,
        "n_records": crosswalk.n_records,
        "n_entities": crosswalk.n_entities,
        "unmapped": crosswalk.unmapped,
        "reduction_ratio": crosswalk.reduction_ratio,
        "store_path": crosswalk.store_path,
        "durable": store_path is not None,
    }
