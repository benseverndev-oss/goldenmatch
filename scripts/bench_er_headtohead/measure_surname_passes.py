"""Can a surname blocking pass recover recall without the common-surname blow-up?

historical_50k loses recall at candidate generation, and the one pass that
recovers the most of it -- ``surname`` soundex -- is rejected by auto-config's
``_is_scale_safe`` (#876/#715): Zipf-concentrated surnames make blocks that grow
with N. Forcing it in also cost 0.0276 F1 at 50k (#2929).

This measures candidate passes on the pipeline's own blocking (``build_blocks``,
auto-config's ``max_block_size`` and ``skip_oversized``), two ways:

* RECALL at full N: true pairs the pass puts in a shared block, the MARGINAL true
  pairs no auto-config pass covers, and the comparisons it adds.
* SCALE across nested samples of ENTITIES (25% / 50% / 100%), keeping every record
  of a sampled entity: comparisons per row and the largest raw key group before any
  oversized-block splitting. Sampling ROWS instead would shrink every entity's
  duplicate count with the sample, so every key -- concentrated or not -- would
  look like it grows in proportion to N (measured: x3.6-x4.2 for all five default
  candidates). Holding duplicates per entity fixed leaves only growth from more
  DISTINCT people colliding on the key, which is what scale adds. A concentrated
  key's largest group still grows with the entity count; a bounded one's should not.

Entities are the connected components of the ground-truth pairs; a record in no
true pair is its own entity.

Pass specs: ``field:t1,t2`` for one field, ``field:t1,t2+field:t3`` for a compound
key with per-field transforms (e.g. ``surname:lowercase,soundex+dob:substring:0:4``).

Known-positive: the union coverage of auto-config's own passes is printed and must
match ``measure_blocking_signatures.py``'s reachable pair completeness for the same
dataset (0.8793 on historical_50k).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_CANDIDATES = (
    "surname:lowercase,soundex",
    "surname:lowercase,soundex+dob:substring:0:4",
    "surname:lowercase,soundex+first_name:lowercase,substring:0:1",
    "surname:lowercase,soundex+birth_place:lowercase,strip",
    "surname:lowercase,substring:0:4+dob:substring:0:4",
)
FRACTIONS = (0.25, 0.5, 1.0)


def pass_from_spec(spec: str):
    """``field:t1,t2`` or ``field:t1,t2+field:t3`` -> ``BlockingKeyConfig``."""
    from goldenmatch.config.schemas import BlockingKeyConfig

    parts = [p for p in spec.split("+") if p]
    if not parts:
        raise ValueError(f"empty blocking pass spec {spec!r}")
    fields: list[str] = []
    chains: dict[str, list[str]] = {}
    for part in parts:
        field, _, transforms = part.partition(":")
        if not field:
            raise ValueError(f"blocking pass spec {spec!r} has a part with no field")
        fields.append(field)
        chains[field] = [t for t in transforms.split(",") if t]
    if len(fields) == 1:
        return BlockingKeyConfig(fields=fields, transforms=chains[fields[0]])
    return BlockingKeyConfig(fields=fields, transforms=[], field_transforms=chains)


def label(key) -> str:
    chains = getattr(key, "field_transforms", None) or {}
    out = []
    for f in key.fields:
        ts = chains.get(f, key.transforms or [])
        shown = "+".join(t for t in ts if t not in ("lowercase", "strip")) or "raw"
        out.append(f"{f}[{shown}]")
    return " x ".join(out)


def _block_sets(frame, blocking_cfg, keys):
    """Per pass: {row_id: set(block ids)} plus comparisons, via the pipeline's build_blocks."""
    from measure_meta_blocking import _members_for_passes

    passes, blocks, rids, per_pass = _members_for_passes(frame, blocking_cfg, keys)
    sets: list[dict[int, set[int]]] = [{} for _ in keys]
    for p, b, r in zip(passes, blocks, rids):
        sets[p].setdefault(r, set()).add(b)
    return sets, [pp["comparisons"] for pp in per_pass]


def _raw_max_group(frame, key) -> int:
    chains = getattr(key, "field_transforms", None) or None
    col = frame.derive_block_key(
        list(key.fields), list(key.transforms or []), field_transforms=chains
    )
    keyed = frame.with_column("__block_key__", col).filter_valid_key("__block_key__")
    if keyed.height == 0:
        return 0
    return int(keyed.group_len(["__block_key__"]).column("len").max() or 0)


