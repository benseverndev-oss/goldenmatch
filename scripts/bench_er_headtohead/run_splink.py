#!/usr/bin/env python
"""Single-datapoint Splink (DuckDB backend) dedupe runner for the ER head-to-head.

Runs ONE (engine=splink, rows=N) measurement in its own process; the OS reclaims
all memory on exit. Splink has no zero-config mode, so we give it an idiomatic,
reasonable settings spec (compound blocking + standard comparisons) that mirrors
the blocking semantics GoldenMatch's auto-config lands on, then record the
scored-pair count so any blocking-aggressiveness difference is visible, not hidden.

Sub-phases (train / predict / cluster) are timed separately for transparency;
`dedupe_wall_seconds` is their sum — the fair end-to-end cost, paralleling
GoldenMatch's auto_configure+dedupe. Counts come from DuckDB relations, never a
pandas materialization, so this stays memory-bounded at 25M/100M.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def _count(splink_df) -> int | None:
    """Row count without materialising to pandas (DuckDB relation fast path)."""
    try:
        return int(splink_df.as_duckdbpyrelation().count("*").fetchone()[0])
    except Exception:
        try:
            return len(splink_df.as_pandas_dataframe())
        except Exception:
            return None


def _distinct_clusters(splink_df) -> int | None:
    try:
        rel = splink_df.as_duckdbpyrelation()
        return int(rel.aggregate("count(distinct cluster_id) AS c").fetchone()[0])
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None,
                    help="write {record_id, pred_cluster_id} parquet for accuracy eval")
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--max-pairs", type=float, default=2e6, help="u-estimation sample size")
    args = ap.parse_args()

    result: dict = {
        "engine": "splink",
        "backend": "duckdb",
        "rows_requested": args.rows,
        "status": "error",
        "threshold": args.threshold,
    }
    t_start = time.perf_counter()
    try:
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on
        import splink.comparison_library as cl

        db_api = DuckDBAPI()

        settings = SettingsCreator(
            link_type="dedupe_only",
            unique_id_column_name="record_id",
            blocking_rules_to_generate_predictions=[
                block_on("surname", "substr(dob, 1, 4)"),
                block_on("first_name", "substr(dob, 1, 4)"),
                block_on("postcode"),
            ],
            comparisons=[
                cl.JaroWinklerAtThresholds("first_name", [0.9, 0.7]),
                cl.JaroWinklerAtThresholds("surname", [0.9, 0.7]),
                cl.DamerauLevenshteinAtThresholds("dob", [1, 2]),
                cl.DamerauLevenshteinAtThresholds("postcode", [1, 2]),
                cl.ExactMatch("city"),
            ],
        )

        # Register the parquet as a DuckDB view so it's read lazily (no pandas
        # materialization at scale). A bare path string gets templated raw into
        # Splink's SQL and fails to parse; a view name resolves cleanly. `_con`
        # is Splink's underlying duckdb connection (stable on the 4.x DuckDBAPI).
        db_api._con.execute(
            f"CREATE OR REPLACE VIEW bench_input AS "
            f"SELECT * FROM read_parquet('{args.input}')"
        )
        linker = Linker("bench_input", settings, db_api=db_api)

        t0 = time.perf_counter()
        linker.training.estimate_probability_two_random_records_match(
            [block_on("surname", "dob")], recall=0.7
        )
        linker.training.estimate_u_using_random_sampling(max_pairs=args.max_pairs)
        # EM blocking rules must be SELECTIVE or training is super-linear at scale
        # (block_on a single high-frequency field generates enormous comparison
        # sets). Compound keys keep the EM training set tractable at 5M/25M.
        linker.training.estimate_parameters_using_expectation_maximisation(
            block_on("surname", "dob")
        )
        linker.training.estimate_parameters_using_expectation_maximisation(
            block_on("first_name", "dob")
        )
        train_wall = time.perf_counter() - t0

        t0 = time.perf_counter()
        df_predict = linker.inference.predict(threshold_match_probability=0.5)
        scored_pairs = _count(df_predict)
        predict_wall = time.perf_counter() - t0

        t0 = time.perf_counter()
        df_clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
            df_predict, threshold_match_probability=args.threshold
        )
        cluster_count = _distinct_clusters(df_clusters)
        cluster_wall = time.perf_counter() - t0

        # Per-record cluster assignment for accuracy eval (DuckDB -> parquet, no
        # pandas materialization). Splink names the entity column `cluster_id`.
        if args.pred_out is not None:
            try:
                rel = df_clusters.as_duckdbpyrelation()
                rel.project("record_id, cluster_id AS pred_cluster_id").write_parquet(
                    str(args.pred_out)
                )
            except Exception as e:  # noqa: BLE001 - eval is best-effort
                result["pred_emit_error"] = f"{type(e).__name__}: {e}"

        result.update(
            status="ok",
            train_wall_seconds=round(train_wall, 2),
            predict_wall_seconds=round(predict_wall, 2),
            cluster_wall_seconds=round(cluster_wall, 2),
            dedupe_wall_seconds=round(train_wall + predict_wall + cluster_wall, 2),
            scored_pairs=scored_pairs,
            cluster_count=cluster_count,
        )
    except MemoryError as e:
        result.update(status="OOM", error=f"{type(e).__name__}: {e}")
    except BaseException as e:  # noqa: BLE001
        result.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(
            f"[splink] rows={args.rows:,} status={result['status']} "
            f"dedupe={result.get('dedupe_wall_seconds')}s "
            f"peak_rss={result['peak_rss_mb']}MB pairs={result.get('scored_pairs')}"
        )


if __name__ == "__main__":
    main()
