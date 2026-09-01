"""The bucket scorer blocked a `static` config on the WRONG field list.

`backends/score_buckets.py` resolved its block keys as::

    pass_keys = blocking_config.passes or blocking_config.keys

which inverts `blocker.py` for one shape: a `strategy="static"` config carrying
BOTH `keys` and `passes`. Legacy blocks a static config on `keys` and never
looks at `passes`; `passes or keys` took `passes` instead. The two backends then
blocked on different fields, and bucket scored a candidate set the plan never
described -- zero pairs, no error, no warning.

Why no existing test caught it: every parity case in
`test_score_buckets_multipass.py` sets `keys=[zip]` alongside
`passes=[zip, ssn]`, i.e. `keys` is a SUBSET of `passes`, so taking `passes`
lost nothing. Auto-config emits `keys=[org_name]` with
`passes=[postcode, record_id]`, where the dropped key is the only one that pairs
the duplicates.

Measured on the suggest_quality `orgs_hard` corpus (845 rows, that exact
auto-config): bucket 0 pairs / 0 dupes vs legacy 242 pairs / 313 dupes, moving
the dataset's convergence F1 from 0.2939 to 0.0000. With the fix it is 0.4108.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

# `grp` pairs the records; `city` and `id` deliberately do not.
#
# The names must be dissimilar ACROSS groups. An earlier draft used
# "Acme Holdings {i}", where every record scored > 0.8 against every other -- so
# blocking on the WRONG field still produced (wrong) clusters and the test passed
# with the fix reverted. Distinct words make the wrong blocking produce a
# different answer, which is what the assertions need in order to mean anything.
_WORDS = [
    "Quorvex", "Blimtal", "Zandric", "Purnoss", "Ferrigo",
    "Wystrim", "Kalbonn", "Tovanti", "Grexil", "Marnoth",
    "Sylvorn", "Dranmix", "Ulbreth", "Cintaro", "Hexavil",
    "Jorlund", "Nepthal", "Ravonix", "Torbeck", "Yalmire",
]


def _key(field: str) -> BlockingKeyConfig:
    return BlockingKeyConfig(fields=[field], transforms=["lowercase"])


def _frame(pl):
    rows = []
    for i, w in enumerate(_WORDS):
        rows.append(
            {"id": f"a{i}", "grp": f"g{i}", "name": f"{w} Holdings", "city": "leeds"}
        )
        rows.append(
            {"id": f"b{i}", "grp": f"g{i}", "name": f"{w} Holdngs", "city": "york"}
        )
    return pl.DataFrame(rows)


def _config(strategy: str) -> GoldenMatchConfig:
    """A WEIGHTED matchkey on purpose: bucket routing governs the FUZZY scoring
    stage only, and exact matchkeys take a different path. A first draft of this
    test used an exact matchkey and passed with the fix reverted."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name",
                type="weighted",
                threshold=0.8,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(
            strategy=strategy,
            keys=[_key("grp")],
            passes=[_key("city"), _key("id")],
            union_mode=True,
        ),
    )


def _multi_clusters(df, cfg) -> list:
    from goldenmatch.core.pipeline import run_dedupe_df

    res = run_dedupe_df(df, cfg, output_clusters=True, output_dupes=True)
    clusters = res.get("clusters") or {}
    return [c for c in clusters.values() if len(c.get("members", [])) > 1]


def test_static_keys_and_passes_finds_its_duplicates():
    """The regression, asserted on the OUTCOME rather than on routing."""
    pl = pytest.importorskip("polars")

    multi = _multi_clusters(_frame(pl), _config("static"))

    # EXACT, not "some": with the wrong field list, blocking can still emit
    # spurious clusters, so a non-empty check would not distinguish the two.
    assert len(multi) == len(_WORDS), (
        f"expected {len(_WORDS)} two-member clusters, got {len(multi)} -- bucket "
        f"blocked on `passes` instead of `keys` again"
    )
    assert all(len(c["members"]) == 2 for c in multi)


def test_bucket_and_legacy_agree_on_a_static_keys_plus_passes_config():
    """Parity for the shape no existing parity test covered.

    The two backends must select the same block-key list, so forcing the legacy
    route has to produce the same clusters.
    """
    pl = pytest.importorskip("polars")
    from goldenmatch.core import pipeline

    df, cfg = _frame(pl), _config("static")

    bucket = {frozenset(c["members"]) for c in _multi_clusters(df, cfg)}

    original = pipeline._use_bucket_scorer
    pipeline._use_bucket_scorer = lambda *a, **k: False
    try:
        legacy = {frozenset(c["members"]) for c in _multi_clusters(df, cfg)}
    finally:
        pipeline._use_bucket_scorer = original

    assert bucket == legacy
    assert bucket, "both backends found nothing -- the fixture stopped matching"


def test_multi_pass_still_blocks_on_passes():
    """Guard against 'fixing' this by inverting it. A `multi_pass` config carries
    its keys in `.passes`, and `blocker.py` iterates `.passes` for that strategy
    -- so bucket must keep preferring `passes` there.

    Asserted as a DIFFERENCE rather than as emptiness: blocking on `city` still
    lets some cross-group pairs clear 0.8 (the names share " Holdings"), so
    "finds nothing" was the wrong expectation. What must hold is that the two
    strategies select different block keys and therefore different clusters.
    """
    pl = pytest.importorskip("polars")
    df = _frame(pl)

    static = {frozenset(c["members"]) for c in _multi_clusters(df, _config("static"))}
    multi = {frozenset(c["members"]) for c in _multi_clusters(df, _config("multi_pass"))}

    assert static != multi, (
        "static and multi_pass produced identical clusters -- bucket is no "
        "longer distinguishing `keys` from `passes` by strategy"
    )
    assert len(static) == len(_WORDS)
