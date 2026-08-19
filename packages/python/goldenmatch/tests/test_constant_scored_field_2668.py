"""#2668: a column that never varies carries no signal, so scoring it only
relaxes the bar on the columns that do.

Sibling of #2526. There a field varies in the data but blocking guarantees
agreement WITHIN a block; here it does not vary at all, so no pair can disagree
on it. Either way the field is a constant offset with zero discriminative power.

The measured case is the goldengraph TINY fixture, whose `type` column is
`'concept'` on all ten rows. Scored at equal weight beside `name` it makes

    score = (name + 1.0) / 2      threshold 0.8  ->  the real bar on `name` is 0.6

and `ensemble("Beta Corp", "Delta LLC") = 0.7037` clears 0.6 comfortably, so two
different companies merged. Dropping the column and KEEPING the threshold puts
the bar back on `name` at 0.8, which is what auto-config picks when the useless
column is not in the frame at all.

**The no-rescale choice is the whole point of this rule and has a test below.**
#2526 rescales, because a blocking-agreeing field is genuinely informative
elsewhere and its calibrated contribution should survive. Rescaling here would
preserve the same absolute bar, which is precisely the bar that produced the
false merge -- it would be a no-op on every decision the rule exists to change.
"""
from __future__ import annotations

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.autoconfig import ColumnProfile, _drop_constant_scored_fields


def _prof(name: str, full_n_distinct: int | None, n_distinct: int | None = 1) -> ColumnProfile:
    """`full_n_distinct` is the verdict; `n_distinct` is only the sample flag."""
    return ColumnProfile(
        name=name, dtype="String", col_type="string", confidence=1.0,
        n_distinct=n_distinct, full_n_distinct=full_n_distinct,
    )


def _mk(fields, threshold=0.8, mk_type="weighted"):
    return MatchkeyConfig(
        name="fuzzy_match", type=mk_type, threshold=threshold,
        fields=[MatchkeyField(field=f, scorer="ensemble", weight=w) for f, w in fields],
    )


# ── the rule fires on a constant column ───────────────────────────────────

def test_constant_column_is_dropped():
    mks = [_mk([("name", 1.0), ("type", 1.0)])]
    _drop_constant_scored_fields(mks, [_prof("name", 10), _prof("type", 1)])
    assert [f.field for f in mks[0].fields] == ["name"]


def test_threshold_is_NOT_rescaled():
    """The load-bearing difference from #2526. Rescaling 0.8 over the surviving
    half would give 0.6 -- the exact bar that let "Beta Corp"/"Delta LLC" (0.7037)
    merge. Keeping 0.8 is what makes this rule change any decision at all."""
    mks = [_mk([("name", 1.0), ("type", 1.0)], threshold=0.8)]
    _drop_constant_scored_fields(mks, [_prof("name", 10), _prof("type", 1)])
    assert mks[0].threshold == 0.8


def test_zero_distinct_counts_as_constant():
    """An all-null/all-blank column distinguishes nothing either."""
    mks = [_mk([("name", 1.0), ("empty", 1.0)])]
    _drop_constant_scored_fields(mks, [_prof("name", 10), _prof("empty", 0)])
    assert [f.field for f in mks[0].fields] == ["name"]


# ── and leaves everything else exactly alone ──────────────────────────────

def test_varying_column_is_kept():
    mks = [_mk([("name", 1.0), ("city", 1.0)])]
    _drop_constant_scored_fields(mks, [_prof("name", 10), _prof("city", 4)])
    assert [f.field for f in mks[0].fields] == ["name", "city"]


def test_unknown_full_count_is_left_alone():
    """"Cannot answer" must not become "assumed constant". Hand-built profiles
    and any future producer that does not populate the field keep their prior
    behaviour rather than acquiring a verdict on evidence nobody collected."""
    mks = [_mk([("name", 1.0), ("type", 1.0)])]
    _drop_constant_scored_fields(mks, [_prof("name", None), _prof("type", None)])
    assert [f.field for f in mks[0].fields] == ["name", "type"]


