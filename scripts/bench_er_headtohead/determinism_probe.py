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
VARIANTS = (
    ("default", {}),
    ("default (repeat)", {}),
    ("PYTHONHASHSEED=0", {"PYTHONHASHSEED": "0"}),
    ("PYTHONHASHSEED=0 (repeat)", {"PYTHONHASHSEED": "0"}),
    ("PYTHONHASHSEED=1", {"PYTHONHASHSEED": "1"}),
    ("PYTHONHASHSEED=1 (repeat)", {"PYTHONHASHSEED": "1"}),
    ("POLARS_MAX_THREADS=1", {"POLARS_MAX_THREADS": "1"}),
    ("POLARS_MAX_THREADS=1 (repeat)", {"POLARS_MAX_THREADS": "1"}),
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
        print(f"{label:30s} {json.dumps(results[-1][1])}", flush=True)

    ok = dict((label, r) for label, r in results if "error" not in r)
    if len(ok) < 2:
        print("\nPROBE: FAIL -- fewer than two variants ran; nothing to compare.")
        return 1
    print("\nrepeat pairs (a setting only removes the drift if its own two runs agree):")
    default_stable = None
    for label, _ in VARIANTS:
        if not label.endswith(" (repeat)"):
            continue
        base_label = label[: -len(" (repeat)")]
        a, b = ok.get(base_label), ok.get(label)
        if a is None or b is None:
            print(f"  {base_label:24s} could not compare (a run failed)")
            continue
        same = a["digest"] == b["digest"]
        if base_label == "default":
            default_stable = same
        print(
            f"  {base_label:24s} {'IDENTICAL' if same else 'DIFFERS'}  "
            f"pairs {a['pairs']} vs {b['pairs']}  digests {a['digest']} vs {b['digest']}"
        )
    # Exit non-zero when the default drifts: that is the defect this probe tracks,
    # and a green job would read as "deterministic".
    return 0 if default_stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
