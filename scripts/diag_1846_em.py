"""#1846 probe 2: dump the EM result for historical_50k (DIAG1846EM lines).

Probe 1 proved config, blocking and the 12,608,858 candidate pairs are
BYTE-IDENTICAL on Windows (0.82) and Linux CI (0.33). Same input, different
answer -> the divergence is inside FS/EM, not upstream.

Ruled out since:
  * cpu_count (n_buckets 48 vs 16, FS workers 12 vs 4): forcing cpu_count=4
    locally still PASSES at 0.8236. The "output pairs are invariant to bucket
    count" docstring holds.

Weighted f1 is fine on BOTH hosts (0.3409 -> 0.3421); only f1_probabilistic
collapses. So the similarity scorers agree -- it is the FS layer on top.

train_em is seeded (seed=42) and samples within-block pairs, and the blocks are
identical, so the sample should be identical too. If m/u or match_weights
differ, EM diverged; if `converged` is False on Linux, it hit max_iterations
and the weights are unconverged garbage. Either way THIS names it.

Run: python scripts/diag_1846_em.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "python" / "goldenmatch"))
sys.path.insert(0, str(ROOT))

P = "DIAG1846EM"


def _fmt(xs, n=4):
    if xs is None:
        return None
    return [round(float(x), n) for x in xs]


def main() -> int:
    import goldenmatch.core.probabilistic as prob

    real_train_em = prob.train_em
    calls: list = []

    def traced(*a, **kw):
        res = real_train_em(*a, **kw)
        calls.append(res)
        i = len(calls)
        print(f"{P} call={i} converged={res.converged} iterations={res.iterations} "
              f"proportion_matched={round(float(res.proportion_matched), 6)}", flush=True)
        for field in sorted(res.match_weights):
            print(f"{P} call={i} field={field} "
                  f"m={_fmt(res.m_probs.get(field))} "
                  f"u={_fmt(res.u_probs.get(field))} "
                  f"w={_fmt(res.match_weights.get(field))}", flush=True)
        tf = res.tf_freqs
        print(f"{P} call={i} tf_fields={sorted(tf) if tf else None} "
              f"tf_collision={ {k: round(v,6) for k,v in (res.tf_collision or {}).items()} }",
              flush=True)
        return res

    prob.train_em = traced
    # The FS pipeline may hold its own reference; patch the common re-exports too.
    for modname in ("goldenmatch.core.probabilistic_fast", "goldenmatch.core.pipeline"):
        try:
            m = __import__(modname, fromlist=["*"])
            if hasattr(m, "train_em"):
                m.train_em = traced
        except Exception:
            pass

    import polars as pl
    p = ROOT / "scripts" / "autoconfig_quality" / "vendored" / "historical_50k.parquet"
    if not p.exists():
        print(f"{P} dataset=ABSENT", flush=True)
        return 0

    # Drive the SAME path the gate drives, so the EM we trace is the gate's EM.
    sys.argv = ["autoconfig_quality", "report", "--datasets", "historical_50k"]
    from scripts.autoconfig_quality.__main__ import main as harness

    rc = harness()
    print(f"{P} train_em_calls={len(calls)}", flush=True)
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
