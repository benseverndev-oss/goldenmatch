"""Frozen corpus for FS link-cut routing (spec 2026-09-11-fs-cut-rule-routing-design, P3).

DESIGN is the labelled part of the auto-config quality registry plus the
`ab_lever` panel. The two unlabelled blocking-shape anchors (sparse_zip,
shared_email) cannot be scored and are left out. Routing rows may be written from
these datasets' results.

HOLDOUT is never used to write or tune a row. A row ships only if it is also never
below the default on every HOLDOUT dataset. Changing HOLDOUT after any row exists
means re-running the full gate, so tests/test_corpus.py pins it verbatim.
"""

from __future__ import annotations

import argparse
import json

DESIGN: tuple[str, ...] = (
    "person",
    "household_hardneg",
    "cotenant_hardneg",
    "febrl3",
    "febrl4",
    "ncvr_synthetic",
    "ncvr_real",
    "historical_50k",
    "dblp_acm",
    "dblp_scholar",
    "amazon_google",
    "musicbrainz_20k",
    "geo_settlements",
    "affiliations",
    "synth_biblio_d02",
    "synth_person_d02",
    "synth_product_d02",
    "synth_product_d20",
    "ncvr_synthetic_s7",
    "household_hardneg_s7",
    "cotenant_hardneg_s7",
)

HOLDOUT: tuple[str, ...] = (
    "abt_buy",
    "walmart_amazon",
    "itunes_amazon",
    "fodors_zagats",
    "febrl1",
    "febrl2",
    "synth_person_c05_d10",
    "synth_person_c05_d40",
    "synth_person_c20_d10",
    "synth_person_c20_d40",
    "synth_biblio_c05_d10",
    "synth_biblio_c05_d40",
    "synth_biblio_c20_d10",
    "synth_biblio_c20_d40",
)

#: Never provisioned in CI: real voter PII stays on the laptop.
LOCAL_ONLY: frozenset[str] = frozenset({"ncvr_real"})

_SETS = {"design": DESIGN, "holdout": HOLDOUT, "all": DESIGN + HOLDOUT}


def corpus_of(name: str) -> str:
    """``"design"``, ``"holdout"``, or ``"unlisted"`` for an ad-hoc dataset name."""
    if name in DESIGN:
        return "design"
    if name in HOLDOUT:
        return "holdout"
    return "unlisted"


def select(corpus: str) -> tuple[str, ...]:
    """The dataset names of ``corpus`` (``design``, ``holdout`` or ``all``)."""
    return _SETS[corpus]


def ci_datasets(corpus: str) -> list[str]:
    """``select(corpus)`` without the local-only datasets."""
    return [name for name in select(corpus) if name not in LOCAL_ONLY]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="List the FS link-cut routing corpus.")
    ap.add_argument("--list", required=True, choices=sorted(_SETS), help="corpus to list")
    ap.add_argument("--ci", action="store_true", help="drop local-only datasets")
    args = ap.parse_args(argv)
    names = ci_datasets(args.list) if args.ci else list(select(args.list))
    print(json.dumps(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
