#!/usr/bin/env python
"""Splink-conversion parity gate for the ER head-to-head bench.

The success bar for the Splink -> GoldenMatch config converter
(docs/superpowers/specs/2026-07-13-splink-config-converter-design.md):
a `goldenmatch.from_splink`-converted config must land within F1 0.05 of
native Splink on this harness's shared datasets.

Flow (one dataset, one process):
  1. Load (records, truth) via datasets.load_dataset.
  2. Build the SAME Splink SettingsCreator run_splink.py uses for that
     dataset (its builder is imported, not copied).
  3. Serialize it with `SettingsCreator.create_settings_dict(sql_dialect_str=
     "duckdb")` -- the REAL serialized dict, sql_condition strings and all --
     and feed it to `goldenmatch.from_splink`. The full ConversionReport is
     printed.
  4. Run native Splink (run_splink._run_splink_linker, reused) -> pred parquet.
  5. Run GoldenMatch `dedupe_df` with the CONVERTED config -> pred parquet.
  6. Score both with evaluate.evaluate (the panel's shared scorer) against the
     same truth parquet; gate on delta = splink_f1 - gm_f1 > 0.05 (a converted
     config that BEATS Splink passes; only materially-worse fails).

Default dataset: synthetic_person (5K rows, generated locally by
generate_fixture with seed=42 -- no downloads, deterministic, person-shaped
columns matching run_splink's `_settings_person_default` builder).
historical_50k also works (`--dataset historical_50k`) but needs
splink_datasets' download and a much slower EM.

Measured 2026-07-13 (Windows dev box, splink 4.0.16, synthetic_person 5K,
threshold 0.85):
    splink   P/R/F1 = 1.0/0.9927/0.9964     (wall 5.5s)
    convGM   P/R/F1 = 0.9992/0.954/0.9761   (wall 6.8s)
    splink_f1=0.9964  converted_gm_f1=0.9761  delta=0.0203  (gate: <= 0.05) PASS
The run also flushed out a real converter bug now fixed in from_splink.py:
Splink 4's `create_settings_dict` paren-wraps every blocking-rule conjunct
(`(l."surname" = r."surname") AND (SUBSTRING(...) = ...)`), which the
blocking recognizer used to drop entirely (losing 2 of 3 blocking keys).

Run (Windows dev box):
    $env:PYTHONPATH = "<repo>\\packages\\python\\goldenmatch"
    $env:POLARS_SKIP_CPU_CHECK = "1"; $env:PYTHONIOENCODING = "utf-8"
    python scripts\\bench_er_headtohead\\run_converted_splink.py
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

_HERE = Path(__file__).resolve().parent

# Must be set before goldenmatch/polars imports (Windows WMI hang guard is a
# no-op elsewhere; AUTOCONFIG_MEMORY off keeps runs reproducible).
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

_F1_GATE = 0.05


def _import_sibling(name: str):
    """Import a sibling bench module by file path (script dir, not a package)."""
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    path = _HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def _import_run_splink():
    """Import run_splink.py, shimming the Unix-only `resource` module on
    Windows (run_splink imports it at module top; we only reuse its settings
    builders + `_run_splink_linker`, neither of which touches rusage)."""
    try:
        import resource  # noqa: F401
    except ImportError:
        # Shim assumes `resource` is only referenced inside function bodies
        # of run_splink (module top-level just imports it).
        shim = types.ModuleType("resource")
        shim.RUSAGE_SELF = 0
        shim.getrusage = lambda _who: types.SimpleNamespace(ru_maxrss=0)
        sys.modules["resource"] = shim
    return _import_sibling("run_splink")


def _print_report(conversion) -> None:
    print(f"[convert] ConversionReport: {conversion.report.summary()}")
    for f in conversion.report.findings:
        arrow = f" -> {f.mapped_to}" if f.mapped_to else ""
        print(f"[convert]   [{f.severity}] {f.splink_path}: {f.message}{arrow}")


def _run_native_splink(run_splink_mod, settings, training_rules, s,
                       records, pred_out: Path, threshold: float,
                       max_pairs: float) -> dict:
    """Native Splink train -> predict -> cluster via run_splink's shared path."""
    from splink import DuckDBAPI

    result: dict = {}
    args = argparse.Namespace(
        max_pairs=max_pairs, threshold=threshold, pred_out=pred_out
    )
    with tempfile.TemporaryDirectory(prefix="conv_splink_") as td:
        input_path = Path(td) / "records.parquet"
        pq.write_table(records, input_path)
        db_api = DuckDBAPI()
        db_api._con.execute(
            f"CREATE OR REPLACE VIEW bench_input AS "
            f"SELECT * FROM read_parquet('{input_path}')"
        )
        run_splink_mod._run_splink_linker(
            settings, training_rules, "bench_input", db_api, s, args, result
        )
    if "pred_emit_error" in result:
        raise RuntimeError(f"splink pred emit failed: {result['pred_emit_error']}")
    return result


