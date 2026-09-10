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

VARIANTS = (
    ("default", {}),
    ("default (repeat)", {}),
    ("rayon 1 thread", {"RAYON_NUM_THREADS": "1"}),
    ("rayon 2 threads", {"RAYON_NUM_THREADS": "2"}),
    ("n_buckets 16", {"PROBE_N_BUCKETS": "16"}),
    ("n_buckets 64", {"PROBE_N_BUCKETS": "64"}),
    ("numpy route", {"GOLDENMATCH_NATIVE": "0"}),
)

_CHILD = r"""
import json, logging, os, sys, time
logging.disable(logging.WARNING)
import goldenmatch
from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
from goldenmatch.core.evaluate import evaluate_clusters
from scripts.autoconfig_quality import datasets as D
from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

name = sys.argv[1]
t0 = time.perf_counter()
loaded = getattr(D, "_" + name)()
if loaded is None:
    print(json.dumps({"error": f"{name} unavailable"}))
    sys.exit(0)
df, gt = loaded
cfg = auto_configure_probabilistic_df(df)
nb = os.environ.get("PROBE_N_BUCKETS")
if nb:
    cfg = cfg.model_copy(update={"n_buckets": int(nb)})
res = goldenmatch.dedupe_df(df, config=cfg)
ev = evaluate_clusters(res.clusters, gt).summary()
pairs, digest = _partition_fingerprint(res.clusters)
print(json.dumps({
    "f1": round(ev["f1"], 6), "precision": round(ev["precision"], 6),
    "recall": round(ev["recall"], 6), "pairs": pairs, "digest": digest,
    "n_buckets": getattr(cfg, "n_buckets", None), "cpu_count": os.cpu_count(),
    "seconds": round(time.perf_counter() - t0, 1),
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
        print(f"{label:18s} {json.dumps(results[-1][1])}", flush=True)

    ok = [(label, r) for label, r in results if "error" not in r]
    if len(ok) < 2:
        print("\nPROBE: FAIL -- fewer than two variants ran; nothing to compare.")
        return 1
    base_label, base = ok[0]
    print(f"\ncontrol: {base_label} digest {base['digest']} pairs {base['pairs']}")
    control_ok = (
        len(ok) > 1 and ok[1][0] == "default (repeat)" and ok[1][1]["digest"] == base["digest"]
    )
    print(f"control repeat identical: {control_ok}")
    for label, r in ok[1:]:
        same = r["digest"] == base["digest"]
        delta = r["pairs"] - base["pairs"]
        print(
            f"  {label:18s} {'same' if same else 'DIFFERS'}  pairs {r['pairs']} ({delta:+d})  "
            f"F1 {r['f1']:.4f}"
        )
    return 0 if control_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
