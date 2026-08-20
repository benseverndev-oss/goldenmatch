"""`gamma_frame` must produce exactly the levels `gamma_columns` produces.

The only difference between them is that `gamma_frame` NAMES each per-field
similarity before the level ladder reads it. The ladder sums
`when(sim >= t, 1)` once per threshold, so an inline similarity is evaluated
once per threshold -- and it contains the jar scorer call. MEASURED at 50M with
the layer aggregates matched, bucketing costs 38.57s inline and 2.21s
projected.

That is a performance change, so the gate is that the ANSWER is unchanged. A
level is the unit an FS model's weights are indexed by, so a level that shifted
would retrain the model against a different partition of the data and every
downstream number would still look plausible.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from goldenmatch.config.schemas import (  # noqa: E402
    MatchkeyConfig,
    MatchkeyField,
)

_ID = "__row_id__"
_COLS = [_ID, "first", "last"]

# Nulls on BOTH sides and on one side only: `observed` is judged from the raw
# columns, so these are the rows where a mishandled `keep` or a mis-ordered
# projection shows up as a level of -1 where it should be 0, or vice versa.
_ROWS = [
    (0, "jon", "smith"),
    (1, "jon", "smith"),
    (2, "jonathan", "smyth"),
    (3, "amy", None),
    (4, None, None),
    (5, "amy", "wong"),
]


def _mk(missing: str) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        missing=missing,
        fields=[
            MatchkeyField(field="first", scorer="exact", levels=2),
            MatchkeyField(field="last", scorer="exact", levels=2),
        ],
    )


@pytest.fixture(scope="module")
def joined(spark):
    """Every ordered pair, joined under the scoring aliases."""
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from pyspark.sql import functions as F

    src = spark.createDataFrame(_ROWS, _COLS)
    a, b = src.alias(CAND_LHS), src.alias(CAND_RHS)
    return a.join(b, F.col(f"{CAND_LHS}.{_ID}") < F.col(f"{CAND_RHS}.{_ID}"))


# BOTH missing modes: `-1` under `unobserved` and `0` under `disagree` are
# different levels, and a projection that dropped `observed` would still pass
# under one of them.
@pytest.mark.parametrize("missing", ["unobserved", "disagree"])
def test_gamma_frame_levels_equal_gamma_columns_levels(joined, missing):
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from goldenmatch.spark.em import gamma_columns, gamma_frame
    from pyspark.sql import functions as F

    mk = _mk(missing)
    names = [f"gamma_{f.resolved_field}" for f in mk.fields]

    want = sorted(
        tuple(int(r[n]) for n in names)
        for r in joined.select(
            *gamma_columns(mk, CAND_LHS, CAND_RHS)
        ).collect()
    )
    got = sorted(
        tuple(int(r[n]) for n in names)
        for r in gamma_frame(joined, mk, lhs=CAND_LHS, rhs=CAND_RHS)
        .select(*[F.col(n) for n in names])
        .collect()
    )

    assert got == want
    assert want, "no pairs -- the comparison is vacuous"
    # Not vacuous in the other direction either: an all-identical fixture would
    # make every level equal and hide a field-ordering bug.
    assert len({g for row in want for g in row}) > 1, "every level identical"


def test_keep_columns_survive_the_projection(joined):
    """`keep` exists because a column reading the SOURCE aliases cannot be added
    after the similarity projection -- by then those aliases are gone."""
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from goldenmatch.spark.em import gamma_frame
    from pyspark.sql import functions as F

    mk = _mk("unobserved")
    same = F.col(f"{CAND_LHS}.first") == F.col(f"{CAND_RHS}.first")
    out = gamma_frame(
        joined, mk, lhs=CAND_LHS, rhs=CAND_RHS, keep=[(same, "same_first")]
    )

    assert "same_first" in out.columns
    rows = out.select("same_first", f"gamma_{mk.fields[0].resolved_field}").collect()
    for r in rows:
        # `first` is an exact scorer at 2 levels, so agreement IS level 1 --
        # unless the field is unobserved on either side, which scores -1. This
        # ties the kept column to the level it should agree with, rather than
        # merely asserting the column exists.
        if r["same_first"] is True:
            assert int(r[f"gamma_{mk.fields[0].resolved_field}"]) in (1, -1)


def test_the_similarity_is_named_once_in_the_level_ladder(joined):
    """The performance property. `fs_level_expr` reads its similarity once per
    threshold, so the ladder must be given a NAME rather than an expression."""
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from goldenmatch.spark.em import gamma_frame

    mk = _mk("unobserved")
    out = gamma_frame(joined, mk, lhs=CAND_LHS, rhs=CAND_RHS)
    # The frame under the levels projects the similarities by name; if the
    # ladder had been handed expressions instead, no `__sim_` column would
    # exist anywhere in the plan.
    plan = out._jdf.queryExecution().analyzed().toString() if hasattr(out, "_jdf") else ""
    if not plan:
        pytest.skip("backend does not expose an analyzed plan")
    assert "__sim_0" in plan, f"similarities were not projected by name\n{plan}"