def _covers(sets: dict[int, set[int]], a: int, b: int) -> bool:
    sa = sets.get(a)
    return bool(sa) and bool(sa & sets.get(b, set()))


def _entities(n_rows: int, gt_pairs) -> list[int]:
    """Entity id per row: connected components of the true pairs."""
    parent = list(range(n_rows))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in gt_pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return [find(i) for i in range(n_rows)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="historical_50k")
    ap.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="pass spec (repeatable; default: built-in list)",
    )
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    import numpy as np
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.frame import to_frame

    from scripts.autoconfig_quality import datasets as D

    loaded = getattr(D, f"_{args.dataset}")()
    if loaded is None:
        raise SystemExit(f"dataset {args.dataset!r} is unavailable here")
    df, gt = loaded
    gt_pairs = sorted((min(a, b), max(a, b)) for a, b in gt)
    cfg = auto_configure_probabilistic_df(df)
    base = list(cfg.blocking.resolved_keys())
    candidates = [pass_from_spec(s) for s in (args.candidate or DEFAULT_CANDIDATES)]
    frame = to_frame(df.with_row_index("__row_id__"))
    entity = _entities(df.height, gt_pairs)
    n_entities = len(set(entity))
    print(
        f"{args.dataset}: rows {df.height:,}, entities {n_entities:,}, true pairs {len(gt_pairs):,}, "
        f"auto-config passes {len(base)}, max_block_size {cfg.blocking.max_block_size}, "
        f"skip_oversized {cfg.blocking.skip_oversized}"
    )

    all_keys = base + candidates
    sets, comparisons = _block_sets(frame, cfg.blocking, all_keys)
    base_sets = sets[: len(base)]
    covered_base = [any(_covers(s, a, b) for s in base_sets) for a, b in gt_pairs]
    base_pc = sum(covered_base) / len(gt_pairs)
    print(
        f"auto-config union coverage (known-positive, should equal the harness's reachable PC): "
        f"{base_pc:.4f}; comparisons {sum(comparisons[: len(base)]):,}"
    )

    print("\nRECALL at full N")
    print(f"  {'pass':58s} {'alone':>7s} {'marginal':>9s} {'union':>7s} {'comparisons':>12s}")
    for k, key in enumerate(candidates):
        s = sets[len(base) + k]
        alone = [_covers(s, a, b) for a, b in gt_pairs]
        marginal = sum(1 for cb, al in zip(covered_base, alone) if al and not cb)
        print(
            f"  {label(key):58s} {sum(alone) / len(gt_pairs):7.4f} {marginal:9,d} "
            f"{(sum(covered_base) + marginal) / len(gt_pairs):7.4f} {comparisons[len(base) + k]:12,d}"
        )

    rng = np.random.default_rng(args.seed)
    entity_ids = sorted(set(entity))
    entity_order = [entity_ids[i] for i in rng.permutation(len(entity_ids))]
    print(
        "\nSCALE over nested ENTITY samples (all records of each sampled entity): "
        "comparisons per row | largest raw key group (before splitting)"
    )
    header = "  ".join(f"{int(f * 100):>3d}%: pairs/row  max group" for f in FRACTIONS)
    print(f"  {'pass':58s} {header}   growth 25%->100%")
    rows = {}
    for f in FRACTIONS:
        keep = set(entity_order[: int(round(len(entity_order) * f))])
        idx = [i for i, e in enumerate(entity) if e in keep]
        sub = to_frame(df[idx].with_row_index("__row_id__"))
        _, sub_comparisons = _block_sets(sub, cfg.blocking, candidates)
        rows[f] = [
            (c / len(idx), _raw_max_group(sub, key)) for c, key in zip(sub_comparisons, candidates)
        ]
    for k, key in enumerate(candidates):
        cells = "  ".join(f"{rows[f][k][0]:15.1f} {rows[f][k][1]:10,d}" for f in FRACTIONS)
        p0, p1 = rows[FRACTIONS[0]][k][0], rows[FRACTIONS[-1]][k][0]
        g0, g1 = rows[FRACTIONS[0]][k][1], rows[FRACTIONS[-1]][k][1]
        print(
            f"  {label(key):58s} {cells}   pairs/row x{(p1 / p0) if p0 else float('nan'):.2f}, "
            f"group x{(g1 / g0) if g0 else float('nan'):.2f} (entities x4)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
