#!/usr/bin/env python
"""Phase 3c bench: Fellegi-Sunter dedupe at scale on the bucket backend.

Validates that the Phase 3a FS-on-bucket path holds at 5M+ rows: measures wall
+ peak RSS, and F1 against KNOWN injected duplicate pairs (so accuracy is
checked, not just that it runs). The bucket backend is what carries the Ray /
DataFusion distribution wiring, so a healthy single-node bucket run is the
prerequisite for the distributed claim.

Synthetic shape: high-entropy distinct names (so different people are
separable) + injected near-duplicates (a typo'd name + shared email + block
key). Each base row is an entity; a base may be duplicated more than once.
Ground truth is the full per-entity CLIQUE (all within-entity pairs), exact in
`__row_id__` space since `dedupe_df` assigns row-id by input position.

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_fs_distributed.py \
        --rows 5000000 --dup-frac 0.2 --backend bucket

Emits machine-greppable `KEY=VALUE` lines (WALL_SECONDS, PEAK_RSS_GB, F1, ...)
plus a markdown block the workflow tees into the job summary.
"""
from __future__ import annotations

import argparse
import random
import resource
import time

import polars as pl


# Realistic name diversity: real person data has 10^4-10^5 distinct surnames, so
# two random people sharing (or even looking similar to) another's name in a
# small block is rare. A tiny pool (30 surnames) manufactures coincidental
# same-name non-duplicates that are ambiguous to ANY matcher; a syllable-
# concatenation pool manufactures shared-PREFIX names that jaro_winkler (prefix-
# weighted) scores as false partials. Both are generator artifacts, not FS
# weaknesses. Generate high-entropy names with VARIED prefixes so genuinely
# different people are separable (low pairwise jaro_winkler).
def _build_pools(n_each: int = 60_000, seed: int = 1) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    cons, vow = "bcdfghjklmnprstvwz", "aeiou"

    def _name() -> str:
        # 3-4 CV syllables -> length 6-8, first letter spread across 18 consonants
        # so prefixes are high-entropy (no jaro prefix clustering).
        return "".join(rng.choice(cons) + rng.choice(vow)
                       for _ in range(rng.randint(3, 4)))

    surn = list({_name() for _ in range(n_each * 2)})[:n_each]
    first = list({_name() for _ in range(n_each * 2)})[:n_each]
    return surn, first


_SURN, _FIRST = _build_pools()


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 4:
        return s
    j = rng.randrange(len(s) - 1)
    return s[:j] + s[j + 1] + s[j] + s[j + 2:]  # adjacent transposition


def gen_with_gt(n: int, dup_frac: float, seed: int):
    """Return (df, ground_truth_pairs). __row_id__ == input row position.

    Ground truth is the full per-entity CLIQUE: every base row is its own
    entity, every dup carries its base's entity id, and GT = all within-entity
    pairs. A base can be duplicated more than once, so an entity may have 3+
    rows -- the dup<->dup pairs are true matches too, and clustering finds them,
    so GT must be the clique (a star base->dup GT would wrongly score those as
    false positives). Dups copy an ORIGINAL base (not another dup) so entity
    membership is unambiguous."""
    import itertools

    rng = random.Random(seed)
    n_zip = max(1, n // 40)
    rows: list[dict] = []
    entity: list[int] = []  # entity id per row (row index of the base)
    for i in range(n):
        f, l = rng.choice(_FIRST), rng.choice(_SURN)
        rows.append({
            "first_name": f, "last_name": l,
            "email": f"{f}.{l}.{i}@example.com",
            "zip": f"{rng.randrange(n_zip):05d}",
        })
        entity.append(i)
    n_dups = int(n * dup_frac)
    for _ in range(n_dups):
        base_idx = rng.randrange(n)  # ORIGINAL bases only (no dup-of-dup chains)
        src = rows[base_idx]
        rows.append({
            "first_name": _typo(src["first_name"], rng),
            "last_name": src["last_name"],
            "email": src["email"],  # exact agree on email -> strong FS signal
            "zip": src["zip"],
        })
        entity.append(base_idx)

    groups: dict[int, list[int]] = {}
    for idx, e in enumerate(entity):
        groups.setdefault(e, []).append(idx)
    gt: set[tuple[int, int]] = set()
    for members in groups.values():
        if len(members) >= 2:
            for a, b in itertools.combinations(members, 2):
                gt.add((a, b))
    # NOTE: order preserved (no shuffle) so row position == __row_id__ == GT idx.
    return pl.DataFrame(rows), gt


def _fs_cfg(backend: str):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.85),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
            MatchkeyField(field="email", scorer="exact", levels=2),
        ])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend=backend,
    )


def _zero_config_cfg(df, backend: str):
    """The ZERO-CONFIG FS path -- auto_configure_probabilistic_df on a bounded
    head sample (auto-config only ever samples). This is the path the
    identifier-admission fix lives in: on realistic data whose sample under-
    represents duplicates it used to drop the shared `email` identifier and
    collapse the EM model to F1=0. The gate asserts zero-config produces a
    WORKING config (F1 above --min-f1), so that regression cannot return
    silently -- qis_gate only covers the weighted path."""
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    sample = df.head(200_000)
    cfg = auto_configure_probabilistic_df(sample.to_arrow(), n_rows_full=df.height)
    cfg.backend = backend
    return cfg


