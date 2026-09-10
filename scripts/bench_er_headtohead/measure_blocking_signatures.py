"""Does WHICH blocking passes a pair co-fires in separate true pairs from noise?

``measure_meta_blocking.py`` found standard meta-blocking loses 20+ points of pair
completeness on historical_50k, and the reason is structural: 5 of auto-config's 8
passes derive from name fields, so namesakes accumulate several shared blocks from
one fact (pairs sharing exactly 4 blocks are 1.1% true), while typo'd true
duplicates fall out of the name passes and share only 1-2 blocks. Counting shared
blocks double-counts correlated passes.

This asks whether the SET of passes a pair co-fires in carries the signal the count
lacks, in three steps:

1. ORACLE (labels): group candidate pairs by pass signature (bitmask of passes they
   share a block in), rank signatures by true purity, and read the pair-completeness
   vs candidate-count curve. This is an upper bound for any signature-based pruning;
   if it cannot beat the current operating point, the idea is dead.
2. ZERO-CONFIG (no labels): fit a two-class Bernoulli mixture over the signature bits
   by EM (Fellegi-Sunter applied to blocking passes), rank signatures by the match
   log-likelihood ratio, and read the same curve. The gap to the oracle is what a
   real implementation would forfeit.
3. GROUPED (no labels): the mixture assumes passes are independent within a class,
   which correlated name passes break. Measure pass-to-pass co-firing (phi over the
   candidate pairs, no labels), merge passes whose phi clears a threshold into one
   "any of these fired" feature, and rerun the oracle and EM over the groups.

EM is run from several starts and two label-free rules pick among the modes it finds:
``max-likelihood`` (the textbook choice) and ``smallest-prior`` (matches are rare
among candidate pairs, so keep the mode that says so). A third variant fixes u from
RANDOM record pairs, Splink-style, and fits only m and the prior over the full pair
space; it is kept as a measured negative.

The candidate set is the pipeline's own: rebuilt with ``build_blocks`` and checked
against ``measure_blocking_profile`` before anything is reported. ``--signatures-json``
reuses a previous run's ``--out`` and skips the rebuild.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PC_TARGETS = (0.80, 0.85, 0.8793, 0.90, 0.93)
BUDGETS = (1_000_000, 2_000_000, 4_000_000, 6_000_000, 8_000_000, 11_977_484)
EM_STARTS = tuple(
    (pi, pm, pu)
    for pi in (0.001, 0.01, 0.05, 0.2, 0.5)
    for pm, pu in ((0.8, 0.05), (0.6, 0.3), (0.9, 0.5))
)


def _signatures(df_rowid, cfg, keys, gt):
    import duckdb
    import pyarrow as pa
    from goldenmatch.core.blocker import measure_blocking_profile
    from goldenmatch.core.frame import to_frame
    from measure_meta_blocking import _members_for_passes

    passes, blocks, rids, per_pass = _members_for_passes(to_frame(df_rowid), cfg.blocking, keys)
    rebuilt = sum(p["comparisons"] for p in per_pass)
    probe = cfg.model_copy(
        update={"blocking": cfg.blocking.model_copy(update={"passes": list(keys)})}
    )
    prof = measure_blocking_profile(df_rowid, probe)
    if prof is None or int(prof.total_comparisons) != rebuilt:
        raise SystemExit(
            f"RECONSTRUCTION MISMATCH: rebuilt {rebuilt:,} vs profile {None if prof is None else prof.total_comparisons}"
        )

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='3GB'")
    con.register(
        "members_arrow",
        pa.table(
            {
                "pass": pa.array(passes, pa.int32()),
                "block": pa.array(blocks, pa.int64()),
                "rid": pa.array(rids, pa.int64()),
            }
        ),
    )
    con.register(
        "gt_arrow",
        pa.table(
            {
                "i": pa.array([a for a, _ in gt], pa.int64()),
                "j": pa.array([b for _, b in gt], pa.int64()),
            }
        ),
    )
    con.execute("CREATE TEMP TABLE m AS SELECT * FROM members_arrow")
    con.execute("CREATE TEMP TABLE g AS SELECT least(i, j) AS i, greatest(i, j) AS j FROM gt_arrow")
    con.execute(
        "CREATE TEMP TABLE e AS SELECT a.rid AS i, b.rid AS j, bit_or((1::BIGINT) << a.pass) AS mask "
        "FROM m a JOIN m b ON a.block = b.block AND a.rid < b.rid GROUP BY 1, 2"
    )
    rows = con.execute(
        "SELECT e.mask, count(*) AS pairs, sum((g.i IS NOT NULL)::INT) AS true_pairs "
        "FROM e LEFT JOIN g ON g.i = e.i AND g.j = e.j GROUP BY 1"
    ).fetchall()
    total_pairs = con.execute("SELECT count(*) FROM e").fetchone()[0]
    con.close()
    sigs = [{"mask": int(mk), "pairs": int(p), "true_pairs": int(t or 0)} for mk, p, t in rows]
    return sigs, per_pass, rebuilt, int(total_pairs)


def _curve(sigs_sorted, n_gt):
    cum_pairs = cum_true = 0
    pts = []
    for s in sigs_sorted:
        cum_pairs += s["pairs"]
        cum_true += s["true_pairs"]
        pts.append((cum_pairs, cum_true / n_gt))
    return pts


def _read_curve(pts):
    at_target = {}
    for t in PC_TARGETS:
        hit = next((c for c, pc in pts if pc >= t - 1e-12), None)
        at_target[t] = hit
    at_budget = {}
    for b in BUDGETS:
        best = 0.0
        for c, pc in pts:
            if c <= b:
                best = pc
            else:
                break
        at_budget[b] = best
    return at_target, at_budget


def _clip(v):
    return min(max(v, 1e-4), 1 - 1e-4)


def _em(sigs, n, start=(0.02, 0.8, 0.05), iters=500):
    """Two-class Bernoulli mixture over signature bits, weighted by pair counts.

    Returns (llr per signature, prior, m, u, log-likelihood)."""
    pi, pm0, pu0 = start
    X = [[(s["mask"] >> k) & 1 for k in range(n)] for s in sigs]
    w = [s["pairs"] for s in sigs]
    total = float(sum(w))
    pm, pu = [pm0] * n, [pu0] * n
    ll = 0.0
    for _ in range(iters):
        resp, ll = [], 0.0
        for x, c in zip(X, w):
            lm = math.log(pi) + sum(math.log(pm[k] if x[k] else 1 - pm[k]) for k in range(n))
            lu = math.log(1 - pi) + sum(math.log(pu[k] if x[k] else 1 - pu[k]) for k in range(n))
            mx = max(lm, lu)
            z = math.exp(lm - mx) + math.exp(lu - mx)
            ll += c * (mx + math.log(z))
            resp.append(math.exp(lm - mx) / z)
        wm = sum(r * c for r, c in zip(resp, w))
        wu = total - wm
        pi = min(max(wm / total, 1e-6), 1 - 1e-6)
        pm = [
            _clip(sum(r * c * x[k] for r, c, x in zip(resp, w, X)) / max(wm, 1e-12))
            for k in range(n)
        ]
        pu = [
            _clip(sum((1 - r) * c * x[k] for r, c, x in zip(resp, w, X)) / max(wu, 1e-12))
            for k in range(n)
        ]
    # Label guard: the match class is the one whose passes co-fire more.
    if sum(pm) < sum(pu):
        pm, pu, pi = pu, pm, 1 - pi
    llr = [
        sum(
            math.log((pm[k] if x[k] else 1 - pm[k]) / (pu[k] if x[k] else 1 - pu[k]))
            for k in range(n)
        )
        for x in X
    ]
    return llr, pi, pm, pu, ll


def _em_fixed_u(sigs, n, all_pairs, iters=300):
    """Splink-style: u from random record pairs, fit m and the prior over ALL pairs.

    The all-zero cell is every non-candidate pair. Returns (llr, prior, m, u)."""
    cand = sum(s["pairs"] for s in sigs)
    cells = list(sigs) + [{"mask": 0, "pairs": all_pairs - cand}]
    u = [sum(s["pairs"] for s in sigs if (s["mask"] >> k) & 1) / all_pairs for k in range(n)]
    X = [[(c["mask"] >> k) & 1 for k in range(n)] for c in cells]
    w = [c["pairs"] for c in cells]
    lu = [sum(math.log(u[k] if x[k] else 1 - u[k]) for k in range(n)) for x in X]
    pi, m = 1e-4, [0.9] * n
    for _ in range(iters):
        resp = []
        for x, luc in zip(X, lu):
            lm = math.log(pi) + sum(math.log(m[k] if x[k] else 1 - m[k]) for k in range(n))
            l0 = math.log(1 - pi) + luc
            mx = max(lm, l0)
            resp.append(math.exp(lm - mx) / (math.exp(lm - mx) + math.exp(l0 - mx)))
        wm = sum(r * c for r, c in zip(resp, w))
        pi = min(max(wm / all_pairs, 1e-9), 1 - 1e-9)
        m = [_clip(sum(r * c * x[k] for r, c, x in zip(resp, w, X)) / wm) for k in range(n)]
    llr = [
        sum(math.log((m[k] if x[k] else 1 - m[k]) / (u[k] if x[k] else 1 - u[k])) for k in range(n))
        for x in X[:-1]
    ]
    return llr, pi, m, u


def _phi(sigs, n_passes):
    """Pass-to-pass phi coefficient over candidate pairs. Uses no labels."""
    total = float(sum(s["pairs"] for s in sigs))
    ones = [0.0] * n_passes
    both = [[0.0] * n_passes for _ in range(n_passes)]
    for s in sigs:
        bits = [k for k in range(n_passes) if (s["mask"] >> k) & 1]
        for a in bits:
            ones[a] += s["pairs"]
            for b in bits:
                both[a][b] += s["pairs"]
    phi = [[0.0] * n_passes for _ in range(n_passes)]
    for a in range(n_passes):
        for b in range(n_passes):
            pa, pb, pab = ones[a] / total, ones[b] / total, both[a][b] / total
            den = math.sqrt(pa * (1 - pa) * pb * (1 - pb))
            phi[a][b] = (pab - pa * pb) / den if den > 0 else 0.0
    return phi


def _groups(phi, threshold):
    """Single-linkage merge of passes whose phi clears the threshold."""
    n = len(phi)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(n):
        for b in range(a + 1, n):
            if phi[a][b] >= threshold:
                parent[find(a)] = find(b)
    by_root: dict[int, list[int]] = {}
    for k in range(n):
        by_root.setdefault(find(k), []).append(k)
    return sorted(by_root.values())


def _regroup(sigs, groups):
    """Collapse pass signatures onto group signatures: a group fires if any member did."""
    agg: dict[int, dict] = {}
    for s in sigs:
        gm = 0
        for g, members in enumerate(groups):
            if any((s["mask"] >> k) & 1 for k in members):
                gm |= 1 << g
        a = agg.setdefault(gm, {"mask": gm, "pairs": 0, "true_pairs": 0})
        a["pairs"] += s["pairs"]
        a["true_pairs"] += s["true_pairs"]
    return list(agg.values())


def _label(mask, per_pass):
    names = []
    for p in per_pass:
        if (mask >> p["pass"]) & 1:
            t = "+".join(x for x in p["transforms"] if x not in ("lowercase", "strip")) or "raw"
            names.append(f"{'/'.join(p['fields'])}[{t}]")
    return " & ".join(names)


def _rank(sigs, llr):
    order = sorted(range(len(sigs)), key=lambda i: -llr[i])
    return [sigs[i] for i in order]


def _report_curve(name, ranked, n_gt, report):
    at_t, at_b = _read_curve(_curve(ranked, n_gt))
    report["curves"][name] = {
        "candidates_for_pc": {str(k): v for k, v in at_t.items()},
        "pc_at_budget": {str(k): v for k, v in at_b.items()},
    }
    print(f"\n{name}:")
    print(
        "  candidates needed for PC:  "
        + "  ".join(
            f"{t:.4f}->{('%.2fM' % (v / 1e6)) if v else 'unreachable'}" for t, v in at_t.items()
        )
    )
    print(
        "  best PC within budget:     "
        + "  ".join(f"{b / 1e6:.1f}M->{v:.4f}" for b, v in at_b.items())
    )


def _oracle(sigs):
    return sorted(sigs, key=lambda s: (-(s["true_pairs"] / s["pairs"]), -s["true_pairs"]))


def _evaluate_features(title, sigs, names, n_gt, all_pairs, out):
    """Oracle, multi-start EM under both mode rules, and fixed-u EM over one feature set."""
    n = len(names)
    print(f"\n=== {title}: {n} features, {len(sigs)} signatures ===")
    for g, name in enumerate(names):
        print(f"  feature {g}: {name}")
    fits = [_em(sigs, n, start) for start in EM_STARTS]
    modes = sorted({(round(f[1], 6), round(f[4])) for f in fits})
    print(
        f"  EM modes over {len(EM_STARTS)} starts (prior, log-likelihood): "
        + "  ".join(f"({p:.4f}, {ll:,})" for p, ll in modes)
    )
    ml = max(fits, key=lambda f: f[4])
    small = min(fits, key=lambda f: f[1])
    for rule, fit in (("max-likelihood", ml), ("smallest-prior", small)):
        _, pi, pm, pu, ll = fit
        print(
            f"  {rule} mode: prior {pi:.4f}; weights "
            + " ".join(f"{math.log2(pm[g] / pu[g]):+.2f}b" for g in range(n))
        )
        out.setdefault("em", {})[rule] = {"pi": pi, "m": pm, "u": pu, "loglik": ll}
    _report_curve(f"{title} | oracle (labels)", _oracle(sigs), n_gt, out)
    _report_curve(f"{title} | EM max-likelihood mode (no labels)", _rank(sigs, ml[0]), n_gt, out)
    _report_curve(f"{title} | EM smallest-prior mode (no labels)", _rank(sigs, small[0]), n_gt, out)
    llr, pi, m, u = _em_fixed_u(sigs, n, all_pairs)
    print(
        f"\n  fixed-u prior {pi:.2e}; weights "
        + " ".join(f"{math.log2(m[g] / u[g]):+.1f}b" for g in range(n))
    )
    out["em"]["fixed-u"] = {"pi": pi, "m": m, "u": u}
    _report_curve(
        f"{title} | EM fixed-u from random pairs (no labels)", _rank(sigs, llr), n_gt, out
    )


def _n_records_historical_50k():
    import pyarrow.parquet as pq

    from scripts.autoconfig_quality import datasets as D

    return pq.ParquetFile(D._VENDORED / "historical_50k.parquet").metadata.num_rows


def _load_or_build(args):
    if args.signatures_json:
        data = json.loads(Path(args.signatures_json).read_text(encoding="utf-8"))
        sigs = [
            {"mask": s["mask"], "pairs": s["pairs"], "true_pairs": s["true_pairs"]}
            for s in data["signatures"]
        ]
        n_records = data.get("n_records") or _n_records_historical_50k()
        print(f"loaded {len(sigs)} signatures from {args.signatures_json} (rebuild skipped)")
        return sigs, data["passes"], int(data["total_pairs"]), int(data["gt_pairs"]), n_records

    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from measure_surname_passes import pass_from_spec

    from scripts.autoconfig_quality import datasets as D

    loaded = getattr(D, f"_{args.dataset}")()
    if loaded is None:
        raise SystemExit(f"dataset {args.dataset!r} is unavailable here")
    df, gt = loaded
    cfg = auto_configure_probabilistic_df(df)
    df_rowid = df.with_row_index("__row_id__") if "__row_id__" not in df.columns else df
    keys = list(cfg.blocking.resolved_keys())
    # "field:t1,t2" or a compound "field:t1,t2+field:t3" with per-field transforms.
    keys.extend(pass_from_spec(spec) for spec in args.extra)
    sigs, per_pass, rebuilt, total_pairs = _signatures(df_rowid, cfg, keys, gt)
    print(f"passes={len(keys)}  comparisons rebuilt {rebuilt:,} == profile (known-positive OK)")
    return sigs, per_pass, total_pairs, len(gt), df.height


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--extra", action="append", default=[], help='extra pass "field:t1,t2" (repeatable)'
    )
    ap.add_argument(
        "--phi",
        action="append",
        type=float,
        default=[],
        help="merge passes whose phi clears this threshold (repeatable; default 0.2 0.5 0.7)",
    )
    ap.add_argument(
        "--dataset",
        default="historical_50k",
        help="scripts.autoconfig_quality.datasets loader name (default historical_50k)",
    )
    ap.add_argument("--signatures-json", default=None, help="reuse a previous --out")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    thresholds = args.phi or [0.2, 0.5, 0.7]

    sigs, per_pass, total_pairs, n_gt, n_records = _load_or_build(args)
    all_pairs = n_records * (n_records - 1) // 2
    n_passes = len(per_pass)
    reachable = sum(s["true_pairs"] for s in sigs) / n_gt
    print(
        f"records {n_records:,}; distinct candidate pairs {total_pairs:,}; signatures {len(sigs)}; "
        f"reachable PC {reachable:.4f}; true share of candidates "
        f"{sum(s['true_pairs'] for s in sigs) / total_pairs:.4f}"
    )

    print("\nlargest signatures (pairs, true, purity):")
    for s in sorted(sigs, key=lambda s: -s["pairs"])[:12]:
        print(
            f"  {s['pairs']:>10,} {s['true_pairs']:>8,} {s['true_pairs'] / s['pairs']:.3f}  {_label(s['mask'], per_pass)}"
        )
    print("\npurest signatures carrying >= 2,000 true pairs:")
    for s in sorted(
        (s for s in sigs if s["true_pairs"] >= 2000), key=lambda s: -s["true_pairs"] / s["pairs"]
    )[:12]:
        print(
            f"  {s['pairs']:>10,} {s['true_pairs']:>8,} {s['true_pairs'] / s['pairs']:.3f}  {_label(s['mask'], per_pass)}"
        )

    phi = _phi(sigs, n_passes)
    print("\npass-to-pass phi over candidate pairs (no labels):")
    for a in range(n_passes):
        print(
            f"  {a} {_label(1 << a, per_pass):40s} "
            + " ".join(f"{phi[a][b]:+.2f}" for b in range(n_passes))
        )

    report = {
        "passes": per_pass,
        "n_records": n_records,
        "total_pairs": total_pairs,
        "gt_pairs": n_gt,
        "reachable_pc": reachable,
        "phi": phi,
        "feature_sets": {},
    }
    ungrouped = {"groups": [[k] for k in range(n_passes)], "curves": {}}
    _evaluate_features(
        "ungrouped",
        sigs,
        [_label(1 << k, per_pass) for k in range(n_passes)],
        n_gt,
        all_pairs,
        ungrouped,
    )
    report["feature_sets"]["ungrouped"] = ungrouped
    for thr in thresholds:
        groups = _groups(phi, thr)
        sub = {"groups": groups, "curves": {}}
        _evaluate_features(
            f"grouped phi>={thr}",
            _regroup(sigs, groups),
            [" | ".join(_label(1 << k, per_pass) for k in g) for g in groups],
            n_gt,
            all_pairs,
            sub,
        )
        report["feature_sets"][f"phi>={thr}"] = sub

    if args.out:
        Path(args.out).write_text(
            json.dumps({"signatures": sigs, **report}, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
