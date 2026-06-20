#!/usr/bin/env python
"""Single-datapoint GoldenMatch throughput-tier runner for the corpus-dedup bench.

Runs ONE throughput-tier dedupe on a `{doc_id, text}` corpus parquet in its own process,
so all memory is reclaimed by the OS on exit. Writes one atomic JSON result + an optional
`{record_id, pred_cluster_id}` prediction parquet for the engine-agnostic evaluator.

LOUD-FAIL discipline (mirrors bench_er_headtohead): refuse to report a number if the
throughput tier did not actually engage. The proof the tier engaged is `ded.throughput_posture`
(only populated on the sketch-then-verify path) + a `lsh`/`simhash` blocking strategy.

COST METRICS come from `ded.throughput_posture` (a dict), NOT bench_capture — on the
throughput path the fuzzy/FS scorer is bypassed, so `scored_pair_count` is 0 (spike #1086 0.2).
"""
from __future__ import annotations

import argparse
import json
import faulthandler
import os
import time
from pathlib import Path

try:
    import resource  # Unix-only; absent on Windows dev boxes (CI/bench runs on Linux)
except ImportError:  # pragma: no cover - Windows fallback path
    resource = None

# Self-report a hang: if the tier stalls (e.g. a controller pathology on
# adversarial data), dump every thread's stack and exit non-zero rather than
# stalling the gate/test until the CI job timeout. Cancelled automatically when
# the process exits normally first. Tunable for slow boxes via the env var.
_HANG_DUMP_S = int(os.environ.get("GOLDENMATCH_BENCH_HANG_DUMP_S", "150") or 0)
if _HANG_DUMP_S > 0:
    faulthandler.dump_traceback_later(_HANG_DUMP_S, exit=True)

# Must be set BEFORE importing goldenmatch.
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

VALID_STRATEGIES = ("lsh", "simhash")


def _peak_rss_mb() -> float | None:
    if resource is None:  # Windows dev box: no rusage.
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None)
    ap.add_argument("--recall-target", type=float, default=0.95)
    args = ap.parse_args()

    result: dict = {
        "engine": "goldenmatch",
        "status": "error",
        "recall_target": args.recall_target,
    }
    t_start = time.perf_counter()
    try:
        import polars as pl
        from goldenmatch.core.bench import bench_capture  # noqa: F401 (kept for timing parity)

        try:
            from goldenmatch import dedupe_df
        except ImportError:
            from goldenmatch._api import dedupe_df

        df = pl.read_parquet(args.input)
        if set(df.columns) < {"doc_id", "text"}:
            raise ValueError(f"corpus must have doc_id+text columns, got {df.columns}")
        n = df.height
        bytes_in = int(df["text"].str.len_bytes().sum())
        result.update(n_docs=n, bytes_in=bytes_in)

        t0 = time.perf_counter()
        ded = dedupe_df(df, throughput=args.recall_target)
        dedupe_wall = time.perf_counter() - t0

        # --- tier-engaged proof + cost metrics from the posture ---
        post = getattr(ded, "throughput_posture", None)
        if not post or "candidate_pairs" not in post:
            raise RuntimeError(
                "throughput tier did not engage (no throughput_posture) — "
                "refusing to report a number for the wrong path"
            )
        strategy = getattr(getattr(ded.config, "blocking", None), "strategy", None)
        if strategy not in VALID_STRATEGIES:
            raise RuntimeError(
                f"throughput tier blocking strategy={strategy!r} not in {VALID_STRATEGIES}"
            )

        candidate_pairs = int(post["candidate_pairs"])
        reduction_ratio = float(post["reduction_ratio"])
        docs_per_sec = round(n / dedupe_wall, 1) if dedupe_wall else None
        mb_per_sec = round((bytes_in / 1e6) / dedupe_wall, 3) if dedupe_wall else None

        # --- prediction parquet: clusters members are positional __row_id__ ints,
        #     remap to the original doc_id STRINGS (else the evaluator joins zero rows) ---
        if args.pred_out is not None:
            import pyarrow as pa
            import pyarrow.parquet as pq

            doc_ids = df["doc_id"].to_list()
            rids, cids = [], []
            for cid, c in ded.clusters.items():
                members = c["members"] if isinstance(c, dict) else c.members
                for m in members:
                    rids.append(str(doc_ids[m]))
                    cids.append(int(cid))
            pq.write_table(
                pa.table({"record_id": pa.array(rids, pa.string()),
                          "pred_cluster_id": pa.array(cids, pa.int64())}),
                args.pred_out, compression="zstd",
            )

        result.update(
            status="ok",
            dedupe_wall_seconds=round(dedupe_wall, 3),
            docs_per_sec=docs_per_sec,
            mb_per_sec=mb_per_sec,
            candidate_pairs=candidate_pairs,
            reduction_ratio=round(reduction_ratio, 4),
            verify_mode="sketch_distance",  # definitional: posture present == tier engaged
            blocking_strategy=strategy,
            clusters=int(getattr(ded, "total_clusters", 0) or len(ded.clusters)),
            throughput_posture=post,
        )
    except MemoryError as e:
        result.update(status="OOM", error=f"{type(e).__name__}: {e}")
    except BaseException as e:  # noqa: BLE001 - record any failure
        result.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 3)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(f"[goldenmatch] status={result['status']} "
              f"docs/sec={result.get('docs_per_sec')} "
              f"candidate_pairs={result.get('candidate_pairs')} "
              f"reduction_ratio={result.get('reduction_ratio')}")


if __name__ == "__main__":
    main()
