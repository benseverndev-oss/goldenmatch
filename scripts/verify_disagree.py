#!/usr/bin/env python
"""Parity verification: native-vs-numpy FS partition churn on historical_50k.

Question (FS_HANDOFF.md 3b): the native missing="disagree" kernel produces the
same cluster COUNT as numpy but ~1-2% of multi-member partitions differ. Is that
churn (a) a bug in the disagree weight math, or (b) the inherent rust-rapidfuzz
vs py-rapidfuzz scoring tolerance on OBSERVED fields that already exists in
"unobserved" mode too?

Method: for each missing_mode in {disagree, unobserved}, run the SAME
auto-configured probabilistic dedupe with the native kernel ON and OFF (numpy
fallback), holding blocking constant (env pins the mode; auto-config is
deterministic on identical data), and compare per-record partition assignment.

Verdict: if the native-vs-numpy churn is COMPARABLE in `unobserved` mode (where
the disagree code path is never taken) to `disagree` mode, the churn is inherent
scoring tolerance, NOT a disagree bug -- acceptable under "Rust is the reference".
A disagree-specific weight bug would show churn in `disagree` but ~none in
`unobserved`.

Each (mode, backend) runs in its own SUBPROCESS so the native/missing-mode env
gates are read fresh (they can cache at import).

Run:  .venv/bin/python scratchpad/verify_disagree.py
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRATCH = Path(__file__).resolve().parent


def _worker(mode: str, backend: str, inp: str, out: str) -> None:
    """Run one dedupe under the pinned mode/backend and write partitions."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from goldenmatch import dedupe_df
    from goldenmatch.core._native_loader import native_module
    from goldenmatch.core.probabilistic import fs_missing_mode
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    df = pq.read_table(inp)
    cfg = auto_configure_probabilistic_df(df)
    # Force rerank off so a weighted matchkey can't pull a cross-encoder at
    # dedupe time (mirrors run_goldenmatch.py).
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", None) is not None:
            mk.rerank = None

    resolved = fs_missing_mode(cfg.get_matchkeys()[0] if cfg.get_matchkeys() else None)
    native_present = native_module() is not None
    fs_disagree_ok = bool(getattr(native_module(), "FS_SUPPORTS_MISSING_DISAGREE", False))

    ded = dedupe_df(df, config=cfg)

    rid = df.column("record_id").to_pylist()
    clusters = getattr(ded, "clusters", None) or {}
    rids, cids = [], []
    for cid, c in clusters.items():
        members = c["members"] if isinstance(c, dict) else c.members
        for m in members:
            rids.append(str(rid[m]))
            cids.append(int(cid))
    pq.write_table(
        pa.table(
            {
                "record_id": pa.array(rids, pa.string()),
                "pred_cluster_id": pa.array(cids, pa.int64()),
            }
        ),
        out,
        compression="zstd",
    )
    sys.stderr.write(
        f"[worker] mode={mode} backend={backend} resolved_missing={resolved} "
        f"native_present={native_present} fs_disagree_ok={fs_disagree_ok} "
        f"clusters={len(clusters)} records={len(rids)}\n"
    )


def _load_partitions(path: str) -> tuple[dict[str, int], list[frozenset[str]]]:
    import pyarrow.parquet as pq

    t = pq.read_table(path)
    rid = t.column("record_id").to_pylist()
    cid = t.column("pred_cluster_id").to_pylist()
    rec_to_cid = dict(zip(rid, cid))
    by_cid: dict[int, set[str]] = {}
    for r, c in zip(rid, cid):
        by_cid.setdefault(c, set()).add(r)
    multi = [frozenset(s) for s in by_cid.values() if len(s) > 1]
    return rec_to_cid, multi


def _compare(native_path: str, numpy_path: str) -> dict:
    _, nat = _load_partitions(native_path)
    _, num = _load_partitions(numpy_path)
    nat_set, num_set = set(nat), set(num)
    only_native = nat_set - num_set
    only_numpy = num_set - nat_set
    shared = nat_set & num_set
    union = nat_set | num_set
    churn = len(union) - len(shared)
    return {
        "multi_native": len(nat),
        "multi_numpy": len(num),
        "identical_multi_partitions": len(shared),
        "only_native": len(only_native),
        "only_numpy": len(only_numpy),
        "churn_partitions": churn,
        "churn_pct": round(100.0 * churn / max(len(union), 1), 3),
    }


def _run_worker_subprocess(mode: str, backend: str, inp: str, out: str) -> None:
    env = dict(os.environ)
    env["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    env["ARROW_DEFAULT_MEMORY_POOL"] = "system"
    env["GOLDENMATCH_FS_MISSING"] = mode
    if backend == "native":
        env["GOLDENMATCH_NATIVE"] = "1"
        env["GOLDENMATCH_FS_NATIVE"] = "1"
    else:  # numpy
        env["GOLDENMATCH_FS_NATIVE"] = "0"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--mode",
        mode,
        "--backend",
        backend,
        "--input",
        inp,
        "--out",
        out,
    ]
    subprocess.run(cmd, check=True, env=env)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--mode", choices=["disagree", "unobserved"])
    ap.add_argument("--backend", choices=["native", "numpy"])
    ap.add_argument("--input")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.worker:
        _worker(args.mode, args.backend, args.input, args.out)
        return

    # Parent: materialize historical_50k once, then fan out.
    sys.path.insert(0, str(REPO / "scripts" / "bench_er_headtohead"))
    import pyarrow.parquet as pq
    from datasets import load_dataset  # type: ignore

    records, _truth = load_dataset("historical_50k")
    inp = str(SCRATCH / "historical_50k_records.parquet")
    pq.write_table(records, inp, compression="zstd")
    print(f"[verify] historical_50k records: {records.num_rows} rows -> {inp}")

    results = {}
    for mode in ("disagree", "unobserved"):
        outs = {}
        for backend in ("native", "numpy"):
            out = str(SCRATCH / f"parts_{mode}_{backend}.parquet")
            print(f"[verify] running mode={mode} backend={backend} ...")
            _run_worker_subprocess(mode, backend, inp, out)
            outs[backend] = out
        results[mode] = _compare(outs["native"], outs["numpy"])

    print("\n===== NATIVE vs NUMPY partition churn =====")
    for mode, r in results.items():
        print(f"\n[{mode}]")
        for k, v in r.items():
            print(f"  {k:32s} {v}")

    dis = results["disagree"]["churn_pct"]
    uno = results["unobserved"]["churn_pct"]
    print("\n===== VERDICT =====")
    print(f"disagree churn:   {dis}%")
    print(f"unobserved churn: {uno}%")
    if uno > 0 and dis <= max(uno * 3.0, uno + 0.5):
        print(
            "PASS: disagree churn is comparable to the unobserved-mode baseline "
            "churn -> the churn is inherent native-vs-numpy rapidfuzz scoring "
            "tolerance on observed fields, NOT a disagree-specific bug."
        )
    elif uno == 0 and dis > 0:
        print(
            "INVESTIGATE: unobserved mode is byte-identical native-vs-numpy but "
            "disagree churns -> churn is disagree-specific, not generic tolerance."
        )
    else:
        print(
            "REVIEW: churn present in both; inspect magnitudes above (both should "
            "be small, ~1-2%, dominated by float scoring tolerance)."
        )


if __name__ == "__main__":
    main()
