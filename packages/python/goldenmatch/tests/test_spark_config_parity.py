"""P4 parity gate: the config-driven Spark tier must agree with the one-box.

Runs in the Spark lanes (`spark_connect` blocking, `sail` advisory); skips where
no Spark Connect client is installed.

The reference is computed HERE in plain Python -- candidate generation by
transform-and-group, scoring by ``core.scorer.score_pair``, clustering by
Union-Find -- rather than by calling the tier a second way. A reference that
shares code with the thing under test proves only that the code is
self-consistent.

The fixture is built to exercise exactly what P4 added over ``run_sail_pipeline``:
two blocking passes (so a pair reachable by either must appear once), a
multi-field weighted matchkey with unequal weights (so the denominator matters),
and NULLs in a scored field (so the exclusion rule matters).
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)

_ID = "__row_id__"

# (id, first, last, city, email). The nulls are chosen so each bug this pipeline
# had to avoid FLIPS a decision here -- measured, not assumed (threshold 0.85,
# weights first=1.0 last=3.0):
#
#  - rows 3/4 (`first` null on one side): correct score 1.0000 -> merged.
#    Using the matchkey's total weight as a fixed denominator instead of the
#    per-pair one gives 0.7500 -> the pair is LOST.
#  - rows 6/7 (`last` null on BOTH sides): correct score 0.4365 -> not merged.
#    Taking the kernel's null-vs-null 1.0 gives 0.8591 -> a FALSE MERGE.
#
# Change these rows and you may quietly remove the discrimination; recheck the
# two numbers above if you do.
_ROWS = [
    (0, "jon", "smith", "york", "j@x.com"),
    (1, "john", "smith", "york", "j@x.com"),
    (2, "jonathan", "smyth", "york", None),
    (3, "amy", "wong", "leeds", "a@x.com"),
    (4, None, "wong", "leeds", "a2@x.com"),
    (5, "bob", "clark", "hull", None),
    (6, "xavier", None, "derby", None),
    (7, "yolanda", None, "derby", None),
]
_COLS = [_ID, "first", "last", "city", "email"]


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="name",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="last", scorer="jaro_winkler", weight=3.0),
                ],
            )
        ],
        blocking=BlockingConfig(
            keys=[
                BlockingKeyConfig(fields=["city"]),
                BlockingKeyConfig(fields=["email"]),
            ]
        ),
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"city": GoldenFieldRule(strategy="first_non_null")},
        ),
    )


# ── the independent reference ────────────────────────────────────────

def _reference_pairs(config) -> set[tuple[int, int]]:
    """Candidate generation + scoring, done the one-box way."""
    from goldenmatch.core.scorer import score_pair
    from goldenmatch.utils.transforms import apply_transforms

    rows = [dict(zip(_COLS, r)) for r in _ROWS]

    candidates: set[tuple[int, int]] = set()
    for key in config.blocking.keys:
        buckets: dict[str, list[int]] = {}
        for r in rows:
            parts, null = [], False
            for f in key.fields:
                v = r[f]
                if v is None:
                    null = True
                    break
                parts.append(apply_transforms(str(v), list(key.transforms or [])) or "")
            if null:
                continue
            k = "||".join(parts)
            if k.strip().lower() in ("nan", "null", "none"):
                continue
            buckets.setdefault(k, []).append(r[_ID])
        for ids in buckets.values():
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    candidates.add((min(a, b), max(a, b)))

    by_id = {r[_ID]: r for r in rows}
    accepted = set()
    for a, b in candidates:
        for mk in config.get_matchkeys():
            if score_pair(by_id[a], by_id[b], mk.fields) >= mk.threshold:
                accepted.add((a, b))
                break
    return accepted


def _partition(ids, edges) -> set[frozenset]:
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comp: dict = {}
    for i in ids:
        comp.setdefault(find(i), set()).add(i)
    return {frozenset(v) for v in comp.values()}


# ── tests ────────────────────────────────────────────────────────────

@pytest.fixture()
def source(spark):
    return spark.createDataFrame(_ROWS, _COLS)


def test_candidate_pairs_match_the_one_box(source):
    """Two blocking passes, unioned and de-duplicated."""
    from goldenmatch.spark.config_pipeline import generate_candidates

    cfg = _config()
    got = {
        (int(r["a"]), int(r["b"]))
        for r in generate_candidates(source, cfg, id_col=_ID).collect()
    }

    # Both keys here are single-field with no transforms, so the expected set is
    # a plain group-by -- deliberately NOT reusing _reference_pairs' derivation,
    # so a bug in that helper cannot hide a bug in the tier.
    rows = [dict(zip(_COLS, r)) for r in _ROWS]
    want = set()
    for key in cfg.blocking.keys:
        buckets: dict = {}
        for r in rows:
            v = r[key.fields[0]]
            if v is None:
                continue
            buckets.setdefault(str(v), []).append(r[_ID])
        for ids in buckets.values():
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    want.add((min(a, b), max(a, b)))
    assert got == want


def test_a_pair_reachable_by_two_passes_appears_once(source):
    """Rows 0 and 1 share BOTH city and email. A union without a distinct would
    emit them twice and double-count them downstream."""
    from goldenmatch.spark.config_pipeline import generate_candidates

    rows = generate_candidates(source, _config(), id_col=_ID).collect()
    dupes = [r for r in rows if (int(r["a"]), int(r["b"])) == (0, 1)]
    assert len(dupes) == 1, f"pair (0,1) emitted {len(dupes)} times"


def test_scored_pairs_match_the_one_box(source):
    """The load-bearing parity assertion: same accepted pair set."""
    from goldenmatch.spark.config_pipeline import (
        generate_candidates,
        score_candidates,
    )

    cfg = _config()
    cands = generate_candidates(source, cfg, id_col=_ID)
    got = {
        (int(r["a"]), int(r["b"]))
        for r in score_candidates(cands, source, cfg, id_col=_ID).collect()
    }
    assert got == _reference_pairs(cfg)


def test_two_records_missing_the_scored_field_do_not_merge(source):
    """Rows 6 and 7 share a block and are BOTH null on `last`.

    The raw scorer kernel scores null-vs-null as a perfect 1.0, so a tier that
    took the kernel's answer would merge two records whose only shared evidence
    is a shared absence. They disagree on `first`, so the correct score is well
    under the threshold.
    """
    from goldenmatch.spark.config_pipeline import (
        generate_candidates,
        score_candidates,
    )

    cfg = _config()
    cands = generate_candidates(source, cfg, id_col=_ID)
    pairs = {
        (int(r["a"]), int(r["b"]))
        for r in score_candidates(cands, source, cfg, id_col=_ID).collect()
    }
    assert (6, 7) not in pairs, (
        "rows 6 and 7 merged on a shared NULL -- the null-vs-null 1.0 leaked "
        "through into the accepted pair set"
    )


def test_cluster_partition_matches_the_one_box(source):
    """End to end: the same clusters the one-box would produce."""
    from goldenmatch.spark.config_pipeline import run_config_pipeline

    cfg = _config()
    golden = run_config_pipeline(
        source, cfg, id_col=_ID, golden_cols=["first", "last", "city"]
    )
    got_clusters = {int(r["cluster_id"]) for r in golden.collect()}

    want = {c for c in _partition([r[0] for r in _ROWS], _reference_pairs(cfg))
            if len(c) > 1}
    assert len(got_clusters) == len(want), (
        f"{len(got_clusters)} multi-member clusters on Spark, {len(want)} in "
        f"the reference"
    )


def test_per_field_golden_strategy_is_applied(source):
    """`city` carries its own rule; the rest take the default. A tier that used
    one strategy for every column would pass a single-strategy test."""
    from goldenmatch.spark.config_pipeline import run_config_pipeline

    golden = run_config_pipeline(
        source, _config(), id_col=_ID, golden_cols=["first", "last", "city"]
    )
    out = golden.collect()
    assert out, "no golden records produced"
    assert set(golden.columns) == {"cluster_id", "first", "last", "city"}
    # `city` uses first_non_null and every member of a city block shares it, so
    # the survivor must be that city -- not a null, and not a merged artifact.
    for row in out:
        assert row["city"] in {"york", "leeds", "hull", "derby"}


def test_probabilistic_config_is_refused_on_spark(source):
    """What a Splink import lands on today. from_splink always emits a
    probabilistic matchkey, so this is the real cutover message until P5."""
    from goldenmatch.spark.config_pipeline import run_config_pipeline

    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="splink",
                type="probabilistic",
                fields=[MatchkeyField(field="first", scorer="jaro_winkler")],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
    )
    with pytest.raises(NotImplementedError, match="P5"):
        run_config_pipeline(source, cfg, id_col=_ID, golden_cols=["first"])