# ── the sample proposes, the full frame disposes ──────────────────────────

def test_near_constant_column_is_NOT_dropped():
    """The scale trap. `n_distinct` is a 1,000-row sample statistic, so a column
    that is 99.99% one value reads 1 there -- a value at frequency 1e-4 is missed
    ~90% of the time. Acting on the sample would drop the field on a 5M-row frame
    and keep it on a 2,000-row one: a verdict that flips with SCALE rather than
    with the data, which is what `full_cardinality_ratio` already exists to stop.

    It also fails in the same direction as the bug this rule fixes. A field that
    agrees on nearly every pair and disagrees only on the rare ones carries its
    whole signal in exactly those rare pairs, so dropping it removes the penalty
    precisely where it discriminates and the rare cross-entity pair merges."""
    mks = [_mk([("name", 1.0), ("tenant", 1.0)])]
    # sample saw one value; the full frame has two.
    _drop_constant_scored_fields(
        mks, [_prof("name", 10), _prof("tenant", 2, n_distinct=1)])
    assert [f.field for f in mks[0].fields] == ["name", "tenant"]


def test_sample_flag_alone_never_decides():
    """Belt and braces: a sampled n_distinct of 1 with NO full-frame count is
    "cannot answer", not "constant"."""
    mks = [_mk([("name", 1.0), ("tenant", 1.0)])]
    _drop_constant_scored_fields(
        mks, [_prof("name", 10), _prof("tenant", None, n_distinct=1)])
    assert [f.field for f in mks[0].fields] == ["name", "tenant"]


def test_never_empties_a_matchkey():
    """A matchkey with no fields cannot score anything, so an all-constant
    matchkey is left intact and the RED verdict is left to say so."""
    mks = [_mk([("a", 1.0), ("b", 1.0)])]
    _drop_constant_scored_fields(mks, [_prof("a", 1), _prof("b", 1)])
    assert [f.field for f in mks[0].fields] == ["a", "b"]


def test_non_weighted_matchkey_untouched():
    # threshold=None: a probabilistic matchkey cuts on `link_threshold`, and
    # setting `threshold` on one warns (#2483). Irrelevant to this rule.
    mks = [_mk([("name", 1.0), ("type", 1.0)], threshold=None, mk_type="probabilistic")]
    _drop_constant_scored_fields(mks, [_prof("name", 10), _prof("type", 1)])
    assert [f.field for f in mks[0].fields] == ["name", "type"]


def test_no_constants_is_a_no_op():
    mks = [_mk([("name", 1.0), ("city", 1.0)])]
    before = [(f.field, f.weight) for f in mks[0].fields]
    _drop_constant_scored_fields(mks, [_prof("name", 10), _prof("city", 5)])
    assert [(f.field, f.weight) for f in mks[0].fields] == before


# ── end to end, on the shape that caused the regression ───────────────────

def test_end_to_end_constant_column_does_not_cause_a_cross_entity_merge():
    """The goldengraph regression in miniature: two companies whose names score
    0.70 against each other, beside a column that is the same word everywhere.
    With the constant column scored, the pair clears the halved bar and merges."""
    import goldenmatch as gm
    import pyarrow as pa

    names = ["Beta Corp", "Beta Corporation", "BETA",
             "Delta LLC", "Delta", "Gamma Ltd", "Gamma Limited"]
    df = pa.table({"name": names, "type": ["concept"] * len(names)})

    cfg = gm.auto_configure_df(df, allow_red_config=True)
    scored = {f.field for mk in (cfg.matchkeys or []) for f in (mk.fields or [])}
    assert "type" not in scored, "the constant column must not be scored"

    res = gm.dedupe_df(df)
    beta = {0, 1, 2}
    delta = {3, 4}
    for info in res.clusters.values():
        members = {int(m) for m in info["members"]}
        assert not (members & beta and members & delta), (
            f"Beta and Delta merged into one cluster: {sorted(members)}"
        )
