"""Memory-capped peak-RSS bench for the fused GOLDEN-record stage (the capacity
win). The fused Arrow kernel holds its intermediates as Rust Vecs -- no wide
`multi_df`, no per-cluster Python dicts -- so it should build the SAME golden
records at materially lower peak RSS than `build_golden_records_batch`. This
bench measures exactly that: on IDENTICAL clusters, peak RSS of

  (a) reference   = core.golden.build_golden_records_batch  (the RSS-heavy path:
                    wide multi_df + one dict per cluster), and
  (b) golden_fused = core.golden_fused.run_golden_fused_arrow (Rust-Vec kernel),

under a fixed cgroup RAM cap. The HEADLINE metric is the peak-RSS RATIO
(reference / fused) -- the capacity win. Wall is reported (min-of-N) but is
expected to be a WASH and is NOT gated.

Clusters are built DIRECTLY (assign __row_id__ + __cluster_id__ + several user
columns) so survivorship has real work; a `field_groups` + `field_rules` config
routes the reference onto the EXACT per-cluster survivorship path (the one that
allocates the multi_df + per-cluster dicts), which is what the fused kernel
beats -- NOT the fast polars-native columnar path.

Peak-RSS discipline (mirrors scripts/bench_match_fused_memcap.py): each path runs
in its OWN process (the workflow wraps each invocation in its own
`systemd-run --scope -p MemoryMax=<cap>`), a warm-up run is discarded (cold-start
GC / import inflates the first run), gc.collect() runs between iterations, and
peak is read from the cgroup's high-water `memory.peak` (plus a
`resource.ru_maxrss` fallback). Wall is min-of-N.

Usage (under a cap, one path per scope):
  systemd-run --scope -p MemoryMax=24G -p MemorySwapMax=0 -- \
    python bench_golden_fused_memcap.py --path golden_fused --n 5000000
  systemd-run --scope -p MemoryMax=24G -p MemorySwapMax=0 -- \
    python bench_golden_fused_memcap.py --path reference    --n 5000000
"""

import argparse
import gc
import json
import os
import time

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

AVG_CLUSTER_SIZE = 4


def _gen(n: int, seed: int):
    """A clustered frame: `n` rows in `n // AVG_CLUSTER_SIZE` multi-member
    clusters (every cluster size AVG_CLUSTER_SIZE), with per-member value
    variation + injected nulls so survivorship (most_complete / longest_value /
    first_non_null / group allow_fill) has real work. Vectorized via numpy so 5M
    rows generate in ~1s. Surnames spread across many soundex codes (per
    feedback_synthetic_surname_fixtures) for realism."""
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(seed)

    firsts = np.array(["john", "jane", "mary", "mike", "sara", "dave", "lisa",
                       "paul", "anna", "mark", "emma", "luke"])
    lasts = np.array(["smith", "brown", "taylor", "wilson", "clark", "young",
                      "harris", "nguyen", "obrien", "zimmerman", "underwood",
                      "fitzgerald", "vasquez", "delacroix", "kowalski", "petrov"])
    streets = np.array(["main st", "oak ave", "elm rd", "pine ln", "cedar blvd",
                        "maple ct", "birch way", "aspen dr"])
    suburbs = np.array(["riverside", "hilltop", "lakeview", "brookfield",
                        "fairview", "meadowbrook", "glenwood"])
    states = np.array(["nsw", "vic", "qld", "wa", "sa", "tas"])
    sources = np.array(["crm", "erp", "web", "import"])

    row_id = np.arange(n, dtype=np.int64)
    cluster_id = (row_id // AVG_CLUSTER_SIZE).astype(np.int64)

    def pick(arr):
        return arr[rng.integers(0, len(arr), n)]

    def with_nulls(vals, p_null):
        out = vals.astype(object)
        mask = rng.random(n) < p_null
        out[mask] = None
        return out.tolist()

    given = with_nulls(pick(firsts), 0.15)
    # surname carries a per-row suffix so longest_value has a real tie-break
    suffix = np.where(rng.random(n) < 0.2, "s", "")
    surname = np.char.add(pick(lasts).astype(str), suffix)
    street = with_nulls(pick(streets), 0.12)
    suburb = with_nulls(pick(suburbs), 0.10)
    postcode = rng.integers(2000, 3000, n).astype(str).tolist()
    state = pick(states).tolist()
    source = pick(sources).tolist()
    dob = np.where(
        rng.random(n) < 0.1,
        None,
        (rng.integers(1950, 2001, n) * 10000
         + rng.integers(1, 13, n) * 100
         + rng.integers(1, 29, n)).astype(str),
    ).tolist()

    return pl.DataFrame({
        "__row_id__": row_id,
        "__cluster_id__": cluster_id,
        "__source__": source,
        "given_name": given,
        "surname": surname.tolist(),
        "street": street,
        "suburb": suburb,
        "postcode": postcode,
        "state": state,
        "date_of_birth": dob,
    })


def _rules():
    """A survivorship config that routes the reference onto the EXACT per-cluster
    path (a field_group forces it off the fast columnar path). Group = the
    address block (lock-step, allow_fill); field_rules exercise the scalar
    strategies the kernel beats on RSS."""
    from goldenmatch.config.schemas import (
        GoldenFieldRule,
        GoldenGroupRule,
        GoldenRulesConfig,
    )

    return GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "given_name": GoldenFieldRule(strategy="most_complete"),
            "surname": GoldenFieldRule(strategy="longest_value"),
            "date_of_birth": GoldenFieldRule(strategy="first_non_null"),
            "__source__": GoldenFieldRule(strategy="majority_vote"),
        },
        field_groups=[
            GoldenGroupRule(
                name="address_block",
                columns=["street", "suburb", "postcode", "state"],
                strategy="most_complete",
                allow_fill=True,
            ),
        ],
    )


