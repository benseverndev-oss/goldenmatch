#!/usr/bin/env python3
"""Validate the Lance base store on a REAL NCVR voter base before any default flip.

Two checks the spec/plan require on real (not synthetic) skewed PII data:

  1. PARITY (correctness, load-bearing): build a REAL char-ngram FAISS ANN index
     over real NCVR names, run ``match_one`` with FrameCandidateStore vs
     LanceCandidateStore for a sample of real probe records, assert the
     ``(row_id, score)`` results are byte-identical. This is the invariant that
     must hold before lance can ever back a default.

  2. SCALE (the win): load a real NCVR base >= the 2M threshold, measure per-probe
     candidate-gather latency + peak RSS (VmHWM) for memory / parquet / lance on
     real records and real zip-block skew (ann scatter + exact block).

Data: real NCVR statewide voter file (gitignored, ~9.1M rows). Obtain via
    curl -sSL -o /tmp/ncvr/ncvoter_Statewide.zip https://dl.ncsbe.gov/data/ncvoter_Statewide.zip
    unzip -o /tmp/ncvr/ncvoter_Statewide.zip -d /tmp/ncvr
Script skips with guidance if absent. Needs faiss-cpu + pylance.

Usage:
    python scripts/validate_lance_base_store_ncvr.py \
        --ncvr /tmp/ncvr/ncvoter_Statewide.txt --index-rows 100000 --scale-rows 3000000
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

MATCH_COLS = [
    "last_name", "first_name", "middle_name", "res_street_address",
    "res_city_desc", "state_cd", "zip_code", "birth_year",
]
SCORE_COLS = ["__row_id__", "__block_key__", "full_name", *MATCH_COLS]


def _have(m: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(m) is not None


def _peak_rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def load_ncvr(path: Path, rows: int):
    """Load `rows` real NCVR records with the match columns + __row_id__ +
    __block_key__ (zip), full_name for ANN."""
    import polars as pl

    lf = pl.scan_csv(path, separator="\t", encoding="utf8-lossy", ignore_errors=True, infer_schema_length=0)
    df = lf.select([c for c in MATCH_COLS if c]).head(rows).collect()
    df = df.with_columns(
        pl.col("zip_code").fill_null("").str.slice(0, 5).alias("__block_key__"),
        (pl.col("first_name").fill_null("") + pl.lit(" ") + pl.col("last_name").fill_null("")).alias("full_name"),
    ).with_row_index("__row_id__")
    # __row_id__ as Int64 for consistency with the pipeline
    return df.with_columns(pl.col("__row_id__").cast(pl.Int64))


# ---- a real (simple) char-ngram embedder + FAISS ANN --------------------------

class CharNgramEmbedder:
    """Hashed char 3-gram -> L2-normalized float32 vector. A real, deterministic
    embedding over real names (no trained model / network needed)."""

    def __init__(self, dim: int = 256, n: int = 3):
        self.dim = dim
        self.n = n

    def _vec(self, text: str):
        import numpy as np

        v = np.zeros(self.dim, dtype="float32")
        s = (text or "").lower()
        for i in range(max(0, len(s) - self.n + 1)):
            g = s[i : i + self.n]
            v[hash(g) % self.dim] += 1.0
        nrm = float((v * v).sum()) ** 0.5
        return v / nrm if nrm > 0 else v

    def embed_column(self, texts, cache_key=None):
        import numpy as np

        return np.vstack([self._vec(t) for t in texts]).astype("float32")


def _mk():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    return MatchkeyConfig(
        name="ncvr", type="weighted", threshold=0.75,
        fields=[
            MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.8),
            MatchkeyField(field="zip_code", scorer="exact", weight=0.5),
        ],
    )


def validate_parity(df, index_rows: int, n_probes: int, tmp: Path) -> bool:
    import numpy as np
    from goldenmatch.core.ann_blocker import ANNBlocker
    from goldenmatch.core.candidate_store import LanceCandidateStore
    from goldenmatch.core.match_one import match_one

    base = df.head(index_rows)
    emb = CharNgramEmbedder()
    print(f"  [parity] embedding {base.height:,} real names + building FAISS index ...")
    vecs = emb.embed_column(base["full_name"].to_list())
    ann = ANNBlocker(top_k=20)
    ann.build_index(vecs)

    # match_one with store=None wraps base in a FrameCandidateStore (the memory
    # path); store=lance_store routes through Lance. Compare the two.
    lance_store = LanceCandidateStore.from_frame(base, str(tmp / "parity.lance"))
    mk = _mk()

    rng = np.random.default_rng(7)
    probe_idx = rng.choice(base.height, size=n_probes, replace=False)
    probes = base[list(probe_idx)].to_dicts()

    mismatches, nonempty = 0, 0
    for rec in probes:
        kw = dict(ann_blocker=ann, embedder=emb, ann_column="full_name", top_k=20)
        r_mem = match_one(rec, base, mk, **kw)                      # FrameCandidateStore
        r_lan = match_one(rec, base, mk, store=lance_store, **kw)   # LanceCandidateStore
        if r_mem:
            nonempty += 1
        if r_mem != r_lan:
            mismatches += 1
            if mismatches <= 3:
                print(f"    MISMATCH: mem={r_mem[:3]} lan={r_lan[:3]}")
    ok = mismatches == 0
    print(f"  [parity] {n_probes} real probes, {nonempty} with >=1 match, mismatches={mismatches} -> {'PASS' if ok else 'FAIL'}")
    return ok


# ---- scale measurement (real base) -------------------------------------------

def _child(q, store_kind, path_str, shape, probe_keys, top_k, n):
    import numpy as np

    path = Path(path_str)
    rng = np.random.default_rng(123)
    if store_kind == "memory":
        import polars as pl

        base = pl.read_parquet(path, columns=SCORE_COLS)
        kc = base["__block_key__"]

        def g_ann(ids):
            return base[ids].height

        def g_block(k):
            return base.filter(kc == k).height
    elif store_kind == "parquet":
        import polars as pl

        def g_ann(ids):
            return pl.read_parquet(path, columns=SCORE_COLS)[ids].height

        def g_block(k):
            return pl.scan_parquet(path).filter(pl.col("__block_key__") == k).select(SCORE_COLS).collect().height
    else:
        import lance

        ds = lance.dataset(str(path))

        def g_ann(ids):
            return ds.take(list(ids), columns=SCORE_COLS).num_rows

        def g_block(k):
            return ds.scanner(columns=SCORE_COLS, filter=f"__block_key__ = '{k}'").to_table().num_rows

    if shape == "ann":
        args = [np.sort(rng.choice(n, size=top_k, replace=False)) for _ in probe_keys]
        fn = g_ann
    else:
        args = list(probe_keys)
        fn = g_block

    walls = []
    for a in args:
        t0 = time.perf_counter()
        fn(a)
        walls.append(time.perf_counter() - t0)
    q.put((statistics.median(walls), _peak_rss_mb()))


def _measure(kind, path, shape, probe_keys, top_k, n):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(q, kind, str(path), shape, probe_keys, top_k, n))
    p.start()
    p.join()
    return q.get() if not q.empty() else (float("nan"), float("nan"))


def measure_scale(df, probes: int, top_k: int, tmp: Path) -> None:
    import numpy as np
    from goldenmatch.core.candidate_store import LanceCandidateStore

    n = df.height
    vc = df.group_by("__block_key__").len().sort("len", descending=True)
    sizes = vc["len"].to_list()
    print(f"  [scale] N={n:,} real rows; zip-block skew p50={sizes[len(sizes)//2]} p99={sizes[max(0,len(sizes)//100)]} max={sizes[0]} blocks={len(sizes):,}")

    pq = tmp / "ncvr.parquet"
    df.select(SCORE_COLS).write_parquet(pq, row_group_size=128 * 1024, statistics=True)
    LanceCandidateStore.from_frame(df.select(SCORE_COLS), str(tmp / "ncvr.lance"))
    paths = {"memory": pq, "parquet": pq, "lance": tmp / "ncvr.lance"}
    stores = ["memory", "parquet", "lance"]

    rng = np.random.default_rng(99)
    probe_keys = list(rng.choice(vc["__block_key__"].to_list(), size=probes))

    print("  " + "-" * 70)
    print(f"  {'shape':<14} " + " | ".join(f"{s:^16}" for s in stores))
    for shape in ("ann", "block"):
        cells = []
        for s in stores:
            med, rss = _measure(s, paths[s], shape, probe_keys, top_k, n)
            cells.append(f"{med*1000:6.1f}ms {rss:6.0f}MB")
        print(f"  {shape+' stream':<14} " + " | ".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ncvr", default="/tmp/ncvr/ncvoter_Statewide.txt")
    ap.add_argument("--index-rows", type=int, default=100_000)
    ap.add_argument("--scale-rows", type=int, default=3_000_000)
    ap.add_argument("--probes", type=int, default=50)
    ap.add_argument("--top-k", type=int, default=50)
    args = ap.parse_args()

    ncvr = Path(args.ncvr)
    if not ncvr.exists():
        print(f"Real NCVR file not found at {ncvr}. Obtain it (see module docstring).", file=sys.stderr)
        return 2
    if not (_have("faiss") and _have("lance")):
        print("Need faiss-cpu + pylance for this validation.", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="ncvr_lance_val_"))
    print("=" * 72)
    print("PARITY (real NCVR + real char-ngram FAISS ANN, Frame vs Lance match_one)")
    pdf = load_ncvr(ncvr, args.index_rows)
    ok = validate_parity(pdf, args.index_rows, args.probes, tmp)

    print("\nSCALE (real NCVR base, per-probe gather latency + peak RSS)")
    sdf = load_ncvr(ncvr, args.scale_rows)
    measure_scale(sdf, args.probes, args.top_k, tmp)

    print("=" * 72)
    print(f"PARITY: {'PASS — Frame and Lance stores byte-identical on real data' if ok else 'FAIL'}")
    print("Defaults stay memory; lance is opt-in. Flip a default only on a real win + parity.")
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
