"""The FS weight lookup must name the level ONCE, and answer identically.

The lookup used to be a CASE chain naming `level` once per level, and `level`
is the whole gamma expression with the jar scorer call inside it. Catalyst's
subexpression elimination does not hoist a UDF out of CASE branches, so the
scorer ran once per branch. MEASURED at 50M by splitting the score stage into
layers: the weight layer cost 2.71x the gamma layer beneath it, on a
three-level model.

These tests pin the two properties that fix depends on:

* the answer is UNCHANGED, including at `level = -1` and outside the trained
  range -- the cases a happy-path fixture never reaches and the ones an array
  index gets wrong if the offset is off by one;
* `level` appears ONCE in the expression, which is the whole point and is
  invisible to any value-based test.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

_WEIGHTS = [-2.5, 0.0, 1.25, 4.75]


def _reference(level: int, weights: list[float]) -> float:
    """What the CASE chain returned, written out plainly.

    Deliberately NOT the old expression: comparing an implementation against a
    copy of itself proves only that the copy is faithful. This states the
    contract -- index by level, unobserved and out-of-range both score zero.
    """
    from goldenmatch.spark.probabilistic import _UNOBSERVED_WEIGHT

    if not weights or level < 0 or level >= len(weights):
        return float(_UNOBSERVED_WEIGHT)
    return float(weights[level])


@pytest.mark.parametrize("weights", [_WEIGHTS, [1.0], []])
def test_weight_lookup_matches_the_contract_at_every_level(spark, weights):
    from goldenmatch.spark.probabilistic import _weight_lookup_expr
    from pyspark.sql import functions as F

    # -2 is not reachable from `fs_level_expr`, which emits -1 or 0..levels-1.
    # It is here because an array index that mishandles it would THROW rather
    # than return a wrong number, and a crash in the scoring path at some
    # unlucky scale is worse than the bug this fix removes.
    levels = [-2, -1, 0, 1, 2, 3, 4, 9]
    df = spark.createDataFrame([(int(x),) for x in levels], ["lvl"])
    got = {
        int(r["lvl"]): float(r["w"])
        for r in df.select(
            F.col("lvl"),
            _weight_lookup_expr(F.col("lvl"), list(weights)).alias("w"),
        ).collect()
    }
    for lv in levels:
        assert got[lv] == pytest.approx(_reference(lv, list(weights))), (
            f"level {lv} with weights={weights}: got {got[lv]}, "
            f"want {_reference(lv, list(weights))}"
        )


def test_the_level_expression_appears_exactly_once(spark):
    """The performance property, asserted structurally.

    A value test cannot see this: the CASE chain and the array index return the
    same numbers and differ only in how many times they evaluate the scorer.

    Counts references in the EXPRESSION rather than in a query plan. The plan
    also names the column in the relation's output schema, so counting there
    reports one reference too many -- which is exactly what the first version of
    this test did.
    """
    from goldenmatch.spark.probabilistic import _weight_lookup_expr
    from pyspark.sql import functions as F

    marker = "zz_marker_col"
    rendered = str(_weight_lookup_expr(F.col(marker), _WEIGHTS))
    if marker not in rendered:
        pytest.skip(f"backend does not render column names in exprs: {rendered!r}")

    n = rendered.count(marker)
    assert n == 1, (
        f"the level expression is named {n} times; it must appear once, or the "
        f"scorer inside it runs once per level.\nexpr: {rendered}"
    )


def test_the_old_case_chain_shape_would_fail_that_test(spark):
    """Guards the guard.

    A structural assertion that cannot fail is worse than none, because it reads
    as protection. This builds the shape the fix replaced and confirms the same
    counting rule rejects it.

    Takes the `spark` fixture only to have an active session: `F.col` asserts
    one exists before it will build a Column.
    """
    from pyspark.sql import functions as F

    marker = "zz_marker_col"
    level = F.col(marker)
    expr = F.when(level == F.lit(0), F.lit(0.0))
    for i, w in enumerate(_WEIGHTS[1:], start=1):
        expr = expr.when(level == F.lit(i), F.lit(float(w)))
    rendered = str(expr.otherwise(F.lit(0.0)))
    if marker not in rendered:
        pytest.skip("backend does not render column names in exprs")
    assert rendered.count(marker) == len(_WEIGHTS), (
        "the old shape did not name the level once per level, so the counting "
        "rule this test family relies on does not mean what it claims"
    )
