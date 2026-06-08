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

Two input modes:
  * `--input <parquet>` (legacy): the person-shaped fixture from generate_fixture.
  * `--dataset <name>`: load (records, truth) from datasets.load_dataset(name),
    write records to a temp parquet, and use a dataset-specific Splink settings
    spec from `_SETTINGS_BY_DATASET`. Datasets without a settings entry (or a
    missing dataset / missing splink) are recorded as `status=skipped` (exit 0),
    never a crash — the panel orchestrator runs GoldenMatch regardless.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path


def _load_datasets_module():
    """Import the sibling datasets.py whether run as a script or from elsewhere."""
    try:
        import datasets as _datasets  # type: ignore
        return _datasets
    except ImportError:
        pass
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import datasets as _datasets  # type: ignore
        return _datasets
    except ImportError:
        ds_path = here / "datasets.py"
        spec = importlib.util.spec_from_file_location("datasets", ds_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


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


# Per-dataset Splink specs. Each entry is a callable
#   build(splink_mod) -> (SettingsCreator, training_blocking_rules: list)
# kept lazy so importing this module never imports splink. The training rules
# must be SELECTIVE (compound keys) or EM training is super-linear at scale.
#
# datasets whose columns don't match any entry below get status=skipped (the
# panel still runs GoldenMatch on them). dblp_acm is intentionally absent —
# bibliographic settings are best-effort / out of scope here, so splink skips it.
def _settings_historical_50k(s):
    """historical_50k real columns: record_id, first_name, surname, dob,
    birth_place, postcode_fake, occupation (see datasets._historical_50k)."""
    SettingsCreator = s["SettingsCreator"]
    block_on = s["block_on"]
    cl = s["cl"]
    settings = SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="record_id",
        blocking_rules_to_generate_predictions=[
            block_on("surname", "substr(dob, 1, 4)"),
            block_on("first_name", "substr(dob, 1, 4)"),
            block_on("postcode_fake"),
        ],
        comparisons=[
            cl.JaroWinklerAtThresholds("first_name", [0.9, 0.7]),
            cl.JaroWinklerAtThresholds("surname", [0.9, 0.7]),
            cl.DamerauLevenshteinAtThresholds("dob", [1, 2]),
            cl.ExactMatch("birth_place"),
            cl.ExactMatch("postcode_fake"),
            cl.ExactMatch("occupation"),
        ],
    )
    # Compound EM blocking rules (selective) keep training tractable.
    training_rules = [
        block_on("surname", "dob"),
        block_on("first_name", "dob"),
    ]
    return settings, training_rules


def _settings_person_default(s, columns):
    """Generic person spec reused for febrl3 / synthetic_person.

    Resolves the actual column names present in the dataset so the same builder
    serves both febrl3 (given_name/surname/date_of_birth/suburb/postcode/state)
    and synthetic_person (first_name/surname/dob/postcode/city). Raises
    KeyError if a usable first-name + surname + date column trio isn't present
    (the caller turns that into status=skipped).
    """
    SettingsCreator = s["SettingsCreator"]
    block_on = s["block_on"]
    cl = s["cl"]
    cols = set(columns)

    def pick(*candidates):
        for c in candidates:
            if c in cols:
                return c
        return None

    first = pick("first_name", "given_name", "givenname")
    surname = pick("surname", "last_name", "lastname")
    dob = pick("dob", "date_of_birth", "birth_date", "birthdate")
    postcode = pick("postcode", "postcode_fake", "zip", "zipcode", "postal_code")
    locality = pick("city", "suburb", "town", "birth_place")
    state = pick("state", "state_cd", "province")

    if first is None or surname is None or dob is None:
        raise KeyError(
            "person settings need first-name + surname + date columns; "
            f"have columns {sorted(cols)}"
        )

    # Blocking: at least one compound key on stable fields. Add postcode if present.
    blocking_rules = [block_on(surname, f"substr({dob}, 1, 4)")]
    blocking_rules.append(block_on(first, f"substr({dob}, 1, 4)"))
    if postcode is not None:
        blocking_rules.append(block_on(postcode))

    comparisons = [
        cl.JaroWinklerAtThresholds(first, [0.9, 0.7]),
        cl.JaroWinklerAtThresholds(surname, [0.9, 0.7]),
        cl.DamerauLevenshteinAtThresholds(dob, [1, 2]),
    ]
    if postcode is not None:
        comparisons.append(cl.ExactMatch(postcode))
    if locality is not None:
        comparisons.append(cl.ExactMatch(locality))
    if state is not None:
        comparisons.append(cl.ExactMatch(state))

    settings = SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="record_id",
        blocking_rules_to_generate_predictions=blocking_rules,
        comparisons=comparisons,
    )
    training_rules = [block_on(surname, dob), block_on(first, dob)]
    return settings, training_rules


# Map dataset -> builder. Builders for febrl3 / synthetic_person resolve columns
# at call time, so they take (s, columns); historical_50k is fixed-schema.
_SETTINGS_BY_DATASET = {
    "historical_50k": ("fixed", _settings_historical_50k),
    "febrl3": ("person", _settings_person_default),
    "synthetic_person": ("person", _settings_person_default),
}


