"""Zero-config must not silently lose recall.

The gate that was missing. Zero-config on person-shaped data regressed from
pairwise F1 0.997 to 0.554 in production while every unit test stayed green,
because nothing asserted on the ANSWER zero-config produces -- only on the parts.

The failure mode is specifically hard to notice: every cause found in that
investigation lost CANDIDATES rather than mis-scoring them, so precision stayed
at 1.0000 and only recall moved. A precision-shaped alarm would never fire. The
three real causes were

  * `rule_low_reduction_ratio` firing on an unmeasured profile (reduction_ratio
    defaults to 0.0) and rebuilding `passes` from `keys`, dropping six of eight
    blocking passes;
  * the controller never MEASURING its blocking profile for `multi_pass` (the
    common zero-config shape), so the above went unnoticed;
  * a committed `backend='chunked'` dropping FS off the bucket scorer onto the
    legacy batched path, which retained 9,250 pairs where bucket retained
    120,269.

None of those is visible from a config assertion. All three are visible here.

Deliberately end-to-end and deliberately cheap: `dedupe_df(df)` with NO config,
on a fixture small enough to run in a normal unit shard. The floor is set well
below the ~0.99 the path actually achieves, so it fails on a COLLAPSE (the 0.55
class) rather than on scorer noise.
"""
from __future__ import annotations

import random

import pyarrow as pa
import pytest
from goldenmatch import dedupe_df

# High-cardinality name pools. An earlier version of this fixture used ten
# first names and five cities, which made the auto-chosen [city, first_name]
# key degenerate (~325 rows into a handful of blocks) and produced P 0.0461 /
# R 1.0000 -- catastrophic OVER-merge. That is a property of unrealistic test
# data, not of the engine: real person data has high-cardinality names, and a
# floor built on a degenerate fixture measures the fixture.
_FIRST = [f"{a}{b}" for a in
          ("ann", "bob", "cara", "dan", "eve", "fay", "gus", "hal", "iris", "jack",
           "kim", "liam", "mia", "noah", "olive", "pia", "quinn", "rosa", "sam", "tom")
          for b in ("", "ie", "ette", "son", "ly", "na", "ric", "bel", "ton", "dra")]
_LAST = [f"{a}{b}" for a in
         ("smith", "jones", "lee", "poe", "ray", "kim", "cruz", "diaz", "novak", "ford",
          "hale", "reed", "vance", "orr", "pike", "shaw", "todd", "ung", "vega", "wolf")
         for b in ("", "s", "wood", "man", "field", "ley", "ton", "berg", "well", "dale")]
_CITY = [f"city{i:03d}" for i in range(60)]

_RECALL_FLOOR = 0.80
_F1_FLOOR = 0.85


def _person_fixture(n_entities: int = 900, dupe_rate: float = 0.25):
    """Person-shaped data with KNOWN duplicates and a light corruption model.

    Duplicates keep dob+postcode and corrupt one name character, so they are
    reachable through several orthogonal blocking passes but through no single
    one -- the property that makes a dropped pass show up as lost recall rather
    than as noise.
    """
    rng = random.Random(11)
    first, last, dob, postcode, city, truth = [], [], [], [], [], []
    for e in range(n_entities):
        f, l = rng.choice(_FIRST), rng.choice(_LAST)
        d = f"19{rng.randint(50, 99)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        z = f"{10000 + rng.randint(0, 4000)}"
        c = rng.choice(_CITY)
        first.append(f); last.append(l); dob.append(d); postcode.append(z)
        city.append(c); truth.append(e)
        if rng.random() < dupe_rate:
            ff = f[:-1] + ("z" if not f.endswith("z") else "q")
            first.append(ff); last.append(l); dob.append(d); postcode.append(z)
            city.append(c); truth.append(e)
    idx = list(range(len(first)))
    rng.shuffle(idx)
    tbl = pa.table({
        "record_id": [str(i) for i in range(len(idx))],
        "first_name": [first[i] for i in idx],
        "surname": [last[i] for i in idx],
        "dob": [dob[i] for i in idx],
        "postcode": [postcode[i] for i in idx],
        "city": [city[i] for i in idx],
    })
    ent = [truth[i] for i in idx]
    return tbl, ent


def _pairwise(clusters, entities) -> tuple[float, float, float]:
    from itertools import combinations

    def pairs(labels):
        by: dict = {}
        for i, e in enumerate(labels):
            by.setdefault(e, []).append(i)
        return {tuple(sorted(p)) for m in by.values() if len(m) > 1
                for p in combinations(sorted(m), 2)}

    truth = pairs(entities)
    pred = set()
    for c in (clusters or {}).values():
        members = sorted(int(m) for m in c.get("members", []))
        if len(members) > 1:
            pred |= {tuple(sorted(p)) for p in combinations(members, 2)}
    tp = len(pred & truth)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(truth) if truth else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


@pytest.mark.timeout(300)
def test_zeroconfig_does_not_collapse_on_person_data():
    tbl, entities = _person_fixture()
    res = dedupe_df(tbl)
    p, r, f1 = _pairwise(res.clusters, entities)
    assert r >= _RECALL_FLOOR, (
        f"zero-config recall {r:.4f} < {_RECALL_FLOOR} (precision {p:.4f}). "
        "Every known cause of this lost CANDIDATES -- dropped blocking passes, "
        "an unmeasured blocking profile, or FS routed off the bucket scorer -- "
        "so precision stays ~1.0 while recall collapses. Check which blocking "
        "passes the committed config carries and which scorer actually ran."
    )
    assert f1 >= _F1_FLOOR, f"zero-config F1 {f1:.4f} < {_F1_FLOOR} (P {p:.4f} R {r:.4f})"


