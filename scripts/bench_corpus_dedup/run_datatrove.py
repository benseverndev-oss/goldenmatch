#!/usr/bin/env python
"""Single-datapoint datatrove MinHash-dedup runner for the corpus-dedup bench.

The external baseline. Runs HuggingFace datatrove's MinHash near-dup pipeline
(signature -> buckets -> cluster) on the identical `{doc_id, text}` corpus parquet and
reports the same speed/memory schema as the GoldenMatch runner, so docs/sec and MB/sec are
apples-to-apples on one machine.

datatrove is `uv pip install`'d in the headline workflow (NOT a repo dependency), exactly
like Splink in bench_er_headtohead. This runner is exercised in that lane; locally the
matching test skips when datatrove is absent.

FAIRNESS / comparability (auditable, not asserted):
  We configure MinHash LSH so its S-curve 50%-point sits near the GoldenMatch throughput
  tier's default Jaccard near-dup threshold (0.8). For `num_buckets=B`, `hashes_per_bucket=R`
  the 50%-point is ~ (1/B)^(1/R). We use B=10, R=10 -> (0.1)^0.1 ~= 0.79 ~= 0.8. Both engines
  therefore target the same near-dup similarity; record the chosen numbers here + in README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import tempfile
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

# LSH config targeting ~0.8 Jaccard (see module docstring).
NUM_BUCKETS = 10
HASHES_PER_BUCKET = 10

# datatrove's MinhashDedupBuckets writes each in-bucket match to a `.dups` file as a
# packed record of four uint32s: (file_id_1, doc_id_1, file_id_2, doc_id_2). When a match
# involves an index signature the file id is replaced by SENTINEL — we run without an
# index_folder, so that never appears here, but we skip it defensively.
_DUP_RECORD = struct.Struct("<4I")
_DUP_SENTINEL = (1 << 32) - 1


def _read_dup_edges(buckets_dir: Path) -> tuple[list[tuple[int, int]], int]:
    """Parse the near-dup edges datatrove's *buckets* stage emitted.

    Returns ``(edges, raw_record_count)`` where ``edges`` are de-duplicated
    ``(doc_id_a, doc_id_b)`` pairs (``a <= b``, self-pairs and SENTINEL matches
    dropped) and ``raw_record_count`` is the total ``.dups`` records read (a
    single pair can appear in more than one bucket).

    The signature stage runs single-task, so every doc has ``file_id == 0`` and
    ``doc_id`` equals the reader's global row index — i.e. the same index the
    caller maps clusters back onto. This reads the BUCKETS output (`.dups`
    edges), NOT the cluster stage output (a `.remove` list of single doc ids,
    which carries no pairs — reading it was the recall=0 bug, #1150).
    """
    seen: set[tuple[int, int]] = set()
    raw = 0
    size = _DUP_RECORD.size
    for f in sorted(buckets_dir.rglob("*.dups")):
        if not f.is_file():
            continue
        blob = f.read_bytes()
        usable = len(blob) - (len(blob) % size)
        for off in range(0, usable, size):
            f1, d1, f2, d2 = _DUP_RECORD.unpack_from(blob, off)
            raw += 1
            if f1 == _DUP_SENTINEL or f2 == _DUP_SENTINEL:
                continue
            if d1 == d2:
                continue
            seen.add((d1, d2) if d1 <= d2 else (d2, d1))
    return sorted(seen), raw


def _peak_rss_mb() -> float | None:
    if resource is None:
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def _run_minhash(corpus_path: Path, workdir: Path) -> tuple[dict[str, int], int]:
    """Run datatrove MinHash dedup; return (doc_id -> cluster_id, candidate_pairs).

    Clusters: datatrove's buckets stage emits, per in-bucket match, a `.dups` edge
    ``(file_id_1, doc_id_1, file_id_2, doc_id_2)``. We union-find those edges into cluster
    ids; every doc not in any edge is its own singleton. candidate_pairs = number of unique
    bucket-matched duplicate edges. (Union-find over the raw bucket edges reproduces the
    cluster stage's own clustering, which is order-invariant.)
    """
    import polars as pl
    from datatrove.data import Document
    from datatrove.executor import LocalPipelineExecutor
    from datatrove.pipeline.base import PipelineStep
    from datatrove.pipeline.dedup import MinhashDedupSignature
    from datatrove.pipeline.dedup.minhash import (
        MinhashConfig,
        MinhashDedupBuckets,
        MinhashDedupCluster,
    )
    from datatrove.utils.hashing import HashConfig

    df = pl.read_parquet(corpus_path)
    doc_ids = df["doc_id"].to_list()
    texts = df["text"].to_list()

    class _MemReader(PipelineStep):
        type = "reader"

        def run(self, data=None, rank: int = 0, world_size: int = 1):
            for i, (did, txt) in enumerate(zip(doc_ids, texts)):
                yield Document(text=txt, id=str(did), metadata={"idx": i})

    cfg = MinhashConfig(
        hash_config=HashConfig(precision=64),
        num_buckets=NUM_BUCKETS,
        hashes_per_bucket=HASHES_PER_BUCKET,
    )
    sig_dir = workdir / "sigs"
    buck_dir = workdir / "buckets"
    clus_dir = workdir / "clusters"

    LocalPipelineExecutor(
        pipeline=[_MemReader(), MinhashDedupSignature(output_folder=str(sig_dir), config=cfg)],
        tasks=1,
    ).run()
    LocalPipelineExecutor(
        pipeline=[MinhashDedupBuckets(input_folder=str(sig_dir), output_folder=str(buck_dir), config=cfg)],
        tasks=cfg.num_buckets,
    ).run()
    LocalPipelineExecutor(
        pipeline=[MinhashDedupCluster(input_folder=str(buck_dir), output_folder=str(clus_dir), config=cfg)],
        tasks=1,
    ).run()

    # Union-find over the duplicate edges the cluster stage emitted.
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[min(ra, rb)] = max(ra, rb)

    # Read the near-dup edges from the BUCKETS stage `.dups` files (the cluster stage's own
    # output is a `.remove` list of single doc ids — no pairs — which read as zero edges, #1150).
    edges, _raw = _read_dup_edges(buck_dir)
    for a, b in edges:
        union(a, b)
    n_edges = len(edges)

    clusters: dict[str, int] = {}
    for i, did in enumerate(doc_ids):
        clusters[str(did)] = find(i) if i in parent else i
    return clusters, n_edges


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None)
    args = ap.parse_args()

    result: dict = {"engine": "datatrove", "status": "error",
                    "lsh": {"num_buckets": NUM_BUCKETS, "hashes_per_bucket": HASHES_PER_BUCKET}}
    t_start = time.perf_counter()
    try:
        import polars as pl

        df = pl.read_parquet(args.input)
        n = df.height
        bytes_in = int(df["text"].str.len_bytes().sum())
        result.update(n_docs=n, bytes_in=bytes_in)

        with tempfile.TemporaryDirectory() as td:
            t0 = time.perf_counter()
            clusters, candidate_pairs = _run_minhash(args.input, Path(td))
            dedupe_wall = time.perf_counter() - t0

        if args.pred_out is not None:
            import pyarrow as pa
            import pyarrow.parquet as pq

            rids = list(clusters.keys())
            cids = [int(c) for c in clusters.values()]
            pq.write_table(
                pa.table({"record_id": pa.array(rids, pa.string()),
                          "pred_cluster_id": pa.array(cids, pa.int64())}),
                args.pred_out, compression="zstd",
            )

        result.update(
            status="ok",
            dedupe_wall_seconds=round(dedupe_wall, 3),
            docs_per_sec=round(n / dedupe_wall, 1) if dedupe_wall else None,
            mb_per_sec=round((bytes_in / 1e6) / dedupe_wall, 3) if dedupe_wall else None,
            candidate_pairs=candidate_pairs,
            clusters=len(set(clusters.values())),
        )
    except MemoryError as e:
        result.update(status="OOM", error=f"{type(e).__name__}: {e}")
    except BaseException as e:  # noqa: BLE001
        result.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 3)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(f"[datatrove] status={result['status']} "
              f"docs/sec={result.get('docs_per_sec')} candidate_pairs={result.get('candidate_pairs')}")


if __name__ == "__main__":
    main()
