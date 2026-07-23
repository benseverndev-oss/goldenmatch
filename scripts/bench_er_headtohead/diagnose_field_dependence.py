#!/usr/bin/env python
"""Measure conditional-independence violation in GoldenMatch's FS scoring.

Fellegi-Sunter sums per-field match weights ``log2(m_i / u_i)`` assuming the
comparison-vector components are INDEPENDENT given match status. If two fields'
agreements are positively correlated among NON-matches (the u-side), the true
joint u is larger than ``prod(u_i)``, so FS under-counts the denominator and
OVER-weights any pair that agrees on both — inflating the match score and driving
OVER-merges. historical_50k is the testbed where GM over-merges (precision ~0.75
vs Splink ~0.97); this quantifies how much of that is the independence assumption
vs a threshold/blocking cause a dependence correction can't fix.

What it reports (markdown, also to GITHUB_STEP_SUMMARY):
1. Per-field agreement rate on random non-match pairs (u_i), on false-merge (FP)
   pairs, and on true-merge (TP) pairs.
2. The field-pair AGREEMENT-LIFT matrix among non-matches: lift_ij =
   P(agree i AND j | non-match) / (u_i * u_j). lift >> 1 == positive dependence
   FS ignores; log2(lift) = bits of match weight FS OVER-counts on a pair
   agreeing on both.
3. Headline: the dominant correlated field bundle the FALSE merges agree on, its
   lift/bits, and the share of FP pairs it covers -> the size of the prize for a
   dependence-aware weight.

Self-contained: runs GM (auto_configure_probabilistic_df + dedupe_df) on the
dataset with GOLDENMATCH_BENCH_DUMP_PAIRS to recover the emitted (merged) pairs.
Diagnostic only; never fails a job (dataset/dep gaps -> note + exit 0).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datasets as ds_mod  # noqa: E402

# Comparison fields + how "agreement" is defined per field. Names/free-text use
# Jaro-Winkler >= threshold (mirrors the FS name scorer); discrete categoricals
# and codes use normalized exact.
_FUZZY_FIELDS = {
    "first_name": 0.85, "surname": 0.85, "name": 0.85,
    "given_name": 0.85, "family_name": 0.85,
    "occupation": 0.90, "birth_place": 0.90, "city": 0.90,
}
_EXACT_FIELDS = ("postcode", "postcode_fake", "zip", "dob")

# Deterministic RNG (Math.random / time are banned in workflow scripts, but this
# is a plain CLI; a fixed seed keeps the sampled baseline reproducible anyway).
_SEED = 20260723
_NONMATCH_SAMPLE = 40000
_FP_SAMPLE = 40000


def _norm(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _jw():
    try:
        from rapidfuzz.distance import JaroWinkler
        return JaroWinkler.normalized_similarity
    except Exception:
        return None


def _run_gm_emitted(records):
    """Run GM's probabilistic path; return emitted (merged) record-id pairs."""
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    try:
        from goldenmatch import dedupe_df
    except ImportError:
        from goldenmatch._api import dedupe_df

    import pyarrow.parquet as pq

    rid = records.column("record_id").to_pylist()
    with tempfile.TemporaryDirectory() as d:
        os.environ["GOLDENMATCH_BENCH_DUMP_PAIRS"] = d
        try:
            cfg = auto_configure_probabilistic_df(records)
            dedupe_df(records, config=cfg)
        finally:
            os.environ.pop("GOLDENMATCH_BENCH_DUMP_PAIRS", None)
        f = Path(d) / "emitted_pairs.parquet"
        if not f.exists():
            return []
        t = pq.read_table(f)
        a, b = t.column("a").to_pylist(), t.column("b").to_pylist()
        n = len(rid)
        return [(rid[x], rid[y]) for x, y in zip(a, b) if 0 <= x < n and 0 <= y < n]


def _agree_fns(fields, colidx):
    """Per-field agreement predicate over two record indices."""
    jw = _jw()
    fns = {}
    for c, vals in colidx.items():
        if c in _FUZZY_FIELDS and jw is not None:
            thr = _FUZZY_FIELDS[c]

            def make(vals=vals, thr=thr):
                def f(i, j):
                    a, b = _norm(vals[i]), _norm(vals[j])
                    if a is None or b is None:
                        return False
                    return jw(a, b) >= thr
                return f
            fns[c] = make()
        else:
            def make(vals=vals):
                def f(i, j):
                    a, b = _norm(vals[i]), _norm(vals[j])
                    return a is not None and a == b
                return f
            fns[c] = make()
    return fns


