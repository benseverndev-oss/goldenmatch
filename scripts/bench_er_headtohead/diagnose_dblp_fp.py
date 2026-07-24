#!/usr/bin/env python
"""Profile the residual FALSE merges of the CALIBRATED FS model on dblp_acm.

Threshold calibration rescued dblp_acm recall (0.27 -> 0.93) by lowering the
cutoff, but precision landed at ~0.80 — the 20% residual false merges are the
next FS-accuracy target. This runs GM (calibration ON via env) on dblp_acm,
splits the emitted pairs into true/false merges by truth, and for each
bibliographic field (title / authors / venue / year) reports the agreement rate
on FALSE-merge vs TRUE-merge vs random non-match pairs.

Read it as: a field where FALSE merges agree ~as often as TRUE merges is
non-discriminative (shared by both, so agreeing on it shouldn't carry match
weight); a field FALSE merges agree on at HIGH rate while TRUE merges disagree
would be an over-weighted scorer. That localizes the lever (drop/down-weight a
non-discriminative field, or tighten a scorer).

Self-contained (runs GM, no dump). Diagnostic only; never fails a job.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datasets as ds_mod  # noqa: E402

# Bibliographic fields + agreement definition. Titles/authors/venue via
# Jaro-Winkler >= threshold; year exact.
_FUZZY_FIELDS = {"title": 0.90, "authors": 0.80, "venue": 0.85}
_EXACT_FIELDS = ("year",)
_SEED = 20260724
_SAMPLE = 30000


def _norm(v):
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
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    try:
        from goldenmatch import dedupe_df
    except ImportError:
        from goldenmatch._api import dedupe_df
    rid = records.column("record_id").to_pylist()
    ded = dedupe_df(records, config=auto_configure_probabilistic_df(records))
    clusters = getattr(ded, "clusters", None) or {}
    # Emitted "merges" = within-predicted-cluster pairs (sampled if huge).
    import itertools
    pairs = []
    for c in clusters.values():
        members = c["members"] if isinstance(c, dict) else c.members
        recs = [rid[m] for m in members]
        if len(recs) > 1:
            pairs.extend(itertools.combinations(sorted(recs), 2))
    return pairs


def _agree_fns(fields, colidx):
    jw = _jw()
    fns = {}
    for c, vals in colidx.items():
        if c in _FUZZY_FIELDS and jw is not None:
            thr = _FUZZY_FIELDS[c]

            def make(vals=vals, thr=thr):
                def f(a, b):
                    x, y = _norm(vals[a]), _norm(vals[b])
                    return x is not None and y is not None and jw(x, y) >= thr
                return f
            fns[c] = make()
        else:
            def make(vals=vals):
                def f(a, b):
                    x, y = _norm(vals[a]), _norm(vals[b])
                    return x is not None and x == y
                return f
            fns[c] = make()
    return fns


def _rate(pairs, fields, agree):
    n = len(pairs) or 1
    return {c: sum(1 for a, b in pairs if agree[c](a, b)) / n for c in fields}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dblp_acm")
    args = ap.parse_args()
    lines = [f"## Residual-FP diagnostic (calibrated) — {args.dataset}", ""]
    try:
        records, truth = ds_mod.load_dataset(args.dataset)
    except Exception as e:
        lines.append(f"_unavailable ({type(e).__name__}: {e}); skipped._")
        _emit("\n".join(lines) + "\n")
        return 0
    if _jw() is None:
        lines.append("_rapidfuzz unavailable; skipped._")
        _emit("\n".join(lines) + "\n")
        return 0

    import random
    rng = random.Random(_SEED)
    rid = records.column("record_id").to_pylist()
    idx = {r: i for i, r in enumerate(rid)}
    tr = dict(zip(truth.column("record_id").to_pylist(),
                  truth.column("cluster_id").to_pylist()))
    cols = set(records.column_names)
    fields = [c for c in (*_FUZZY_FIELDS, *_EXACT_FIELDS) if c in cols]
    colidx = {c: records.column(c).to_pylist() for c in fields}
    agree = _agree_fns(fields, colidx)

    emitted = _run_gm_emitted(records)
    fp, tp = [], []
    for a, b in emitted:
        ia, ib = idx.get(a), idx.get(b)
        if ia is None or ib is None:
            continue
        ca, cb = tr.get(a), tr.get(b)
        (tp if (ca is not None and ca == cb) else fp).append((ia, ib))
    if len(fp) > _SAMPLE:
        fp = rng.sample(fp, _SAMPLE)
    if len(tp) > _SAMPLE:
        tp = rng.sample(tp, _SAMPLE)
    n = len(rid)
    nonmatch = []
    while len(nonmatch) < _SAMPLE and n > 1:
        i, j = rng.randrange(n), rng.randrange(n)
        if i != j and tr.get(rid[i]) != tr.get(rid[j]):
            nonmatch.append((i, j))

    u = _rate(nonmatch, fields, agree)
    fpr = _rate(fp, fields, agree)
    tpr = _rate(tp, fields, agree)

    lines.append(f"GM emitted {len(emitted)} merge-pairs: {len(tp)} true / {len(fp)} "
                 f"FALSE (sampled). fields present: {fields}\n")
    lines.append("| field | u (non-match) | FALSE-merge agree | TRUE-merge agree | discriminative? |")
    lines.append("| --- | --- | --- | --- | --- |")
    for c in fields:
        # discriminative if TRUE merges agree much more than FALSE merges;
        # non-discriminative (shared by both) if FP agree ~ TP agree.
        gap = tpr[c] - fpr[c]
        tag = ("weak (FP≈TP agree)" if abs(gap) < 0.15 and fpr[c] > 0.4
               else "over-weighted?" if fpr[c] > 0.6 and gap < 0 else "ok")
        lines.append(f"| {c} | {u[c]:.3f} | {fpr[c]:.3f} | {tpr[c]:.3f} | {tag} |")
    lines.append("")
    # Top values the FALSE merges agree on (for the most-agreed field).
    if fp and fields:
        top_field = max(fields, key=lambda c: fpr[c])
        vals = colidx[top_field]
        agreed = Counter(_norm(vals[a]) for a, b in fp if agree[top_field](a, b))
        lines.append(f"### FALSE merges most agree on `{top_field}` ({fpr[top_field]*100:.0f}%); top values")
        lines.append("")
        lines.append(", ".join(f"{v}({k})" for v, k in agreed.most_common(8) if v))
    lines.append("")

    # EM-weight inspection: does FS learn to down-weight year (m~=u) / venue
    # (m<u -> agreement penalized)? match_weights[field][level] = log2(m/u).
    lines.append("### EM-learned match weights (log2(m/u) per level; last = top/agree)")
    lines.append("")
    try:
        from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
        from goldenmatch.core.blocker import build_blocks
        from goldenmatch.core.probabilistic import load_or_train_em
        cfg = auto_configure_probabilistic_df(records)
        mk = cfg.matchkeys[0]
        blk = getattr(cfg, "blocking", None)
        blocks = build_blocks(records, blk) if blk is not None else None
        bfields = []
        for k in (getattr(blk, "keys", None) or []):
            bfields.extend(getattr(k, "fields", []) or [])
        em = load_or_train_em(records, mk, blocks=blocks, blocking_fields=bfields)
        lines.append("| field | scorer | levels | match_weights (per level) | top-level (agree) weight |")
        lines.append("| --- | --- | --- | --- | --- |")
        for f in mk.fields:
            w = (em.match_weights or {}).get(f.field)
            if w is None:
                continue
            top = w[-1] if w else None
            wl = "[" + ", ".join(f"{x:+.2f}" for x in w) + "]"
            lines.append(f"| {f.field} | {f.scorer} | {f.levels} | {wl} | "
                         f"{top:+.2f} | " if top is not None else
                         f"| {f.field} | {f.scorer} | {f.levels} | {wl} | - |")
        lines.append("")
        lines.append("_A field whose TOP-level (agreement) weight is ~0 or NEGATIVE is one FS "
                     "already treats as non-evidence / anti-evidence of a match. If year/venue "
                     "still have large POSITIVE agreement weights despite being non-discriminative/"
                     "inverted, EM is mis-estimating them -> the lever is fixing EM (clamp agreement "
                     "weight to <=0 where m<=u). If they're already <=0, the FP is driven elsewhere."
                     )
    except Exception as e:
        lines.append(f"_EM inspection failed ({type(e).__name__}: {e})_")

    _emit("\n".join(lines) + "\n")
    return 0


def _emit(md):
    print(md)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(md)


if __name__ == "__main__":
    raise SystemExit(main())
