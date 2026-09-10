"""A/B-a-lever gate — the FS/Lever-Enablement regression gate (Phase 0).

Measures the F1 / precision / recall delta of a single auto-config lever across
the locally-available ``bench_er_headtohead`` panel datasets, and reports a
per-dataset PASS/FAIL: a lever may only flip its default when NO panel dataset
regresses beyond ``--tol``.

The lever is toggled via an environment variable read lazily by the engine
(e.g. ``GOLDENMATCH_FS_DOMAIN_COMPARATORS``), so both arms run in one process by
flipping ``os.environ`` between runs. Zero-config: each dataset goes through
``auto_configure_probabilistic_df`` + ``dedupe_df`` (the real default FS path),
scored against committed ground truth via ``evaluate_clusters``.

Usage:
    python -m scripts.bench_er_headtohead.ab_lever \
        --env GOLDENMATCH_FS_DOMAIN_COMPARATORS --off 0 --on 1
    # optional: --datasets historical_50k,febrl3  --tol 0.005

Design spec: docs/superpowers/specs/2026-08-01-fs-lever-enablement-design.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# The real F1 panel (anchor shapes with no positive ground-truth pairs are
# excluded — they gate structure, not F1). Skipped automatically when a
# dataset's optional dep / vendored file is absent (loader returns None).
#
# The five real datasets are all 0.50-OPTIMAL (FS at ceiling — every cheap lever
# declined on them). The two synthetic over-merge shapes cover the failure mode
# the real panel structurally lacks: household_hardneg (MODERATE surname
# over-merge) + cotenant_hardneg (SEVERE address over-merge), where the fixed
# 0.50 cutoff over-merges and a lever (the threshold-refit loop) can actually
# move F1. Including them means a lever A/B is measured on both the at-ceiling
# regime (must-not-regress) AND the has-headroom regime (can-it-win).
_PANEL = [
    "person",
    "febrl3",
    "ncvr_synthetic",
    "dblp_acm",
    "historical_50k",
    "household_hardneg",
    "cotenant_hardneg",
]


def _load(name: str):
    """Load a panel dataset.

    Raises ``KeyError`` on an UNKNOWN dataset name (a typo / config error the
    gate must NOT silently swallow into a smaller panel). Returns ``None`` only
    when a KNOWN loader reports the dataset is unavailable (optional dep or
    vendored file absent -> that dataset is skipped, surfaced by the caller). A
    real loader exception propagates (a genuine bug, not an expected skip)."""
    from scripts.autoconfig_quality import datasets as D

    fn = getattr(D, f"_{name}", None)
    if fn is None:
        raise KeyError(
            f"unknown panel dataset {name!r} "
            f"(no loader _{name} in scripts.autoconfig_quality.datasets)"
        )
    return fn()  # loader returns None when its dep/vendored file is absent -> skip


def _with_extra_passes(cfg, df, specs: list[str]):
    """Append blocking passes to an auto-configured config.

    Specs are ``field:t1,t2``, or a compound ``field:t1,t2+field:t3`` with
    per-field transforms. Measurement-only, after auto-config: ``--extra-passes``
    applies them to BOTH arms (a held constant), ``--on-extra-passes`` to the ON
    arm only (the pass IS the variable). Auto-config's own guards
    (``_is_scale_safe``) are deliberately bypassed -- that is the point of forcing
    a pass in. A spec naming a column the dataset lacks is skipped and reported,
    not silently dropped.
    """
    if not specs or cfg.blocking is None:
        return cfg, []
    from scripts.bench_er_headtohead.measure_surname_passes import pass_from_spec

    keys = list(cfg.blocking.resolved_keys())
    added = []
    for spec in specs:
        key = pass_from_spec(spec)
        missing = [f for f in key.fields if f not in df.columns]
        if missing:
            print(f"  [extra pass] skipped {spec!r}: no column(s) {missing}", file=sys.stderr)
            continue
        keys.append(key)
        added.append(spec)
    if not added:
        return cfg, []
    blocking = cfg.blocking.model_copy(update={"strategy": "multi_pass", "passes": keys})
    return cfg.model_copy(update={"blocking": blocking}), added


def _f1(name: str, extra_passes: list[str] | None = None) -> dict | None:
    """Run zero-config FS dedupe on one dataset, return the F1 summary (or None
    to skip). Reads the lever from the CURRENT os.environ."""
    loaded = _load(name)
    if loaded is None:
        return None
    df, gt = loaded
    if not gt:  # anchor shape, no positive pairs -> not an F1 dataset
        return None
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.evaluate import evaluate_clusters

    cfg = auto_configure_probabilistic_df(df)
    cfg, added = _with_extra_passes(cfg, df, extra_passes or [])
    if added:
        print(f"  [extra pass] {name}: added {added}", file=sys.stderr)
    t0 = time.perf_counter()
    res = goldenmatch.dedupe_df(df, config=cfg)
    wall = time.perf_counter() - t0
    ev = evaluate_clusters(res.clusters, gt).summary()
    pairs, digest = _partition_fingerprint(res.clusters)
    return {
        "f1": ev["f1"],
        "p": ev["precision"],
        "r": ev["recall"],
        "wall": wall,
        "pairs": pairs,
        "digest": digest,
    }


def _partition_fingerprint(clusters) -> tuple[int, str]:
    """Predicted-pair count + a stable digest of the multi-member partition.

    F1/P/R are the headline, and a lever that leaves them flat to four decimals
    reads as "did nothing" -- which is TWO different findings wearing the same
    face. Either the lever never reached scoring, or it reached it and the
    clustering absorbed the change (an edge alters connected components only
    when it was a BRIDGE; cutting a redundant one inside a dense cluster is
    real work with no visible effect).

    The digest separates them without a debugger: identical means the partition
    is byte-identical and the lever is INERT; different-with-flat-F1 means it is
    ACTIVE and absorbed. Built from the same expansion ``evaluate_clusters``
    scores, so it measures the object the metric is computed from, not a proxy.
    """
    import hashlib
    from itertools import combinations

    groups = []
    pairs = 0
    for info in clusters.values():
        members = info.get("members", [])
        if len(members) < 2:
            continue
        ms = sorted(map(str, members))
        pairs += sum(1 for _ in combinations(ms, 2))
        groups.append(",".join(ms))
    h = hashlib.sha256("|".join(sorted(groups)).encode("utf-8")).hexdigest()
    return pairs, h[:12]


def _self_test() -> int:
    """Prove the partition fingerprint can tell two partitions apart.

    A digest that always reports "same" would silently convert every result into
    "the lever is inert" -- the exact false negative this column exists to
    prevent. So the instrument asserts a KNOWN-POSITIVE before it is trusted to
    report an absence, and the gate runs this before it measures anything.
    """

    def cl(*groups):
        return {i: {"members": list(g)} for i, g in enumerate(groups)}

    checks = []
    # Same partition -> same digest, and singletons are ignored by both.
    a_pairs, a = _partition_fingerprint(cl([1, 2, 3], [4, 5], [6]))
    b_pairs, b = _partition_fingerprint(cl([4, 5], [3, 2, 1], [7]))
    checks.append(("stable under cluster/member order", a == b))
    checks.append(("pair count = sum C(n,2)", a_pairs == 4 and b_pairs == 4))
    # One edge cut -> different digest. This is the known-positive.
    c_pairs, c = _partition_fingerprint(cl([1, 2], [3], [4, 5]))
    checks.append(("KNOWN-POSITIVE: split cluster changes digest", a != c))
    checks.append(("split cluster lowers pair count", c_pairs < a_pairs))
    # Empty partition must not collide with a populated one.
    _, d = _partition_fingerprint(cl([1]))
    checks.append(("empty partition distinct", d != a))

    ok = True
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print("partition fingerprint self-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B one auto-config lever across the F1 panel.")
    ap.add_argument("--env", required=True, help="env var toggling the lever")
    ap.add_argument("--off", default="0", help="OFF value (baseline)")
    ap.add_argument("--on", default="1", help="ON value (candidate)")
    ap.add_argument("--datasets", default=",".join(_PANEL), help="comma list")
    ap.add_argument(
        "--tol", type=float, default=0.005, help="max allowed per-dataset F1 regression before FAIL"
    )
    ap.add_argument(
        "--on-extra-passes",
        default="",
        help='blocking passes added to the ON arm only, ";"-separated specs -- A/B a pass '
        "itself (pair with a lever env nothing reads, e.g. --env GOLDENMATCH_UNUSED)",
    )
    ap.add_argument(
        "--extra-passes",
        default="",
        help='blocking passes added to BOTH arms after auto-config, ";"-separated '
        '"field:t1,t2" specs (e.g. "surname:lowercase,soundex")',
    )
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="verify the partition fingerprint distinguishes partitions, then exit",
    )
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    # Isolate the measurement from cross-run auto-config memory.
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    extra_passes = [s.strip() for s in args.extra_passes.split(";") if s.strip()]
    on_extra_passes = [s.strip() for s in args.on_extra_passes.split(";") if s.strip()]
    if on_extra_passes:
        print(f"extra blocking passes on the ON arm only: {on_extra_passes}", file=sys.stderr)
    if extra_passes:
        print(f"extra blocking passes on BOTH arms: {extra_passes}", file=sys.stderr)

    rows: list[tuple[str, dict, dict]] = []
    skipped: list[str] = []
    for name in datasets:
        # KeyError (unknown name) intentionally propagates -- a typo must not
        # silently shrink the panel. A None result = a known-unavailable dataset.
        os.environ[args.env] = args.off
        off = _f1(name, extra_passes)
        if off is None:
            skipped.append(name)
            continue
        os.environ[args.env] = args.on
        on = _f1(name, extra_passes + on_extra_passes)
        assert on is not None, f"{name}: measurable OFF but unmeasurable ON"
        rows.append((name, off, on))

    if skipped:
        print(
            f"[skipped] {len(skipped)} unavailable dataset(s): {', '.join(skipped)}",
            file=sys.stderr,
        )
    # A regression gate that measured NOTHING must FAIL, never PASS -- an empty
    # panel is a broken environment, not a clean bill of health.
    if not rows:
        print(
            f"\nGATE: FAIL — 0 datasets measured (requested {len(datasets)}, "
            f"all unavailable). A gate that measures nothing cannot PASS."
        )
        return 1

    print(f"\nA/B lever: {args.env}  (OFF={args.off}  ON={args.on})  tol={args.tol}")
    print(
        f"{'dataset':18s} {'F1 off':>8s} {'F1 on':>8s} {'dF1':>8s} "
        f"{'P off':>7s} {'P on':>7s} {'R off':>7s} {'R on':>7s} "
        f"{'pairs off':>10s} {'pairs on':>9s} {'partition':>10s}  verdict"
    )
    worst = 0.0
    any_regress = False
    moved = False
    for name, off, on in rows:
        d = on["f1"] - off["f1"]
        worst = min(worst, d)
        regress = d < -args.tol
        any_regress = any_regress or regress
        verdict = "REGRESS" if regress else ("win" if d > args.tol else "flat")
        part = "same" if off["digest"] == on["digest"] else "DIFFERS"
        moved = moved or part == "DIFFERS"
        print(
            f"{name:18s} {off['f1']:8.4f} {on['f1']:8.4f} {d:+8.4f} "
            f"{off['p']:7.3f} {on['p']:7.3f} {off['r']:7.3f} {on['r']:7.3f} "
            f"{off['pairs']:10d} {on['pairs']:9d} {part:>10s}  {verdict}"
        )

    print(
        f"\nGATE: {'FAIL' if any_regress else 'PASS'} "
        f"(worst dF1 {worst:+.4f}, tol {args.tol}); {len(rows)} datasets measured"
    )
    if moved:
        print(
            "PARTITION: differs on at least one dataset -- the lever IS "
            "active, so a flat dF1 means the clustering ABSORBED it."
        )
    else:
        # Not a failure: a default-OFF lever SHOULD be byte-identical. It is
        # said out loud because "flat dF1" and "changed nothing at all" are
        # different claims, and only this one licenses the second.
        print(
            "PARTITION: identical on every dataset -- the lever altered no "
            "cluster anywhere, so a flat dF1 says INERT, not neutral."
        )
    return 1 if any_regress else 0


if __name__ == "__main__":
    raise SystemExit(main())