@pytest.mark.timeout(300)
def test_the_fixture_is_actually_reachable():
    """Guard the guard: if the fixture had no findable duplicates the floor
    above would be unfalsifiable. An explicit config that blocks on the stable
    postcode must clear the floor comfortably."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    tbl, entities = _person_fixture()
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="k", type="weighted", threshold=0.80, fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.34),
            MatchkeyField(field="surname", scorer="jaro_winkler", weight=0.33),
            MatchkeyField(field="dob", scorer="exact", weight=0.33),
        ])],
        blocking=BlockingConfig(strategy="static", keys=[
            BlockingKeyConfig(fields=["postcode"], transforms=["strip"])]),
    )
    _p, r, _f1 = _pairwise(dedupe_df(tbl, config=cfg).clusters, entities)
    assert r >= _RECALL_FLOOR, f"fixture unreachable even with explicit config (recall {r:.4f})"


# ── Rule invariant: no policy rule may shrink the blocking plan ───────────────
#
# The end-to-end floor above is necessary but NOT sufficient, and that was
# verified rather than assumed: restoring the original
# `rule_low_reduction_ratio` bug leaves it GREEN. At ~1,100 rows that rule never
# fires at all (v0 emits 8 passes, the committed config keeps 8), and the outcome
# only diverges at a scale too large for a unit shard (person@10K: F1 0.6421 vs
# 0.9973).
#
# So the invariant is asserted against the RULES directly, where it is cheap and
# scale-free: a policy rule may rewrite blocking, but it may never return FEWER
# passes than it was given. `rule_low_reduction_ratio` violated that by
# rebuilding `passes` from `keys` -- for a multi_pass plan that silently dropped
# six of eight passes, and because a dropped pass removes candidates rather than
# inventing them, precision stayed 1.0000 and nothing looked wrong.
#
# This covers rules that do not exist yet, which the single-rule regression test
# (test_rule_low_reduction_preserves_passes.py) cannot.


def _plan_size(cfg) -> int:
    b = getattr(cfg, "blocking", None)
    if b is None:
        return 0
    return len(list(b.passes or []) or list(b.keys or []))


def _multipass_config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    passes = [
        BlockingKeyConfig(fields=["city", "first_name"], transforms=["lowercase", "strip"]),
        BlockingKeyConfig(fields=["surname"], transforms=["lowercase", "soundex"]),
        BlockingKeyConfig(fields=["dob"], transforms=["strip"]),
        BlockingKeyConfig(fields=["postcode"], transforms=["strip"]),
    ]
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="probabilistic_auto", type="probabilistic",
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler"),
                    MatchkeyField(field="surname", scorer="jaro_winkler")],
        )],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city", "first_name"],
                                    transforms=["lowercase", "strip"])],
            passes=passes,
        ),
    )


def _degenerate_profile():
    """A profile shaped to make as many rules as possible fire at once: an
    UNMEASURED blocking sub-profile (the all-zero default that started this),
    low transitivity, and a unimodal score distribution."""
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ClusterProfile,
        ComplexityProfile,
        DataProfile,
        FieldStats,
        MatchkeyProfile,
        ScoringProfile,
    )

    return ComplexityProfile(
        data=DataProfile(n_rows=100_000, n_cols=6,
                         column_types={"first_name": "name", "surname": "name",
                                       "city": "geo", "dob": "text"}),
        # MEASURED but genuinely poor: n_blocks > 0 so the repaired
        # `rule_low_reduction_ratio` does not decline on "nothing measured yet",
        # with a low ratio so it (and its neighbours) actually engage. An earlier
        # version used the all-zero default here and NO rule fired -- the
        # guard-the-guard assertion at the end caught that the test was
        # asserting nothing.
        blocking=BlockingProfile(keys_used=[["city", "first_name"]], n_blocks=12,
                                 total_comparisons=4_000_000, reduction_ratio=0.10,
                                 block_sizes_p99=900, block_sizes_max=3000),
        scoring=ScoringProfile(n_pairs_scored=4000, mass_above_threshold=0.4,
                               dip_statistic=0.001),
        matchkey=MatchkeyProfile(per_field={"first_name": FieldStats(0.4, 0.0, 10)}),
        cluster=ClusterProfile(transitivity_rate=0.10),
    )


def test_no_policy_rule_shrinks_the_blocking_plan():
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES

    cfg = _multipass_config()
    before = _plan_size(cfg)
    assert before > 1, "fixture must be multi-pass for this invariant to mean anything"

    fired = []
    for rule in DEFAULT_RULES:
        name = getattr(rule, "__name__", repr(rule))
        try:
            out = rule(_degenerate_profile(), _multipass_config(), RunHistory())
        except Exception:
            continue  # a rule that cannot run on this shape proves nothing here
        if out is None:
            continue
        new_cfg, _decision = out
        fired.append(name)
        assert _plan_size(new_cfg) >= before, (
            f"{name} returned {_plan_size(new_cfg)} blocking passes from {before}. "
            "Dropping a pass removes candidates and never invents them, so this "
            "shows up as lost recall with precision still ~1.0 -- see "
            "rule_low_reduction_ratio, which rebuilt `passes` from `keys`."
        )
    assert fired, (
        "no rule fired on the degenerate profile, so this test asserted nothing. "
        "Widen the profile until at least one rule engages."
    )