def _run_converted_goldenmatch(config, records, pred_out: Path) -> float:
    """GoldenMatch dedupe with the converted config; writes {record_id,
    pred_cluster_id} parquet in STRING record_id space (mirrors run_panel.py's
    __row_id__ -> record_id remap). Returns the dedupe wall."""
    import numpy as np

    try:
        from goldenmatch import dedupe_df
    except ImportError:  # older layouts expose this on _api
        from goldenmatch._api import dedupe_df

    rid = records.column("record_id").to_pylist()
    t0 = time.perf_counter()
    ded = dedupe_df(records, config=config)
    wall = time.perf_counter() - t0

    clusters = getattr(ded, "clusters", None) or {}
    rec_ids: list[str] = []
    pred_cids: list[int] = []
    for cid, c in clusters.items():
        members = c["members"] if isinstance(c, dict) else c.members
        for m in members:
            rec_ids.append(str(rid[m]))
            pred_cids.append(cid)

    pred_out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "record_id": pa.array(rec_ids, pa.string()),
                "pred_cluster_id": pa.array(np.asarray(pred_cids, dtype=np.int64)),
            }
        ),
        pred_out,
        compression="zstd",
    )
    return wall


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="synthetic_person",
                    help="dataset name with a run_splink settings entry "
                         "(synthetic_person, historical_50k, febrl3)")
    ap.add_argument("--max-rows", type=int, default=0,
                    help="subsample records to N rows (0 = all)")
    ap.add_argument("--threshold", type=float, default=0.85,
                    help="Splink clustering threshold (panel default)")
    ap.add_argument("--max-pairs", type=float, default=2e6,
                    help="Splink u-estimation sample size")
    ap.add_argument("--out-dir", type=Path,
                    default=Path(".profile_tmp/converted_splink"))
    args = ap.parse_args()

    run_splink_mod = _import_run_splink()
    datasets = _import_sibling("datasets")
    evaluate_mod = _import_sibling("evaluate")

    import splink.comparison_library as cl
    from splink import DuckDBAPI, Linker, SettingsCreator, block_on

    from goldenmatch.config.from_splink import from_splink

    s = {
        "DuckDBAPI": DuckDBAPI,
        "Linker": Linker,
        "SettingsCreator": SettingsCreator,
        "block_on": block_on,
        "cl": cl,
    }

    # 1. Dataset.
    records, truth = datasets.load_dataset(args.dataset)
    if args.max_rows and records.num_rows > args.max_rows:
        records = records.slice(0, args.max_rows)
        truth = truth.filter(
            pc.is_in(
                truth.column("record_id"),
                value_set=records.column("record_id").combine_chunks(),
            )
        )
    print(f"[dataset] {args.dataset}: {records.num_rows} records, "
          f"{len(pc.unique(truth.column('cluster_id')))} true clusters")

    # 2. The SAME settings run_splink.py uses for this dataset.
    spec = run_splink_mod._SETTINGS_BY_DATASET.get(args.dataset)
    if spec is None:
        print(f"run_splink has no settings for dataset {args.dataset!r}; "
              f"have {sorted(run_splink_mod._SETTINGS_BY_DATASET)}")
        return 2
    kind, builder = spec
    if kind == "person":
        settings, training_rules = builder(s, records.column_names)
    else:
        settings, training_rules = builder(s)

    # 3. Serialize -> the REAL dict our recognizers must parse -> convert.
    settings_dict = settings.create_settings_dict(sql_dialect_str="duckdb")
    conversion = from_splink(settings_dict)
    _print_report(conversion)

    out_dir: Path = args.out_dir / args.dataset
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_path = out_dir / "truth.parquet"
    _tidx = truth.schema.get_field_index("record_id")
    pq.write_table(
        truth.set_column(
            _tidx, "record_id", pc.cast(truth.column("record_id"), pa.string())
        ),
        truth_path,
    )

    # 4. Native Splink.
    splink_pred = out_dir / "splink_pred.parquet"
    t0 = time.perf_counter()
    splink_result = _run_native_splink(
        run_splink_mod, settings, training_rules, s, records,
        splink_pred, args.threshold, args.max_pairs,
    )
    splink_wall = time.perf_counter() - t0
    # String record_id space to join the string-keyed truth.
    _sp = pq.read_table(splink_pred)
    _spidx = _sp.schema.get_field_index("record_id")
    pq.write_table(
        _sp.set_column(
            _spidx, "record_id", pc.cast(_sp.column("record_id"), pa.string())
        ),
        splink_pred,
    )
    print(f"[splink] wall={splink_wall:.1f}s "
          f"scored_pairs={splink_result.get('scored_pairs')} "
          f"clusters={splink_result.get('cluster_count')}")

    # 5. GoldenMatch with the CONVERTED config.
    gm_pred = out_dir / "gm_converted_pred.parquet"
    gm_wall = _run_converted_goldenmatch(conversion.config, records, gm_pred)
    print(f"[goldenmatch] wall={gm_wall:.1f}s (converted config)")

    # 6. One evaluator for both engines.
    splink_metrics = evaluate_mod.evaluate(splink_pred, truth_path)
    gm_metrics = evaluate_mod.evaluate(gm_pred, truth_path)
    sp, gp = splink_metrics["pairwise"], gm_metrics["pairwise"]
    print(f"[splink]      P/R/F1 = {sp['precision']}/{sp['recall']}/{sp['f1']}")
    print(f"[goldenmatch] P/R/F1 = {gp['precision']}/{gp['recall']}/{gp['f1']}")

    splink_f1 = sp["f1"]
    gm_f1 = gp["f1"]
    delta = round(splink_f1 - gm_f1, 4)
    print(f"splink_f1={splink_f1} converted_gm_f1={gm_f1} delta={delta}")
    if delta > _F1_GATE:
        print(f"PARITY GATE FAILED: delta {delta} > {_F1_GATE}")
        return 1
    print(f"parity gate passed (delta {delta} <= {_F1_GATE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
