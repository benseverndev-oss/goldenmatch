"""Dump the DQbench ER tier datasets + ground truth locally for diagnosis.

DQbench generates its ER tiers programmatically (no static CSVs), so reproducing
a controller failure — e.g. the T3 precision collapse — needs the *actual* data,
not a hand-rolled fixture. This writes each tier's rows + GT pairs to
``<out>/erN/{data.csv,gt.json}`` so zero-config can be run against the real
adversarial shapes (phonetic / nickname / unicode / field-blanking /
abbreviation / multi-field dupes + collateral-merge traps).

    python scripts/dump_dqbench_er_tiers.py --tiers 1 2 3 4 --out /tmp/dqbench_er

Requires the org dqbench (has the ER category):
    pip install "git+https://github.com/benseverndev-oss/dqbench"
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path


def dump_tier(tier: int, out: Path) -> Path | None:
    """Generate one ER tier and write data.csv + gt.json. Returns the dir or None."""
    try:
        mod = importlib.import_module(f"dqbench.generator.er_tier{tier}")
        gen = getattr(mod, f"generate_er_tier{tier}")
    except (ImportError, AttributeError) as exc:
        print(f"  er{tier}: generator unavailable ({exc}) — is the git dqbench installed?")
        return None

    df, gt = gen()
    tdir = out / f"er{tier}"
    tdir.mkdir(parents=True, exist_ok=True)
    df.write_csv(tdir / "data.csv")
    (tdir / "gt.json").write_text(json.dumps({
        "tier": getattr(gt, "tier", tier),
        "rows": df.height,
        "difficulty": getattr(gt, "difficulty", "?"),
        "duplicate_pairs": [list(p) for p in gt.duplicate_pairs],
        "total_duplicates": getattr(gt, "total_duplicates", len(gt.duplicate_pairs)),
    }))
    print(f"  er{tier}: {df.height} rows, {len(gt.duplicate_pairs)} GT pairs, "
          f"difficulty={getattr(gt, 'difficulty', '?')} -> {tdir}")
    return tdir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiers", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--out", type=Path, default=Path("/tmp/dqbench_er"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for t in args.tiers:
        dump_tier(t, args.out)
    print(f"done -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