def _peak_rss_gb() -> float:
    # ru_maxrss is KiB on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 * 1024.0)


def evaluate_scale_gate(
    f1: float,
    wall_seconds: float,
    peak_rss_gb: float,
    *,
    min_f1: float | None = None,
    max_wall_seconds: float | None = None,
    max_peak_rss_gb: float | None = None,
    rows: int = 0,
    config_mode: str = "explicit",
) -> list[str]:
    """Pure gate: return a list of ``::error::`` messages for every threshold the
    measured run breached (empty list = PASS). Extracted from ``main`` so the
    scheduled-scale-gate decision (issue #1805 checkbox 3) is unit-testable
    without running a 5M dedupe -- mirrors ``scripts/qis_gate.py::evaluate_gate``.

    Each threshold is independent and optional (``None`` = not gated), so the
    same driver serves the ad-hoc ``workflow_dispatch`` bench (no thresholds) and
    the weekly scheduled gate (all three set). All breaches are reported, not
    just the first, so one run surfaces every regression.
    """
    errors: list[str] = []
    if min_f1 is not None and f1 < min_f1:
        errors.append(
            f"::error::{config_mode}-config FS F1 {f1:.4f} < floor {min_f1:.2f} "
            f"at {rows} rows -- quality regression (matchkeys/blocking dropped "
            f"the identity signal?)"
        )
    if max_wall_seconds is not None and wall_seconds > max_wall_seconds:
        errors.append(
            f"::error::FS dedupe wall {wall_seconds:.1f}s > ceiling "
            f"{max_wall_seconds:.0f}s at {rows} rows -- scale-perf regression"
        )
    if max_peak_rss_gb is not None and peak_rss_gb > max_peak_rss_gb:
        errors.append(
            f"::error::FS dedupe peak RSS {peak_rss_gb:.2f} GB > ceiling "
            f"{max_peak_rss_gb:.1f} GB at {rows} rows -- memory regression"
        )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=5_000_000)
    ap.add_argument("--dup-frac", type=float, default=0.2)
    ap.add_argument("--backend", default="bucket")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--config-mode", choices=["explicit", "zero"], default="explicit",
                    help="explicit = hand-written FS config; zero = auto-config "
                         "(the zero-config quality-at-scale gate)")
    ap.add_argument("--min-f1", type=float, default=None,
                    help="gate: exit non-zero if F1 < this floor")
    ap.add_argument("--max-wall-seconds", type=float, default=None,
                    help="gate: exit non-zero if wall > this ceiling")
    ap.add_argument("--max-peak-rss-gb", type=float, default=None,
                    help="gate: exit non-zero if peak RSS > this ceiling")
    args = ap.parse_args()

    print(f"[gen] {args.rows} base rows + {int(args.rows*args.dup_frac)} dups "
          f"(backend={args.backend})", flush=True)
    t_gen = time.perf_counter()
    df, gt = gen_with_gt(args.rows, args.dup_frac, args.seed)
    print(f"[gen] {df.height} total rows, {len(gt)} GT pairs in "
          f"{time.perf_counter()-t_gen:.1f}s", flush=True)

    from goldenmatch import dedupe_df
    from goldenmatch.core.evaluate import evaluate_clusters

    cfg = (_zero_config_cfg(df, args.backend) if args.config_mode == "zero"
           else _fs_cfg(args.backend))
    print(f"[config] mode={args.config_mode} "
          f"matchkey_fields={[f.field for m in cfg.get_matchkeys() for f in m.fields]}",
          flush=True)
    t0 = time.perf_counter()
    result = dedupe_df(df, config=cfg, confidence_required=False)
    wall = time.perf_counter() - t0
    rss = _peak_rss_gb()

    clusters = result.clusters
    multi = sum(1 for c in clusters.values() if len(c.get("members", [])) > 1)
    ev = evaluate_clusters(clusters, gt)

    for k, v in [
        ("ROWS", df.height), ("BACKEND", args.backend),
        ("WALL_SECONDS", f"{wall:.3f}"), ("PEAK_RSS_GB", f"{rss:.2f}"),
        ("CLUSTERS_TOTAL", len(clusters)), ("CLUSTERS_MULTI", multi),
        ("GT_PAIRS", len(gt)),
        ("PRECISION", f"{ev.precision:.4f}"), ("RECALL", f"{ev.recall:.4f}"),
        ("F1", f"{ev.f1:.4f}"),
    ]:
        print(f"{k}={v}", flush=True)

    print("\n## bench-fs-distributed")
    print(f"- rows: **{df.height}**  backend: `{args.backend}`")
    print(f"- wall: **{wall:.1f}s**  peak RSS: **{rss:.2f} GB**")
    print(f"- multi-member clusters: {multi}  (GT pairs: {len(gt)})")
    print(f"- **P={ev.precision:.3f}  R={ev.recall:.3f}  F1={ev.f1:.3f}**")

    errors = evaluate_scale_gate(
        ev.f1, wall, rss,
        min_f1=args.min_f1,
        max_wall_seconds=args.max_wall_seconds,
        max_peak_rss_gb=args.max_peak_rss_gb,
        rows=df.height,
        config_mode=args.config_mode,
    )
    for msg in errors:
        print(msg)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