def _run_reference(df, rules):
    import polars as pl
    from goldenmatch.core.golden import build_golden_records_batch

    # Mirror _multi_df_from_frames' size>1 filter (the reference does NOT
    # self-filter). Every cluster is multi-member here, so this is a near no-op
    # copy -- both paths pay one filter, keeping the peak comparison apples-to-apples.
    multi = df.filter(pl.len().over("__cluster_id__") > 1)
    records = build_golden_records_batch(multi, rules)
    return len(records)


def _run_fused(df, rules):
    from goldenmatch.core.golden_fused import run_golden_fused_arrow

    out = run_golden_fused_arrow(df, rules)  # self-filters singletons
    if out is None:
        raise SystemExit("run_golden_fused_arrow DECLINED the bench config -- "
                         "the config must be fused-covered. Aborting.")
    return out.height


def _cgroup_peak_mb():
    """Best-effort peak RSS of this process's cgroup (systemd scope), MB."""
    try:
        with open("/proc/self/cgroup") as fh:
            rel = fh.read().strip().split(":")[-1]
        path = f"/sys/fs/cgroup{rel}/memory.peak"
        with open(path) as fh:
            return round(int(fh.read().strip()) / 1024 / 1024, 1)
    except Exception:
        return None


def _maxrss_mb():
    """resource.getrusage peak RSS fallback (Linux: ru_maxrss is KB). None on
    Windows / where resource is unavailable."""
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=["reference", "golden_fused"], required=True)
    ap.add_argument("--n", type=int, required=True, help="total clustered rows")
    ap.add_argument("--repeats", type=int, default=3, help="measured runs (min-of-N wall)")
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    df = _gen(args.n, args.seed)
    rules = _rules()
    runner = _run_reference if args.path == "reference" else _run_fused
    n_clusters = df.select(__import__("polars").col("__cluster_id__").n_unique()).item()

    # Warm-up (discarded): cold-start import + GC inflate the first run.
    out_count = runner(df, rules)
    gc.collect()

    walls = []
    for _ in range(args.repeats):
        gc.collect()
        t0 = time.perf_counter()
        out_count = runner(df, rules)
        walls.append(time.perf_counter() - t0)
        gc.collect()

    print(json.dumps({
        "path": args.path,
        "n": args.n,
        "clusters": n_clusters,
        "golden_records": out_count,
        "wall_s": round(min(walls), 3),
        "wall_s_all": [round(w, 3) for w in walls],
        "cgroup_peak_mb": _cgroup_peak_mb(),
        "maxrss_mb": _maxrss_mb(),
        "status": "ok",
    }))


if __name__ == "__main__":
    main()
