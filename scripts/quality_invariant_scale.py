#!/usr/bin/env python3
"""#510: quality-invariant scale validation harness.

The thesis: match quality and clustering behaviour are invariant across scale.
Existing scale benches measure throughput (wall, RSS) but not quality, so the
"validated" rows in `scale-envelope.md` are throughput claims, not F1 claims.
This harness fills the quality side: at each rung it generates a deterministic
synthetic person dataset (replicating the Phase 5 generator's logic, but keeping
the cluster id so we have ground truth), runs zero-config dedupe, and reports
Pairwise F1, B-cubed F1, Cluster F1, plus wall, peak RSS, cluster counts, and
the committed config the controller chose.

Per-rung output (JSON), so future rungs slot in:
    { "rows": N, "clusters": N/5, "wall_s": ..., "rss_mb_peak": ...,
      "pairwise": {"f1": ..., "p": ..., "r": ..., "tp": ..., "fp": ..., "fn": ...},
      "b_cubed":  {"f1": ..., "p": ..., "r": ...},
      "cluster":  {"f1": ..., "p": ..., "r": ..., "exact": N},
      "predicted_clusters": ..., "multi_member": ..., "committed_config": {...} }

Run a single rung locally:
    python scripts/quality_invariant_scale.py --rows 10000 --out out.json

Run the ladder via the bench-gen Railway service (large rungs): wire a Railway
one-shot job modelled on `Dockerfile.embprov` that invokes this script per N.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

if sys.platform != "win32":
    import resource as _resource
else:
    _resource = None  # Windows: fall back to tracemalloc in _peak_rss_mb

import numpy as np
import polars as pl

ROWS_PER_CLUSTER = 5
TYPO_RATE = 0.10

# Width (in syllables / decimal digits) of the collision-free identity encoding.
# A bijection cid -> string of this width keeps per-cluster identity distinctness
# INVARIANT with N: 24**8 ~= 1.1e11 distinct names and 10**8 = 100M distinct
# street numbers, both >> any rung's cluster count. The old md5-hash names
# (24**5 = 8M) and [1,9999]x8 addresses birthday-saturated past ~80K-200K
# clusters, which made the dataset non-separable at scale (precision collapse)
# and produced giant collision blocks (-> OOM at 25M). See _bijective_int.
_NAME_WIDTH = 8
_ADDR_DIGITS = 8


_SYL = ["an", "be", "ca", "da", "el", "fi", "ga", "ha", "in", "jo", "ka", "la",
        "ma", "na", "or", "pa", "ri", "sa", "ta", "va", "wo", "xe", "yu", "ze"]
_STREETS = ["main st", "oak ave", "pine rd", "maple dr", "cedar ln",
            "elm st", "washington ave", "park blvd"]
_CITIES = ["springfield", "franklin", "clinton", "georgetown",
           "salem", "fairview", "madison", "bristol"]


@dataclass(frozen=True)
class CorruptionConfig:
    """Corruption knob for the realistic generator. Per-field rates are the
    probability a given row's field is corrupted; `severity` is how many edits
    (transpose / delete / token-drop) are applied to a corrupted cell -- 1 edit
    barely dents a fuzzy score on a long synthetic name, so `moderate` uses
    several to create real RECALL headroom. `ambiguous_frac` is the fraction of
    clusters made into genuinely-confusable twins (a twin pair shares first+last
    name but keeps distinct address/zip/email), creating PRECISION headroom -- the
    engine must separate them on secondary fields or it over-merges. All of it is
    a deterministic function of (seed, level, cid/row): the per-row corruption
    draw is a fixed-width block and the twin selection is a bijective hash of cid,
    so corruption for row i never depends on n_rows. Every smaller rung is an
    EXACT prefix of every larger one -- the precondition for attributing cross-rung
    F1 deltas to scale rather than data shape."""
    first_name: float = 0.0
    last_name: float = 0.0
    address: float = 0.0
    email: float = 0.0
    severity: int = 1
    ambiguous_frac: float = 0.0


# Stream order is FIXED (spawn index = position here) so each field's child RNG
# is stable regardless of which other fields are corrupted.
_CORRUPT_FIELDS = ("first_name", "last_name", "address", "email")
# Seeds the corruption SeedSequence per level. "light" -> 0 is never actually
# reached (all-zero rates short-circuit the corruption branch in
# _generate_realistic); kept for a complete mapping.
_CORRUPT_LEVEL_INT = {"light": 0, "moderate": 1, "hard": 2}

# `moderate` is TUNED (#510) to land the 1K oracle in the 0.90-0.95 drift band on
# the collision-free fixture. With unique identities the matcher's precision is
# ~1.0 unless the data is genuinely ambiguous, so `moderate` combines multi-edit
# corruption (severity>1 -> real recall headroom; 1 edit on a long synthetic name
# barely moves a fuzzy score) with an ambiguous-twin fraction (-> precision
# headroom). Re-tune AND update the report + the band regression test
# (test_moderate_oracle_f1_in_target_band) if you change these.
CORRUPTION_LEVELS: dict[str, CorruptionConfig] = {
    "light": CorruptionConfig(),  # no extra corruption beyond the 10% a->@ typo
    # TUNED at the 1K oracle (seed 0): pairwise F1 0.9374 (precision 0.910,
    # recall 0.966, B-cubed 0.967, cluster 0.862) -- centered in the 0.90-0.95
    # band with headroom on BOTH precision (the ambiguous twins force over-merge
    # pressure) and recall (severity-2 edits drop match scores). Sweep at 1K:
    #   (.18,.12,.18,.04,sev2,amb.10) -> 0.9374  [chosen]
    #   (.25,.18,.25,.06,sev3,amb.15) -> 0.8770 (p .852/r .904)
    #   (.35,.25,.35,.10,sev3,amb.25) -> 0.8259
    "moderate": CorruptionConfig(first_name=0.18, last_name=0.12, address=0.18,
                                 email=0.04, severity=2, ambiguous_frac=0.10),
    "hard": CorruptionConfig(first_name=0.30, last_name=0.22, address=0.30,
                             email=0.08, severity=3, ambiguous_frac=0.20),
}


def _corrupt_cell(s: str, type_sel: float, pos_sel: float) -> str:
    """One deterministic corruption of a single string from two uniforms in [0,1).

    Three kinds, partitioned by type_sel: adjacent-char transpose, single-char
    delete, whitespace-token drop. NEVER returns an empty string -- a whole-field
    null would collapse many rows to "" and create a cross-cluster mega-block
    (giant blocking group + spurious matches), which is exactly what broke the
    scale audit. Corruption always degrades a value WITHIN its cluster while
    keeping it non-empty and matchable. A kind that can't apply (token-drop on a
    single token, anything on a <2-char string) falls back to delete or a no-op."""
    if len(s) < 2:
        return s
    if type_sel < 1.0 / 3.0:                            # transpose adjacent chars
        i = min(int(pos_sel * (len(s) - 1)), len(s) - 2)
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if type_sel < 2.0 / 3.0:                            # delete one char
        i = min(int(pos_sel * len(s)), len(s) - 1)
        return s[:i] + s[i + 1:]
    # drop one whitespace token; single-token strings fall back to a char delete
    # (never null).
    if " " in s:
        toks = s.split(" ")
        if len(toks) >= 2:
            j = min(int(pos_sel * len(toks)), len(toks) - 1)
            return " ".join(toks[:j] + toks[j + 1:])
    i = min(int(pos_sel * len(s)), len(s) - 1)
    return s[:i] + s[i + 1:]


_MAX_SEVERITY = 4  # upper bound on edits/cell; sizes the fixed per-row draw block


def _apply_field_corruption(values: list[str], rate: float, field_rng,
                            severity: int = 1) -> list[str]:
    """Corrupt a column of strings in place; returns the SAME list (mutated), so
    callers must use the return value. `field_rng` is this field's own numpy
    Generator. Draws a fixed (n, 1 + 2*_MAX_SEVERITY) block per row — col 0 is the
    apply mask, then up to _MAX_SEVERITY (type_sel, pos_sel) pairs — so row i's
    draws sit at fixed flat offsets and the first k rows of an n=k draw equal the
    first k rows of any larger draw (prefix stability). Applies `severity` edits
    to each masked cell (clamped to [1, _MAX_SEVERITY]). Loops only over masked
    rows."""
    n = len(values)
    if rate <= 0.0 or n == 0:
        return values
    sev = max(1, min(int(severity), _MAX_SEVERITY))
    draws = field_rng.random((n, 1 + 2 * _MAX_SEVERITY))
    idx = np.nonzero(draws[:, 0] < rate)[0]
    for k in idx:
        i = int(k)
        s = values[i]
        for e in range(sev):
            s = _corrupt_cell(s, float(draws[i, 1 + 2 * e]), float(draws[i, 2 + 2 * e]))
        values[i] = s
    return values


def _bijective_int(cid: int, mult: int, off: int, modulus: int) -> int:
    """(cid*mult + off) mod modulus -- a BIJECTION over [0, modulus) when `mult`
    is coprime to `modulus`, so distinct cids map to distinct outputs (zero
    collisions) for any cid < modulus, while `mult` scrambles ordering so
    neighbouring cids don't share structure. This is what keeps the synthetic
    identities equally distinct at every N (the scale-invariance precondition)."""
    return (cid * mult + off) % modulus


# Odd multipliers, each coprime to 24 (=2^3*3) and to 10 (=2*5): odd and not
# divisible by 3 or 5. Distinct per field so first/last/address pools are
# uncorrelated. Verified coprimality: all are odd, end in 1/3/7/9, digit sums
# not divisible by 3.
_MULT_FIRST = 2654435761   # Knuth; odd, digitsum 43, ends in 1
_MULT_LAST = 2246822519    # odd, digitsum 41, ends in 9
_MULT_ADDR = 3266489917    # odd, digitsum 58, ends in 7


def _hash_name(salt: str, seed: int, cid: int, width: int = _NAME_WIDTH) -> str:
    """Distinct, low-similarity `width`-syllable name from (salt, seed, cid),
    COLLISION-FREE by construction: a bijection cid -> base-24 digits (24**width
    distinct names). Unlike the old md5 hash, distinctness equals the cluster
    count at every N -- no birthday collisions as clusters grow. Independent
    multipliers/offsets per salt keep the first/last pools uncorrelated."""
    mult = _MULT_FIRST if salt == "F" else _MULT_LAST
    off = (seed * 2654435761 + (0 if salt == "F" else 7)) % (len(_SYL) ** width)
    m = _bijective_int(cid, mult, off, len(_SYL) ** width)
    out = []
    for _ in range(width):
        out.append(_SYL[m % len(_SYL)])
        m //= len(_SYL)
    return "".join(out)


def generate_with_gt(n_rows: int, seed: int = 0, shape: str = "realistic",
                     corruption: str = "light"
                     ) -> tuple[pl.DataFrame, np.ndarray]:
    """Generate a synthetic person dedupe dataset + ground-truth cluster ids.

    shape="phase5"   — the in-process replica of the Phase 5 generator (literal
                       "name_<cid>" / "sur_<cid>" tokens). Throughput-shaped:
                       low cardinality + high inter-cluster token similarity.
    shape="realistic" — 5-syllable hash-derived names + a realistic vocab for
                       address/city/zip/birth_year. Designed to be a fair
                       fixture for measuring pipeline quality across scale (no
                       inter-cluster name similarity, near-distinct identities).

    Both share the 5-rows-per-cluster + 10% typo-on-first_name noise model.

    corruption — one of "light" (default, no extra corruption beyond the 10%
                 a->@ typo), "moderate", or "hard". Applies only to
                 shape="realistic"; ignored (with a warning) for shape="phase5".
                 Oracle cluster ids (cids) are never affected — only displayed
                 field values change.
    """
    if corruption not in CORRUPTION_LEVELS:
        raise ValueError(f"unknown corruption {corruption!r}; expected one of "
                         f"{sorted(CORRUPTION_LEVELS)}")
    if shape == "phase5":
        if corruption != "light":
            print(f"[qis] WARNING: corruption={corruption!r} ignored for shape "
                  f"'phase5' (corruption knob applies to 'realistic' only)", flush=True)
        return _generate_phase5(n_rows, seed)
    if shape == "realistic":
        return _generate_realistic(n_rows, seed, corruption=corruption)
    raise ValueError(f"unknown shape {shape!r}; expected 'phase5' or 'realistic'")


def _generate_phase5(n_rows: int, seed: int = 0) -> tuple[pl.DataFrame, np.ndarray]:
    n_rows = (n_rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER
    n_clusters = n_rows // ROWS_PER_CLUSTER
    rng = np.random.default_rng(seed)
    cids = np.repeat(np.arange(n_clusters, dtype=np.int64), ROWS_PER_CLUSTER)
    typo = rng.random(n_rows) < TYPO_RATE
    df = (
        pl.DataFrame({"__cid__": cids, "__typo__": typo})
        .with_columns(
            first_canon=pl.concat_str([pl.lit("name_"), pl.col("__cid__").cast(pl.Utf8)]),
            last_name=pl.concat_str([pl.lit("sur_"), pl.col("__cid__").cast(pl.Utf8)]),
        )
        .with_columns(
            first_name=pl.when(pl.col("__typo__"))
            .then(pl.col("first_canon").str.replace_all("a", "@", literal=True))
            .otherwise(pl.col("first_canon")),
        )
        .with_columns(
            email=pl.concat_str([pl.col("first_name"), pl.lit("."),
                                 pl.col("last_name"), pl.lit("@example.com")]),
            zip=(pl.col("__cid__") % 100000).cast(pl.Utf8).str.zfill(5),
            id=pl.int_range(0, n_rows, dtype=pl.Int64).cast(pl.Utf8),
        )
        .select("id", "first_name", "last_name", "email", "zip")
    )
    return df, cids


def _generate_realistic(n_rows: int, seed: int = 0, corruption: str = "light"
                        ) -> tuple[pl.DataFrame, np.ndarray]:
    n_rows = (n_rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER
    n_clusters = n_rows // ROWS_PER_CLUSTER

    # Each random field draws from its OWN independent stream (one draw per
    # stream) so the first k values of an N-sized draw equal a k-sized draw:
    # prefix stability. A single shared rng consumed sequentially is NOT
    # prefix-stable — each later field's start state depends on n_clusters, so
    # row i's street/city/year/typo would differ between a 1K and a 100M dataset
    # and the smaller rung would not be an exact prefix of the larger. That
    # prefix property is what lets the ladder attribute cross-rung F1 deltas to
    # scale rather than to data shape (#510).
    def _field_rng(key: int):
        return np.random.default_rng(np.random.SeedSequence([seed, 0xA11CE, key]))

    # Per-cluster canonical fields. Hash-derived names + zip are already a pure
    # function of (seed, cid) so they're prefix-stable as-is; the rng-drawn
    # fields each get a dedicated stream.
    first_canon = [_hash_name("F", seed, c) for c in range(n_clusters)]
    last_canon = [_hash_name("L", seed, c) for c in range(n_clusters)]

    # #510 ambiguity: a deterministic FIXED FRACTION of clusters become confusable
    # "twins" -- a twin pair (c, c^1) shares first+last name but keeps its own
    # address/zip/email, so the matcher must separate them on secondary fields or
    # it over-merges (precision headroom). The fraction is constant at every N
    # (selection is a bijective hash of the pair's lower cid -> uniform in [0,1)),
    # so it's scale-invariant, and both twins stay distinct GT clusters (the
    # oracle/cids are untouched -- only displayed name values collide).
    corr = CORRUPTION_LEVELS[corruption]
    if corr.ambiguous_frac > 0.0:
        for c in range(n_clusters):
            twin = c ^ 1
            if twin >= n_clusters:
                continue
            lo = min(c, twin)
            h = (_bijective_int(lo, _MULT_LAST, 12345, 1_000_000) % 1000) / 1000.0
            if h < corr.ambiguous_frac:
                first_canon[c] = first_canon[lo]
                last_canon[c] = last_canon[lo]

    # street_num is a BIJECTION on cid over [1, 10**_ADDR_DIGITS] (zero address
    # collisions at any N) -- the old random [1,9999] x 8-street space saturated
    # past ~80K clusters. street name stays random (it doesn't need to be unique;
    # the number distinguishes clusters). Both are pure functions of (seed, cid),
    # so still prefix-stable.
    _addr_mod = 10 ** _ADDR_DIGITS
    _addr_off = (seed * _MULT_ADDR + 1) % _addr_mod
    street_num = [_bijective_int(c, _MULT_ADDR, _addr_off, _addr_mod) + 1
                  for c in range(n_clusters)]
    street_idx = _field_rng(1).integers(0, len(_STREETS), n_clusters)
    address_canon = [f"{street_num[c]} {_STREETS[street_idx[c]]}" for c in range(n_clusters)]
    city_canon = [_CITIES[i] for i in _field_rng(2).integers(0, len(_CITIES), n_clusters)]
    zip_canon = [f"{c % 100000:05d}" for c in range(n_clusters)]
    year_canon = _field_rng(3).integers(1940, 2005, n_clusters).astype(str).tolist()

    cids = np.repeat(np.arange(n_clusters, dtype=np.int64), ROWS_PER_CLUSTER)
    typo = _field_rng(4).random(n_rows) < TYPO_RATE

    first_rows = [first_canon[c] for c in cids]
    last_rows = [last_canon[c] for c in cids]
    addr_rows = [address_canon[c] for c in cids]
    city_rows = [city_canon[c] for c in cids]
    zip_rows = [zip_canon[c] for c in cids]
    year_rows = [year_canon[c] for c in cids]

    # Same 'a' -> '@' typo on first_name (matches phase5's noise model so the two
    # shapes only differ in vocab, not noise). Carries into email.
    first_with_typo = [f.replace("a", "@") if t else f for f, t in zip(first_rows, typo)]

    # #510 corruption knob (realistic only). Applied on a SEPARATE RNG derived
    # from (seed, level) so the canonical-field draws above are untouched ->
    # oracle (cids) and the un-corrupted identity are identical across levels.
    # `corr` was bound above (for the ambiguity pass).
    if any(getattr(corr, f) > 0.0 for f in _CORRUPT_FIELDS):
        ss = np.random.SeedSequence([seed, 0xC0FFEE, _CORRUPT_LEVEL_INT[corruption]])
        streams = dict(zip(_CORRUPT_FIELDS, ss.spawn(len(_CORRUPT_FIELDS))))
        first_with_typo = _apply_field_corruption(
            first_with_typo, corr.first_name, np.random.default_rng(streams["first_name"]),
            severity=corr.severity)
        last_rows = _apply_field_corruption(
            last_rows, corr.last_name, np.random.default_rng(streams["last_name"]),
            severity=corr.severity)
        addr_rows = _apply_field_corruption(
            addr_rows, corr.address, np.random.default_rng(streams["address"]),
            severity=corr.severity)
        # Email inherits the corrupted name (realistic), THEN gets its own low-rate
        # pass — kept low so it stays a strong independent recall path.
        email_rows = [f"{f}.{l}@example.com" for f, l in zip(first_with_typo, last_rows)]
        email_rows = _apply_field_corruption(
            email_rows, corr.email, np.random.default_rng(streams["email"]),
            severity=corr.severity)
    else:
        email_rows = [f"{f}.{l}@example.com" for f, l in zip(first_with_typo, last_rows)]

    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(n_rows)],
        "first_name": first_with_typo,
        "last_name": last_rows,
        "address": addr_rows,
        "city": city_rows,
        "zip": zip_rows,
        "birth_year": year_rows,
        "email": email_rows,
    })
    return df, cids


def _pairs_from_clusters(cluster_members: dict[int, list[int]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for members in cluster_members.values():
        m = sorted(members)
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                out.add((m[i], m[j]))
    return out


def score_quality(
    predicted_members: dict[int, list[int]], gt_cids: np.ndarray
) -> dict[str, dict]:
    """O(N) streaming Pairwise + B-cubed + Cluster F1 vs the gt_cids array.

    Never materializes the GT pair set (which is ~16 GB at 100 M rows / 20 M
    clusters of 5). For each predicted cluster, computes its contribution to
    every metric from the gt_cid Counter of its members:

      - pairwise tp  += sum( C(cnt[g], 2) for g in cnt )       per cluster
      - pairwise pred-total += C(|P|, 2)
      - pairwise gt-total = sum( C(gt_sizes[g], 2) )           once, via np.bincount
      - B-cubed bp contribution = sum(cnt[g]^2) / |P|           per cluster
      - B-cubed br contribution = sum(cnt[g]^2 / gt_sizes[g])  per cluster
      - Cluster exact-match counted when |cnt|==1 AND |P|==gt_sizes[only_g]
      - Singletons (rows not in any multi-member cluster) handled with a
        vectorized boolean mask, contributing bp += 1 / br += 1/gt_sizes[gt_c].

    Numbers match the prior set-based implementation exactly on the 1K/10K/100K
    local rungs (validated). Memory at 200 M: ~3 GB peak (gt_sizes_arr +
    in_multi mask + cluster member arrays), vs ~32 GB for the set-based version.
    """
    n_rows = int(len(gt_cids))
    # Per-GT-cluster size (used by B-cubed recall, cluster-F1 exact match, and
    # the GT pair total). bincount needs nonneg ints; gt_cids are nonneg here.
    gt_sizes_arr = np.bincount(gt_cids)
    gt_multi_total = int((gt_sizes_arr > 1).sum())
    gt_pair_total = int(np.sum(gt_sizes_arr * (gt_sizes_arr - 1) // 2))

    in_multi = np.zeros(n_rows, dtype=bool)
    pred_tp = 0
    pred_pair_total = 0
    bp_acc = 0.0
    br_acc = 0.0
    exact_cluster_matches = 0
    pred_multi_total = 0

    for members in predicted_members.values():
        sz = len(members)
        if sz <= 1:
            continue
        pred_multi_total += 1
        arr = np.asarray(members, dtype=np.int64)
        in_multi[arr] = True
        gt_for_members = gt_cids[arr]
        uniq, counts = np.unique(gt_for_members, return_counts=True)
        # Pairwise tp from this cluster
        pred_tp += int(np.sum(counts * (counts - 1) // 2))
        pred_pair_total += sz * (sz - 1) // 2
        # B-cubed contributions
        sq = counts.astype(np.float64) ** 2
        bp_acc += float(np.sum(sq) / sz)
        br_acc += float(np.sum(sq / gt_sizes_arr[uniq]))
        # Cluster exact-match: cluster is purely one gt cluster AND covers all of it
        if uniq.size == 1 and sz == int(gt_sizes_arr[uniq[0]]):
            exact_cluster_matches += 1

    # Singletons: predicted cluster is just {row}, gt cluster has size gt_sizes_arr[gt_c].
    # Each singleton contributes bp += 1/1 and br += 1/gt_sizes[gt_c].
    singleton_mask = ~in_multi
    n_single = int(singleton_mask.sum())
    if n_single:
        gt_single = gt_cids[singleton_mask]
        bp_acc += float(n_single)
        br_acc += float(np.sum(1.0 / gt_sizes_arr[gt_single]))

    # Pairwise
    fp_pairs = pred_pair_total - pred_tp
    fn_pairs = gt_pair_total - pred_tp
    pp = pred_tp / pred_pair_total if pred_pair_total else 0.0
    pr = pred_tp / gt_pair_total if gt_pair_total else 0.0
    pf1 = (2 * pp * pr / (pp + pr)) if (pp + pr) else 0.0
    # B-cubed
    bp = bp_acc / n_rows
    br = br_acc / n_rows
    bf1 = (2 * bp * br / (bp + br)) if (bp + br) else 0.0
    # Cluster
    cfp_cnt = pred_multi_total - exact_cluster_matches
    cfn_cnt = gt_multi_total - exact_cluster_matches
    cp = exact_cluster_matches / pred_multi_total if pred_multi_total else 0.0
    cr = exact_cluster_matches / gt_multi_total if gt_multi_total else 0.0
    cf1 = (2 * cp * cr / (cp + cr)) if (cp + cr) else 0.0
    return {
        "pairwise": {"f1": pf1, "p": pp, "r": pr, "tp": int(pred_tp), "fp": int(fp_pairs), "fn": int(fn_pairs)},
        "b_cubed":  {"f1": bf1, "p": bp, "r": br},
        "cluster":  {"f1": cf1, "p": cp, "r": cr, "exact": exact_cluster_matches,
                     "gt_total": gt_multi_total, "pred_total": pred_multi_total},
    }


def _peak_rss_mb() -> float | None:
    """Best-effort peak RSS in MB. Linux: ru_maxrss is KB; macOS: bytes; Windows: tracemalloc fallback."""
    if sys.platform == "win32":
        try:
            cur, peak = tracemalloc.get_traced_memory()
            return peak / 1024 / 1024
        except Exception:
            return None
    try:
        ru = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        return ru / 1024 if sys.platform != "darwin" else ru / 1024 / 1024
    except Exception:
        return None


def _golden_hash(golden) -> str | None:
    """sha256 of the golden frame sorted by all columns. A per-run CONTENT
    fingerprint (kept in the artifact for the report), NOT a determinism witness:
    golden field-survivorship breaks value ties by input row order, which is not
    stably sorted run-to-run, so this hash can legitimately differ across reruns
    on heavily-corrupted text fields even when the cluster PARTITION is identical.
    Determinism is asserted on `clusters_signature` + golden SHAPE instead; the
    survivorship-ordering gap is tracked as goldenmatch issue #870. See
    docs/quality-invariant-scale.md (Methodology)."""
    if golden is None:
        return None
    g = golden.sort(by=golden.columns)
    return hashlib.sha256(g.write_csv().encode("utf-8")).hexdigest()


def _golden_shape(golden) -> list[int] | None:
    """(height, width) of the golden frame, or None. Deterministic given the
    cluster partition (which IS reproducible), unlike the survivorship VALUES."""
    if golden is None:
        return None
    return [int(golden.height), int(golden.width)]


def _clusters_signature(predicted_members: dict[int, list[int]]) -> str:
    """sha256 over the sorted set of sorted member tuples — label-independent
    cluster-membership identity (cluster_id values can differ; the partition
    can't)."""
    canon = sorted(tuple(sorted(int(m) for m in members))
                   for members in predicted_members.values())
    return hashlib.sha256(repr(canon).encode("utf-8")).hexdigest()


# The FROZEN config (#510 methodology): the published quality ladder builds the
# auto-config ONCE (at the 1K oracle, realistic+moderate) and applies that SAME
# config to every rung, so cross-rung F1 deltas isolate the ENGINE's
# scale-invariance from auto-config drift -- and it runs fast (auto-config on the
# ambiguous data is a ~50-150s controller search per call; a pre-built config
# skips it). The committed artifact is checked in next to this script. Auto-config
# STABILITY across scale is reported separately (see autoconfig_stability()).
_FROZEN_CONFIG_PATH = Path(__file__).resolve().parent / "qis_realistic_frozen_config.json"


def load_frozen_config():
    """Load the committed frozen GoldenMatchConfig (the realistic-shape ladder
    config). Cheap: just JSON deserialization, no controller search."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    return GoldenMatchConfig.model_validate_json(
        _FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))


def build_frozen_config(seed: int = 0, corruption: str = "moderate", n_rows: int = 1000):
    """Rebuild the frozen config from a fresh auto-config run at the oracle and
    write it to ``_FROZEN_CONFIG_PATH``. Run this ONLY when the fixture or the
    moderate corruption level changes (the committed config is derived from
    seed=0 / realistic / moderate at 1K). Mirrors run_rung's rerank/NE strip so
    the saved config is exactly what the ladder applies."""
    import goldenmatch
    df, _gt = generate_with_gt(n_rows, seed=seed, shape="realistic", corruption=corruption)
    cfg = goldenmatch.auto_configure_df(
        df, confidence_required=False, allow_red_config=True, _skip_finalize=True)
    for mk in (cfg.matchkeys or []):  # type: ignore[attr-defined]
        if getattr(mk, "type", None) == "weighted" and getattr(mk, "rerank", False):
            mk.rerank = False  # type: ignore[attr-defined]
        if getattr(mk, "type", None) == "exact" and getattr(mk, "negative_evidence", None):
            mk.negative_evidence = []  # type: ignore[attr-defined]
    cfg.backend = None  # backend is chosen per-rung at apply time
    _FROZEN_CONFIG_PATH.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
    return cfg


def run_rung(n_rows: int, seed: int = 0, shape: str = "realistic",
             backend: str | None = None, corruption: str = "light",
             frozen: bool = False) -> dict:
    import goldenmatch
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if sys.platform == "win32":
        tracemalloc.start()

    t0 = time.time()
    df, gt = generate_with_gt(n_rows, seed=seed, shape=shape, corruption=corruption)
    t_gen = time.time() - t0

    # Backend handling: zero-config (planner picks) when --backend is omitted;
    # otherwise pre-build the auto-config and force the backend (the v3 planner
    # honors a user override). At Railway-scale (10M+) the planner can land on
    # `polars` if it can't detect enough RAM, which OOMs; --backend duckdb
    # is the safest fallback (out-of-core) and bucket is the fastest when the
    # container has 32+ GB.
    #
    # `bench_capture()` pushes a BenchmarkRecorder onto goldenmatch's stage
    # ContextVar. Every `with stage(name)` in core/pipeline.py records its
    # wall + process-lifetime peak RSS (KB) at exit. Diffing consecutive
    # stage_peak_rss_kb entries (insertion-ordered) gives the per-stage
    # contribution to the peak — the input we need to pick the right RSS
    # optimization target for #510. See PR #548.
    from goldenmatch.core.bench import bench_capture, stage
    bench_dict: dict = {}
    t1 = time.time()
    with bench_capture() as bench_rec:
        # Top-level phase markers: bracket auto_configure_df and dedupe_df
        # separately so the stage_peak_rss_kb dict carries explicit
        # qis_autoconfig / qis_dedupe rows. Without these, the controller's
        # internal sample-iteration stages (compute_matchkeys, combined_lf_collect,
        # fuzzy_*) are interleaved with the same-named stages from the full-df
        # pipeline run later, so attribution between "controller used X GB" and
        # "dedupe used Y GB" is invisible in last-write-wins dict semantics.
        if frozen:
            # FROZEN MODE (#510 published ladder): apply the pre-built committed
            # config -- no per-rung auto-config search, so this is just the
            # engine (score -> cluster -> golden) on a fixed config. Isolates
            # engine scale-invariance and runs fast at every N.
            with stage("qis_dedupe"):
                cfg = load_frozen_config()
                if backend:
                    cfg.backend = backend  # type: ignore[attr-defined]
                result = goldenmatch.dedupe_df(df, config=cfg)
        elif backend:
            # confidence_required=False because passing --backend explicitly
            # is "measurement mode" -- accept whatever config the controller
            # commits even if RED. Zero-config path still keeps the guard.
            #
            # _skip_finalize=True ALSO measurement mode: 1M-v6 attribution
            # showed the controller's _finalize step (line 1141 of
            # autoconfig_controller.py) runs the FULL pipeline on the FULL df
            # to compute a verification profile -- that's the 9.49 GB RSS
            # allocator AND a 2x wall amplifier (the user-facing dedupe_df
            # call then reruns the same pipeline). For RSS measurement we
            # want ONE full-df pipeline run, not two; skip_finalize gives
            # that. Cost: history.full_vs_sample_drift is None (caller can't
            # detect sample-vs-full divergence). For production users this
            # matters; for measurement it doesn't.
            with stage("qis_autoconfig"):
                # allow_red_config=True is the post-#715 escape hatch (NOT
                # confidence_required=False, which no longer suffices >=100K):
                # the corrupted realistic shape legitimately commits a RED config
                # at scale because the scoring sub-profile's confidence heuristic
                # dislikes corruption-lowered match scores. The config still
                # produces ~0.93 F1 -- RED is a CONFIDENCE flag, not actual
                # quality -- and measuring that config's quality invariance
                # across scale is exactly the #510 test. Without this, rungs
                # >=100K raise ControllerNotConfidentError. committed_config
                # captures the RED health so the report can state it honestly.
                cfg = goldenmatch.auto_configure_df(
                    df,
                    confidence_required=False,
                    allow_red_config=True,
                    _skip_finalize=True,
                )
                # CLAUDE.md harness pattern + #510 diagnosis (10M-v11):
                # auto_configure_df sets mk.rerank=True on weighted matchkeys
                # with 3+ fields (autoconfig.py:2176). The cross-encoder rerank
                # would normally be cleared by autoconfig_verify in offline
                # mode (autoconfig_verify.py:755), but the harness calls
                # auto_configure_df + dedupe_df(config=cfg) directly --
                # autoconfig_verify never runs, mk.rerank stays True,
                # score_buckets._resolve_fast_path declines on line 138, and
                # the workload falls onto slow find_fuzzy_matches (1370s of
                # bucket_score wall at 10M). Stripping here mirrors what the
                # verify step would have done in a network-isolated context
                # AND matches CLAUDE.md's bench pattern. F1 has been locked
                # at 0.9886 across v6-v11 (all slow path); rerank wasn't
                # firing anyway, so stripping is a pure perf unlock.
                for mk in (cfg.matchkeys or []):  # type: ignore[attr-defined]
                    if getattr(mk, "type", None) == "weighted" and getattr(mk, "rerank", False):
                        mk.rerank = False  # type: ignore[attr-defined]
                        print(
                            f"[qis] stripped mk.rerank from weighted matchkey "
                            f"{getattr(mk, 'name', '?')!r} (n_fields={len(mk.fields)})",
                            flush=True,
                        )
                    # Strip NE from EXACT matchkeys too. Per QIS 10M-v11 trace:
                    # the auto-configured exact matchkey has NE with scorer
                    # 'ensemble' on the 'id' field. score_field doesn't
                    # implement 'ensemble' so it's silently skipped at runtime
                    # via PR #546's _NE_BROKEN cache (penalty=0, final_score=1.0
                    # >= threshold -> every pair passes through unchanged). But
                    # the slow _apply_negative_evidence_to_exact_pairs loop
                    # still iterates all 36.5M pairs doing zero useful work,
                    # AND _resolve_fast_path on the exact_matching numpy path
                    # (PR #557) declines because mk.negative_evidence is truthy.
                    # Stripping here unblocks the numpy fast path without
                    # changing accuracy (broken NE was a no-op anyway).
                    if getattr(mk, "type", None) == "exact" and getattr(mk, "negative_evidence", None):
                        n_ne = len(mk.negative_evidence)
                        mk.negative_evidence = []  # type: ignore[attr-defined]
                        print(
                            f"[qis] stripped {n_ne} NE entries from exact matchkey "
                            f"{getattr(mk, 'name', '?')!r} (broken at runtime via _NE_BROKEN)",
                            flush=True,
                        )
                cfg.backend = backend  # type: ignore[attr-defined]
            with stage("qis_dedupe"):
                result = goldenmatch.dedupe_df(df, config=cfg)
        else:
            # No separate qis_autoconfig stage on this path -- the zero-config
            # `dedupe_df(df)` call runs auto-config + dedupe as one unit
            # internally, and splitting them would change the call shape vs
            # what real users hit. Top-level qis_dedupe still brackets the lot.
            with stage("qis_dedupe"):
                result = goldenmatch.dedupe_df(df)
    t_dedupe = time.time() - t1
    try:
        bench_dict = bench_rec.to_dict()
    except Exception as e:
        bench_dict = {"_capture_error": repr(e)[:120]}

    predicted: dict[int, list[int]] = {}
    for cid, c in (result.clusters or {}).items():
        members = c.get("members") or []
        if len(members) > 1:
            predicted[int(cid)] = list(members)

    metrics = score_quality(predicted, gt)

    golden_hash = _golden_hash(getattr(result, "golden", None))
    golden_shape = _golden_shape(getattr(result, "golden", None))
    clusters_sig = _clusters_signature(predicted)

    multi = sum(1 for v in predicted.values() if len(v) > 1)
    committed_cfg: dict = {}
    # Frozen mode has no per-rung controller run; record a marker and skip the
    # controller-telemetry capture entirely (it would read a stale ContextVar).
    if frozen:
        committed_cfg = {"mode": "frozen", "source": _FROZEN_CONFIG_PATH.name}
    try:
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
        state = None if frozen else _LAST_CONTROLLER_RUN.get()
        if state is not None:
            profile, history = state
            # Per-iteration controller telemetry (v23 expansion).
            # v22 measured POLICY_SATISFIED=6 / YELLOW -- heuristic rules
            # saturated. To tell whether the 14 DEFAULT_RULES have a gap on
            # QIS-realistic shape vs the YELLOW being irreducible, we need
            # per-iteration visibility. iter_log captures rule_name +
            # rationale + per-sub-profile health so we can see which
            # dimension is bottlenecking and which rules fired (or didn't).
            iter_log = []
            for ent in history.entries:
                p = ent.profile
                n_rows = getattr(p.data, "n_rows", 0) or 0
                sub_health: dict[str, str] = {}
                for name, getter in (
                    ("data", lambda: p.data.health()),
                    ("domain", lambda: p.domain.health()),
                    ("matchkey", lambda: p.matchkey.health()),
                    ("blocking", lambda: p.blocking.health(n_rows=n_rows)),
                    ("scoring", lambda: p.scoring.health()),
                    ("cluster", lambda: p.cluster.health(n_rows=n_rows)),
                ):
                    try:
                        v = getter()
                        sub_health[name] = v.value if hasattr(v, "value") else str(v)
                    except Exception as _exc:  # noqa: BLE001
                        sub_health[name] = f"<{type(_exc).__name__}>"
                _eh = ent.profile.health()
                entry_dict: dict = {
                    "iteration": ent.iteration,
                    "health": _eh.value if hasattr(_eh, "value") else str(_eh),
                    "sub_health": sub_health,
                    "wall_ms": ent.wall_clock_ms,
                }
                if ent.decision is not None:
                    entry_dict["decision"] = {
                        "rule_name": ent.decision.rule_name,
                        "rationale": ent.decision.rationale[:300],
                        "config_diff_keys": sorted(
                            list((ent.decision.config_diff or {}).keys())
                        ),
                        "expand_sample": ent.decision.expand_sample,
                    }
                if ent.error is not None:
                    entry_dict["error"] = {
                        "exception_type": ent.error.exception_type,
                        "traceback_summary": ent.error.traceback_summary[:300],
                    }
                iter_log.append(entry_dict)
            committed_cfg = {
                "health": profile.health().value,
                "stop_reason": str(history.stop_reason),
                "iterations": history.iteration,
                "decisions": [d.rule_name for d in (history.decisions or [])],
                "iter_log": iter_log,
                "is_oscillating": bool(history.is_oscillating()),
                "full_vs_sample_drift": history.full_vs_sample_drift,
            }
    except Exception as e:
        if not frozen:  # don't clobber the frozen marker on an import hiccup
            committed_cfg = {"_capture_error": repr(e)[:120]}

    # Native acceleration status: surface whether goldenmatch._native is loaded
    # and which env gate we're under, so the bench artifact carries an explicit
    # native-on/off witness. Prior runs silently fell back to pure-Python; this
    # ends that ambiguity.
    try:
        from goldenmatch.core._native_loader import _GATED_ON, native_available
        native_info = {
            "available": bool(native_available()),
            "env_gate": os.environ.get("GOLDENMATCH_NATIVE", "auto"),
            "gated_on_components": sorted(_GATED_ON),
        }
    except Exception as e:
        native_info = {"_capture_error": repr(e)[:120]}

    return {
        "rows": len(df),
        "corruption": corruption,
        "config_mode": "frozen" if frozen else "autoconfig",
        "clusters_gt": int(len(set(gt.tolist()))),
        "wall_s": {"generate": round(t_gen, 2), "dedupe": round(t_dedupe, 2), "total": round(t_gen + t_dedupe, 2)},
        "rss_mb_peak": _peak_rss_mb(),
        **metrics,
        "predicted_clusters": len(predicted) + (len(df) - sum(len(v) for v in predicted.values())),
        "multi_member_clusters": multi,
        "committed_config": committed_cfg,
        "golden_hash": golden_hash,
        "golden_shape": golden_shape,
        "clusters_signature": clusters_sig,
        "bench": bench_dict,
        "native": native_info,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--shape", choices=("realistic", "phase5"), default="realistic",
                    help="realistic = varied syllable vocab (default, the fair fixture); "
                         "phase5 = the in-process Phase-5 replica (throughput-shaped, "
                         "pathological for ER quality)")
    ap.add_argument("--backend", default=None,
                    choices=(None, "polars", "bucket", "chunked", "duckdb", "ray"),
                    help="override the v3 planner's backend pick. Recommended ladder: "
                         "polars <500K, bucket 500K-25M (>=32GB RAM), duckdb 25M-100M "
                         "(out-of-core, no OOM on smaller boxes), ray 50M+ "
                         "(distributed; needs the ray extra installed).")
    ap.add_argument("--corruption", choices=("light", "moderate", "hard"),
                    default="light",
                    help="realistic-shape corruption level. light = today's baseline "
                         "(10%% a->@ typo only); moderate ~ F1 0.90-0.95 (drift-sensitive, "
                         "the published ladder default); hard = stress. Ignored for --shape phase5.")
    ap.add_argument("--frozen", action="store_true",
                    help="apply the committed frozen config (the #510 published-ladder "
                         "default methodology) instead of running per-rung auto-config. "
                         "Isolates engine scale-invariance and runs fast at every N.")
    ap.add_argument("--rebuild-frozen-config", action="store_true",
                    help="rebuild + save the frozen config from a fresh 1K auto-config "
                         "run (do this only when the fixture/moderate level changes), "
                         "then exit.")
    ap.add_argument("--out", type=Path, default=None, help="write per-rung JSON here")
    args = ap.parse_args(argv)

    if args.rebuild_frozen_config:
        build_frozen_config(seed=args.seed, corruption="moderate")
        print(f"rebuilt frozen config -> {_FROZEN_CONFIG_PATH}")
        return 0

    res = run_rung(args.rows, seed=args.seed, shape=args.shape, backend=args.backend,
                   corruption=args.corruption, frozen=args.frozen)
    # shape/backend are not in run_rung's return dict, so set them here;
    # "corruption" is already in the dict (run_rung received it) -- don't
    # re-write it, that would silently shadow a future rename of the key.
    res["shape"] = args.shape
    res["backend"] = args.backend or "auto"
    print(json.dumps(res, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