def _agreement_rates(pairs, fields, agree):
    """Fraction of pairs agreeing on each field + joint (all-in-subset) helper."""
    n = len(pairs) or 1
    marg = {c: 0 for c in fields}
    joint = {}  # frozenset(fieldpair) -> count both agree
    for i, j in pairs:
        ag = {c: agree[c](i, j) for c in fields}
        for c in fields:
            if ag[c]:
                marg[c] += 1
        for x, y in combinations(fields, 2):
            if ag[x] and ag[y]:
                joint[(x, y)] = joint.get((x, y), 0) + 1
    return {c: marg[c] / n for c in fields}, {k: v / n for k, v in joint.items()}, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="historical_50k")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    lines = [f"## Field-dependence diagnostic (conditional independence) — {args.dataset}", ""]
    try:
        records, truth = ds_mod.load_dataset(args.dataset)
    except Exception as e:
        lines.append(f"_dataset unavailable ({type(e).__name__}: {e}); skipped._")
        _emit("\n".join(lines) + "\n")
        return 0
    if _jw() is None:
        lines.append("_rapidfuzz unavailable; install goldenmatch[bench]. skipped._")
        _emit("\n".join(lines) + "\n")
        return 0

    rid = records.column("record_id").to_pylist()
    idx = {r: i for i, r in enumerate(rid)}
    tr = dict(zip(truth.column("record_id").to_pylist(),
                  truth.column("cluster_id").to_pylist()))
    cols = set(records.column_names)
    fields = [c for c in (*_FUZZY_FIELDS, *_EXACT_FIELDS) if c in cols]
    colidx = {c: records.column(c).to_pylist() for c in fields}
    agree = _agree_fns(fields, colidx)

    import random
    rng = random.Random(_SEED)

    # ── Emitted (merged) pairs -> FP / TP by truth ──
    emitted = _run_gm_emitted(records)
    fp, tp = [], []
    for a, b in emitted:
        ia, ib = idx.get(a), idx.get(b)
        if ia is None or ib is None:
            continue
        ca, cb = tr.get(a), tr.get(b)
        (tp if (ca is not None and ca == cb) else fp).append((ia, ib))
    if len(fp) > _FP_SAMPLE:
        fp = rng.sample(fp, _FP_SAMPLE)
    if len(tp) > _FP_SAMPLE:
        tp = rng.sample(tp, _FP_SAMPLE)

    # ── Non-match baseline: random record pairs (match rate is negligible) ──
    n = len(rid)
    nonmatch = []
    while len(nonmatch) < _NONMATCH_SAMPLE and n > 1:
        i, j = rng.randrange(n), rng.randrange(n)
        if i != j and tr.get(rid[i]) != tr.get(rid[j]):
            nonmatch.append((i, j))

    u, u_joint, _ = _agreement_rates(nonmatch, fields, agree)
    fp_marg, _, n_fp = _agreement_rates(fp, fields, agree)
    tp_marg, _, n_tp = _agreement_rates(tp, fields, agree)

    lines.append(f"GM emitted {len(emitted)} pairs on {args.dataset}: "
                 f"{len(tp)} true / {len(fp)} FALSE merges (sampled). "
                 f"Non-match baseline: {len(nonmatch)} random pairs.\n")

    # 1. Per-field agreement rates
    lines.append("### Per-field agreement rate")
    lines.append("")
    lines.append("| field | u_i (non-match) | FP pairs | TP pairs |")
    lines.append("| --- | --- | --- | --- |")
    for c in fields:
        lines.append(f"| {c} | {u[c]:.4f} | {fp_marg[c]:.4f} | {tp_marg[c]:.4f} |")
    lines.append("")

    # 2. Field-pair agreement LIFT among non-matches (the dependence FS ignores)
    lines.append("### Field-pair agreement lift among NON-matches "
                 "(FS assumes lift = 1)")
    lines.append("")
    lines.append("| field A | field B | u_A*u_B (indep) | P(A&B\\|nonmatch) | "
                 "lift | bits over-counted | % FP agree both |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    rows = []
    for (x, y), pj in u_joint.items():
        indep = u[x] * u[y]
        if indep <= 0 or pj <= 0:
            continue
        lift = pj / indep
        bits = math.log2(lift)
        fp_both = sum(1 for i, j in fp if agree[x](i, j) and agree[y](i, j))
        fp_pct = 100.0 * fp_both / (n_fp or 1)
        rows.append((bits, x, y, indep, pj, lift, fp_pct))
    rows.sort(reverse=True)
    for bits, x, y, indep, pj, lift, fp_pct in rows[:args.top]:
        lines.append(f"| {x} | {y} | {indep:.4f} | {pj:.4f} | {lift:.2f} | "
                     f"{bits:+.2f} | {fp_pct:.1f}% |")
    lines.append("")

    # 3. Headline verdict
    if rows:
        bits, x, y, indep, pj, lift, fp_pct = rows[0]
        # Total over-count on the average FP: sum positive-lift bits over the
        # field pairs it actually co-agrees on.
        tot = 0.0
        for i, j in fp:
            for b2, a2, c2, *_ in rows:
                if b2 <= 0:
                    break
                if agree[a2](i, j) and agree[c2](i, j):
                    tot += b2
        avg_overcount = tot / (n_fp or 1)
        lines.append("### Verdict")
        lines.append("")
        lines.append(
            f"- Dominant correlated bundle in false merges: **{x} + {y}** — "
            f"lift **{lift:.1f}x** among non-matches (FS assumes 1.0), "
            f"**{bits:+.2f} bits** over-counted, covering **{fp_pct:.0f}%** of FP pairs.")
        lines.append(
            f"- Estimated match-weight FS OVER-counts on the average false merge: "
            f"**~{avg_overcount:.2f} bits** (summed positive-lift field pairs).")
        verdict = ("STRONG: conditional-independence violation materially inflates "
                   "FS weights — a dependence-aware weight is the lever."
                   if avg_overcount >= 1.0 else
                   "WEAK: field agreements are near-independent; the over-merge is "
                   "more likely a threshold/blocking cause a dependence correction "
                   "won't fix.")
        lines.append(f"- **{verdict}**")

    _emit("\n".join(lines) + "\n")
    return 0


def _emit(md: str) -> None:
    print(md)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(md)


if __name__ == "__main__":
    raise SystemExit(main())