def _default_person_settings(s):
    """Legacy --input person fixture spec (generate_fixture columns:
    record_id, first_name, surname, dob, postcode, city)."""
    SettingsCreator = s["SettingsCreator"]
    block_on = s["block_on"]
    cl = s["cl"]
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
    training_rules = [block_on("surname", "dob"), block_on("first_name", "dob")]
    return settings, training_rules


def _run_splink_linker(
    settings, training_rules, input_view, db_api, s, args, result
) -> None:
    """Shared train -> predict -> cluster -> emit. Mutates `result` in place."""
    Linker = s["Linker"]
    linker = Linker(input_view, settings, db_api=db_api)

    t0 = time.perf_counter()
    linker.training.estimate_probability_two_random_records_match(
        training_rules, recall=0.7
    )
    linker.training.estimate_u_using_random_sampling(max_pairs=args.max_pairs)
    # EM blocking rules must be SELECTIVE or training is super-linear at scale.
    for rule in training_rules:
        linker.training.estimate_parameters_using_expectation_maximisation(rule)
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


def _write_skip(out: Path, reason: str, dataset: str | None) -> None:
    payload = {"engine": "splink", "status": "skipped", "reason": reason}
    if dataset is not None:
        payload["dataset"] = dataset
    _atomic_write(out, payload)
    print(f"[splink] dataset={dataset} status=skipped reason={reason}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=None,
                    help="person-shaped fixture parquet (legacy mode)")
    ap.add_argument("--dataset", type=str, default=None,
                    help="dataset name (datasets.load_dataset); overrides --input")
    ap.add_argument("--rows", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None,
                    help="write {record_id, pred_cluster_id} parquet for accuracy eval")
    ap.add_argument("--truth-out", type=Path, default=None,
                    help="write {record_id, cluster_id} truth parquet (dataset mode)")
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--max-pairs", type=float, default=2e6, help="u-estimation sample size")
    args = ap.parse_args()

    if args.dataset is None and args.input is None:
        ap.error("one of --input or --dataset is required")

    # Splink import: a missing competitor engine is a SKIP, never a crash.
    try:
        import splink.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on
    except ImportError as e:
        _write_skip(args.out, f"splink not installed: {e}", args.dataset)
        return

    s = {
        "DuckDBAPI": DuckDBAPI,
        "Linker": Linker,
        "SettingsCreator": SettingsCreator,
        "block_on": block_on,
        "cl": cl,
    }

    result: dict = {
        "engine": "splink",
        "backend": "duckdb",
        "rows_requested": args.rows,
        "status": "error",
        "threshold": args.threshold,
    }
    if args.dataset is not None:
        result["dataset"] = args.dataset

    t_start = time.perf_counter()
    tmpdir: tempfile.TemporaryDirectory | None = None
    try:
        db_api = DuckDBAPI()

        if args.dataset is not None:
            # Dataset mode: load (records, truth), build a dataset-specific spec.
            datasets = _load_datasets_module()
            try:
                records, truth = datasets.load_dataset(args.dataset)
            except datasets.DatasetUnavailable as e:
                _write_skip(args.out, f"dataset unavailable: {e}", args.dataset)
                return
            except KeyError as e:
                _write_skip(args.out, f"unknown dataset: {e}", args.dataset)
                return

            spec = _SETTINGS_BY_DATASET.get(args.dataset)
            if spec is None:
                _write_skip(
                    args.out,
                    f"splink settings not defined for {args.dataset}",
                    args.dataset,
                )
                return

            # Materialize records to a temp parquet (Splink reads parquet via view).
            tmpdir = tempfile.TemporaryDirectory(prefix="splink_ds_")
            input_path = Path(tmpdir.name) / "records.parquet"
            records.write_parquet(input_path)
            result["rows_requested"] = records.height
            if args.truth_out is not None:
                args.truth_out.parent.mkdir(parents=True, exist_ok=True)
                truth.write_parquet(args.truth_out)

            kind, builder = spec
            try:
                if kind == "person":
                    settings, training_rules = builder(s, records.columns)
                else:
                    settings, training_rules = builder(s)
            except KeyError as e:
                _write_skip(
                    args.out,
                    f"splink settings not applicable to {args.dataset}: {e}",
                    args.dataset,
                )
                return
        else:
            input_path = args.input
            settings, training_rules = _default_person_settings(s)

        # Register the parquet as a DuckDB view so it's read lazily (no pandas
        # materialization at scale). A bare path string gets templated raw into
        # Splink's SQL and fails to parse; a view name resolves cleanly. `_con`
        # is Splink's underlying duckdb connection (stable on the 4.x DuckDBAPI).
        db_api._con.execute(
            f"CREATE OR REPLACE VIEW bench_input AS "
            f"SELECT * FROM read_parquet('{input_path}')"
        )
        _run_splink_linker(settings, training_rules, "bench_input", db_api, s, args, result)
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
            f"[splink] dataset={args.dataset} rows={result.get('rows_requested')} "
            f"status={result['status']} "
            f"dedupe={result.get('dedupe_wall_seconds')}s "
            f"peak_rss={result['peak_rss_mb']}MB pairs={result.get('scored_pairs')}"
        )
        if tmpdir is not None:
            tmpdir.cleanup()


if __name__ == "__main__":
    main()
