"""In-house ER quality gate — a self-contained, license-clean substitute for
the DQbench composite as the *internal CI gate*.

Runs unconditionally in the python lane (no restricted dataset). Two assertions
on synthetic labeled data whose ground truth is known by construction:

1. **Composite floor** — F1 vs ground truth stays above a per-config sanity
   floor (catches gross accuracy regressions). Floors are intentionally
   conservative: synthetic data is "easy", so the absolute number isn't a
   quality *claim* (DQbench / the public sets remain the external leaderboard
   for that). The value here is regression detection. Tighten from CI output.

2. **Backend parity** — polars-direct and bucket produce IDENTICAL clusters on
   the same data + config. This is the runnable substitute for the
   DQbench-on-native gate behind the bucket+native planner flip (#526):
   identical clusters ⟹ identical precision/recall/F1, by construction. Runs
   without the native ext (bucket uses its Python scorer here; the native
   *kernel*'s score-parity is covered separately by test_native_parity.py), so
   it gates every PR.

Synthetic data: distributed surnames (no soundex collapse) + injected near-dup
clones (typo'd name, shared email/zip) with known entity membership →
ground-truth pairs by construction. Deterministic seed.
"""
from __future__ import annotations

import random
from collections import defaultdict
from itertools import combinations

import polars as pl
import pytest
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.evaluate import evaluate_clusters

_SURN = [
    "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore",
    "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin",
    "Thompson", "Garcia", "Martinez", "Robinson", "Clark", "Rodriguez", "Lewis",
    "Lee", "Walker", "Hall", "Allen", "Young", "King", "Wright", "Lopez",
]
_FIRST = [
    "Alex", "Blair", "Casey", "Dana", "Eli", "Finley", "Gray", "Harper",
    "Indigo", "Jamie", "Kendall", "Logan", "Morgan", "Noel", "Oakley", "Parker",
    "Quinn", "Riley", "Sage", "Taylor", "Umi", "Val", "Wren", "Xena", "Yael",
    "Zane", "Avery", "Brook", "Cleo", "Drew",
]


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 3:
        return s
    i = rng.randrange(len(s) - 1)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]  # adjacent-char swap


def gen_labeled(n_entities: int = 400, seed: int = 7) -> tuple[pl.DataFrame, set]:
    """Synthetic records with known ground truth. Each entity = 1 original +
    0-2 typo'd clones (sharing email + zip). Returns (df, ground_truth_pairs)
    where pairs are (row_index, row_index) for rows of the same true entity."""
    rng = random.Random(seed)
    n_zip = max(1, n_entities // 2)
    tagged: list[tuple[dict, int]] = []
    for e in range(n_entities):
        f, l = rng.choice(_FIRST), rng.choice(_SURN)
        z = f"{rng.randrange(n_zip):05d}"
        email = f"{f}.{l}.{e}@x.com".lower()
        tagged.append(({"first_name": f, "last_name": l, "email": email, "zip": z}, e))
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            tagged.append(
                ({"first_name": _typo(f, rng), "last_name": l, "email": email, "zip": z}, e)
            )
    rng.shuffle(tagged)
    df = pl.DataFrame([rec for rec, _ in tagged])
    by_entity: dict[int, list[int]] = defaultdict(list)
    for pos, (_, e) in enumerate(tagged):
        by_entity[e].append(pos)
    gt: set = set()
    for positions in by_entity.values():
        for a, b in combinations(sorted(positions), 2):
            gt.add((a, b))
    return df, gt


def _cfg(backend: str | None, kind: str) -> GoldenMatchConfig:
    if kind == "exact_email":
        mks = [MatchkeyConfig(name="email", type="exact",
                              fields=[MatchkeyField(field="email")])]
        blocking = None
    else:  # fuzzy_name (block on zip, fuzzy on first+last name)
        mks = [MatchkeyConfig(
            name="name", type="weighted", threshold=0.85,
            fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.5),
                MatchkeyField(field="last_name", scorer="jaro_winkler", weight=0.5),
            ],
        )]
        blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    kwargs: dict = {"matchkeys": mks, "backend": backend}
    if blocking is not None:
        kwargs["blocking"] = blocking
    return GoldenMatchConfig(**kwargs)


def _partition(result) -> set:
    return {
        frozenset(c["members"]) for c in result.clusters.values()
        if len(c.get("members", [])) > 1
    }


# Conservative floors (synthetic is easy); tighten to ~actual-0.02 from CI output.
_FLOORS = {"exact_email": 0.90, "fuzzy_name": 0.75}


@pytest.fixture(scope="module")
def labeled():
    return gen_labeled()


@pytest.mark.parametrize("kind", ["exact_email", "fuzzy_name"])
def test_quality_composite_floor(labeled, kind):
    df, gt = labeled
    summary = evaluate_clusters(dedupe_df(df, config=_cfg(None, kind)).clusters, gt).summary()
    assert summary["f1"] >= _FLOORS[kind], (kind, summary)


@pytest.mark.parametrize("kind", ["exact_email", "fuzzy_name"])
def test_backend_parity_polars_vs_bucket(labeled, kind):
    """The bucket backend must cluster identically to polars-direct — the
    runnable gate behind the bucket+native flip (#526)."""
    df, _ = labeled
    polars_direct = dedupe_df(df, config=_cfg(None, kind))
    bucket = dedupe_df(df, config=_cfg("bucket", kind))
    assert _partition(polars_direct) == _partition(bucket), (
        f"{kind}: polars-direct and bucket produced DIFFERENT clusters"
    )
