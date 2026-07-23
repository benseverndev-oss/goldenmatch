#!/usr/bin/env python
"""Diagnose what GoldenMatch's FALSE merges share on a labeled dataset.

Reads the ``emitted_pairs.parquet`` the pipeline drops under
``GOLDENMATCH_BENCH_DUMP_PAIRS`` (row-id pairs, written by run_panel's `_run_gm`)
for one dataset, joins ground truth, and profiles the FALSE-POSITIVE pairs: for
each comparison field, what fraction of false merges AGREE on it, and — for the
skewed categorical fields — how COMMON the agreed-on value is (its frequency
percentile in the column).

This answers the question term-frequency (TF) adjustment hinges on: are the false
merges driven by agreement on COMMON values ("Smith", a frequent occupation), the
regime TF down-weights? If FP pairs overwhelmingly agree on high-frequency
categorical values, TF is the right lever; if they instead share only a blocking
key while disagreeing on discriminators, the over-merge is a fan-out/threshold
problem TF can't fix.

Pass ``--pairs-off`` and ``--pairs-on`` to compare FP counts across the flag
(did TF actually remove false merges?). The value profile is reported for the ON
set (or whichever is provided). Emits a markdown section to stdout and, when
``GITHUB_STEP_SUMMARY`` is set, appends there too. Diagnostic only — never fails.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datasets as ds_mod  # noqa: E402

# Skewed categorical comparison fields whose value-frequency TF targets. Names
# vary by dataset; we intersect with the columns actually present.
_CATEGORICAL_FIELDS = [
    "first_name", "surname", "name", "given_name", "family_name",
    "occupation", "birth_place", "city", "postcode", "postcode_fake",
]


def _norm(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _emitted_record_pairs(pairs_dir: Path, rid: list) -> list[tuple]:
    """Read emitted_pairs.parquet (row-id space) -> record_id pairs."""
    f = pairs_dir / "emitted_pairs.parquet"
    if not f.exists():
        return []
    t = pq.read_table(f)
    a = t.column("a").to_pylist()
    b = t.column("b").to_pylist()
    n = len(rid)
    out = []
    for x, y in zip(a, b):
        if 0 <= x < n and 0 <= y < n:
            out.append((rid[x], rid[y]))
    return out


def _fp_pairs(pairs: list[tuple], truth_cluster: dict) -> list[tuple]:
    """Pairs whose two records are in DIFFERENT truth clusters (false merges)."""
    fp = []
    for a, b in pairs:
        ca, cb = truth_cluster.get(a), truth_cluster.get(b)
        if ca is None or cb is None or ca != cb:
            fp.append((a, b))
    return fp


def _percentile(count: int, sorted_counts: list[int]) -> float:
    """Fraction of the column's distinct values with count <= this one (a high
    percentile => this value is among the MOST common)."""
    if not sorted_counts:
        return 0.0
    import bisect
    return bisect.bisect_right(sorted_counts, count) / len(sorted_counts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--pairs-on", type=Path, default=None,
                    help="dir with emitted_pairs.parquet for the TF-ON run")
    ap.add_argument("--pairs-off", type=Path, default=None,
                    help="dir with emitted_pairs.parquet for the TF-OFF run")
    ap.add_argument("--max-report", type=int, default=8)
    args = ap.parse_args()

    lines = [f"## Over-merge diagnostic — {args.dataset}", ""]
    try:
        records, truth = ds_mod.load_dataset(args.dataset)
    except Exception as e:  # dataset unavailable in this env -> note and exit 0
        lines.append(f"_dataset unavailable ({type(e).__name__}: {e}); skipped._")
        _emit("\n".join(lines) + "\n")
        return 0

    rid = records.column("record_id").to_pylist()
    tr = dict(zip(truth.column("record_id").to_pylist(),
                  truth.column("cluster_id").to_pylist()))
    cols = set(records.column_names)
    fields = [c for c in _CATEGORICAL_FIELDS if c in cols]
    colvals = {c: records.column(c).to_pylist() for c in fields}
    idx = {r: i for i, r in enumerate(rid)}
    # Per-field frequency of normalized values (for the commonness profile).
    freq = {c: Counter(_norm(v) for v in colvals[c]) for c in fields}
    sorted_counts = {c: sorted(freq[c].values()) for c in fields}

    # FP counts across the flag.
    counts = {}
    for label, d in (("OFF", args.pairs_off), ("ON", args.pairs_on)):
        if d is None:
            continue
        pairs = _emitted_record_pairs(d, rid)
        fp = _fp_pairs(pairs, tr)
        counts[label] = (len(pairs), len(fp))
    if counts:
        lines.append("| flag | emitted pairs | false merges |")
        lines.append("| --- | --- | --- |")
        for label in ("OFF", "ON"):
            if label in counts:
                em, fp = counts[label]
                lines.append(f"| {label} | {em} | {fp} |")
        if "OFF" in counts and "ON" in counts:
            d_fp = counts["ON"][1] - counts["OFF"][1]
            lines.append("")
            lines.append(f"**TF changed false merges by {d_fp:+d}** "
                         f"({counts['OFF'][1]} -> {counts['ON'][1]}).")
        lines.append("")

    # Value profile of the false merges (prefer the ON set; else whatever exists).
    prof_dir = args.pairs_on or args.pairs_off
    if prof_dir is not None and fields:
        pairs = _emitted_record_pairs(prof_dir, rid)
        fp = _fp_pairs(pairs, tr)
        which = "ON" if args.pairs_on else "OFF"
        lines.append(f"### What the {which}-run false merges agree on "
                     f"({len(fp)} FP pairs)")
        lines.append("")
        lines.append("| field | % of FP pairs agreeing | median freq-percentile "
                     "of agreed value | top agreed values |")
        lines.append("| --- | --- | --- | --- |")
        for c in fields:
            vals = colvals[c]
            agree_vals = []
            for a, b in fp:
                ia, ib = idx.get(a), idx.get(b)
                if ia is None or ib is None:
                    continue
                va, vb = _norm(vals[ia]), _norm(vals[ib])
                if va is not None and va == vb:
                    agree_vals.append(va)
            n_fp = len(fp) or 1
            pct = 100.0 * len(agree_vals) / n_fp
            if agree_vals:
                pctiles = sorted(_percentile(freq[c][v], sorted_counts[c])
                                 for v in agree_vals)
                med = pctiles[len(pctiles) // 2]
                top = ", ".join(f"{v}({n})" for v, n in
                                Counter(agree_vals).most_common(args.max_report))
            else:
                med, top = 0.0, "-"
            lines.append(f"| {c} | {pct:.1f}% | {med:.2f} | {top} |")
        lines.append("")
        lines.append("_A high agree-% on a field with a high median freq-percentile "
                     "means false merges concentrate on COMMON values of that field — "
                     "exactly what TF down-weights. Low freq-percentile (rare agreed "
                     "values) or low agree-% across all discriminators points to a "
                     "blocking/threshold cause TF can't address._")

    _emit("\n".join(lines) + "\n")
    return 0


def _emit(md: str) -> None:
    print(md)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a") as fh:
            fh.write(md)


if __name__ == "__main__":
    raise SystemExit(main())
