"""Is zero-config FS deterministic across runners? Probe one dataset under variants.

Context: three `fs-lever-gate` full-panel runs on ONE commit gave dblp_scholar a
different baseline each time (F1 0.3750 / 0.3757 / 0.3759; predicted pairs
27,125 / 27,033 / 27,029), while BOTH arms inside every run agreed exactly. So one
process on one runner is deterministic and the drift follows the RUNNER. Auto-config
and EM are identical across processes locally, and forcing the memory-aware pair
budget from 300M to 1B leaves the config unchanged -- so the cause is downstream.

Each variant runs zero-config dedupe in a FRESH process (so thread pools and env
are honoured) and prints F1, predicted pairs and the partition digest. A variant
that changes the digest names the knob the result depends on; two identical
default runs are the control that the probe itself is deterministic.

Usage:
    python -m scripts.bench_er_headtohead.determinism_probe --dataset dblp_scholar
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# Every candidate runs TWICE in fresh processes: a setting "fixes" the drift only if
# its own two runs agree. Run 34520791144 already showed the drift is per PROCESS,
# not per runner (two default processes on one runner: 27,035 vs 27,029 pairs), and
# that RAYON_NUM_THREADS / n_buckets / the numpy route don't separate the outcomes
# -- so these candidates are the per-process randomness sources.
# Run 34522839151 settled the SOURCE: a fixed PYTHONHASHSEED makes repeats identical
# (seed 0: 27,125 pairs twice; seed 1: 27,035 twice), while the default and
# POLARS_MAX_THREADS=1 drift. So a str-hash-ordered iteration reaches an
# order-dependent step. This probe now finds the STAGE: the two seeds run once each,
# with digests of what clustering receives (the pair SET and its ORDER), every refit
# link threshold, and how often the oversized-cluster splitters fire. The first
# stage whose digest differs between the seeds is where the order leaks in.
VARIANTS = (
    ("PYTHONHASHSEED=0", {"PYTHONHASHSEED": "0"}),
    ("PYTHONHASHSEED=1", {"PYTHONHASHSEED": "1"}),
)

_CHILD = r"""
import json, logging, os, sys, time
logging.disable(logging.WARNING)
import goldenmatch
from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
from goldenmatch.core.evaluate import evaluate_clusters
from scripts.autoconfig_quality import datasets as D
from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

import hashlib
from goldenmatch.core import cluster as C
from goldenmatch.core import pipeline as P

def _h(obj):
    return hashlib.sha256(repr(obj).encode()).hexdigest()[:12]

stages = {"build_clusters": [], "link_threshold": [], "split_calls": 0, "split_members": 0}

_orig_build = C.build_clusters
def _build_spy(pairs, *a, **k):
    plist = list(pairs)
    norm = [(min(x, y), max(x, y), round(float(s), 12)) for x, y, s in plist]
    stages["build_clusters"].append({
        "n_pairs": len(plist),
        "set": _h(sorted(norm)),
        "order": _h(norm),
    })
    return _orig_build(plist, *a, **k)
C.build_clusters = _build_spy

for _fn in ("split_oversized_cluster", "split_oversized_cluster_to_size"):
    _orig = getattr(C, _fn, None)
    if _orig is None:
        continue
    def _make(orig):
        def _split_spy(members, *a, **k):
            stages["split_calls"] += 1
            stages["split_members"] += len(members)
            return orig(members, *a, **k)
        return _split_spy
    setattr(C, _fn, _make(_orig))

_orig_refit = P._maybe_refit_link_threshold
def _refit_spy(mk, link_threshold, **k):
    t = _orig_refit(mk, link_threshold, **k)
    stages["link_threshold"].append(repr(t))
    return t
P._maybe_refit_link_threshold = _refit_spy

name = sys.argv[1]
t0 = time.perf_counter()
loaded = getattr(D, "_" + name)()
if loaded is None:
    print(json.dumps({"error": f"{name} unavailable"}))
    sys.exit(0)
df, gt = loaded
cfg = auto_configure_probabilistic_df(df)
res = goldenmatch.dedupe_df(df, config=cfg)
ev = evaluate_clusters(res.clusters, gt).summary()
pairs, digest = _partition_fingerprint(res.clusters)
print(json.dumps({
    "f1": round(ev["f1"], 6), "pairs": pairs, "digest": digest,
    "config": hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()[:12],
    "stages": stages, "seconds": round(time.perf_counter() - t0, 1),
}))
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="dblp_scholar")
    args = ap.parse_args()

    results = []
    for label, extra in VARIANTS:
        env = {**os.environ, "GOLDENMATCH_AUTOCONFIG_MEMORY": "0", **extra}
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, args.dataset],
            env=env,
            capture_output=True,
            text=True,
        )
        line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
        if proc.returncode != 0 or line is None:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
            results.append((label, {"error": f"exit {proc.returncode}: {' | '.join(tail)}"}))
        else:
            results.append((label, json.loads(line)))
        print(f"{label:30s} {json.dumps(results[-1][1])}", flush=True)

    ok = dict((label, r) for label, r in results if "error" not in r)
    a, b = ok.get(VARIANTS[0][0]), ok.get(VARIANTS[1][0])
    if a is None or b is None:
        print("\nPROBE: FAIL -- a seed run failed; nothing to compare.")
        return 1
    print(f"\nstage comparison, {VARIANTS[0][0]} vs {VARIANTS[1][0]}:")
    print(f"  config digest      {'same' if a['config'] == b['config'] else 'DIFFERS'}")
    sa, sb = a["stages"], b["stages"]
    calls = max(len(sa["build_clusters"]), len(sb["build_clusters"]))
    if calls == 0:
        print("  build_clusters     never called (a columnar / native cluster route?)")
    for i in range(calls):
        ca = sa["build_clusters"][i] if i < len(sa["build_clusters"]) else None
        cb = sb["build_clusters"][i] if i < len(sb["build_clusters"]) else None
        if ca is None or cb is None:
            print(f"  build_clusters #{i} called under only one seed")
            continue
        print(
            f"  build_clusters #{i}  pairs {ca['n_pairs']} vs {cb['n_pairs']}  "
            f"SET {'same' if ca['set'] == cb['set'] else 'DIFFERS'}  "
            f"ORDER {'same' if ca['order'] == cb['order'] else 'DIFFERS'}"
        )
    same_thr = sa["link_threshold"] == sb["link_threshold"]
    print(
        f"  link thresholds    {'same' if same_thr else 'DIFFERS'}  "
        f"{sa['link_threshold']} vs {sb['link_threshold']}"
    )
    print(
        f"  MST splitter calls {sa['split_calls']} vs {sb['split_calls']}  "
        f"(members {sa['split_members']} vs {sb['split_members']})"
    )
    same_final = a["digest"] == b["digest"]
    print(
        f"  final partition    {'same' if same_final else 'DIFFERS'}  "
        f"pairs {a['pairs']} vs {b['pairs']}"
    )
    # Non-zero while the seeds disagree: that is the defect being traced, and a
    # green job would read as "deterministic".
    return 0 if same_final else 1


if __name__ == "__main__":
    raise SystemExit(main())
