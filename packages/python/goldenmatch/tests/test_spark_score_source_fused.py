"""Scoring without ever materialising a candidate frame.

`generate_candidates` -> `score_candidates` builds the block self-join,
projects it to `(a, b)`, then joins both sides back to the source to recover
the columns that projection dropped. MEASURED on the 100M lane, the resulting
join stage holds 62% of all executor time in the job.

`score_source` scores inside the block join instead. The gate is that it
returns the SAME (a, b, score) rows -- exactly, including the float -- because
a scoring path that disagreed with the one training was fitted on would be a
silent quality change, not a visible failure.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

_ID = "__row_id__"
_COLS = [_ID, "first", "last", "city", "zip"]

_ROWS = [
    (0, "jon", "smith", "york", "10001"),
    (1, "john", "smith", "york", "10001"),   # blocks under BOTH passes
    (2, "jonathan", "smyth", "york", "99999"),
    (3, "amy", "wong", "leeds", "10001"),
    (4, "amy", "wong", "leeds", None),
    (5, "ann", "webb", None, "10001"),
    (6, "bob", "hale", "hull", None),
    (7, "jon", "smith", "york", "10001"),    # a third member of the 0/1 block
    # Null comparison values: `comparable` is judged from the RAW columns, so a
    # pair whose only evidence is shared missingness must NOT score 1.0. If the
    # fused frame resolved a column to the wrong side this is where it shows.
    (8, None, "smith", "york", "10001"),
]


def _config(n_matchkeys: int = 1) -> GoldenMatchConfig:
    mks = [
        MatchkeyConfig(
            name="mk1",
            type="weighted",
            fields=[
                MatchkeyField(field="first", scorer="exact", weight=1.0),
                MatchkeyField(field="last", scorer="exact", weight=2.0),
            ],
            threshold=0.3,
        )
    ]
    if n_matchkeys > 1:
        # A SECOND matchkey exercises the `greatest`-over-nulls combine that
        # replaced the pair-sized groupBy. With one matchkey that combine is a
        # no-op and the test would prove nothing about it.
        mks.append(
            MatchkeyConfig(
                name="mk2",
                type="weighted",
                fields=[MatchkeyField(field="zip", scorer="exact", weight=1.0)],
                threshold=0.9,
            )
        )
    return GoldenMatchConfig(
        matchkeys=mks,
        blocking=BlockingConfig(
            keys=[
                BlockingKeyConfig(fields=["city"]),
                BlockingKeyConfig(fields=["zip"]),
            ]
        ),
    )


@pytest.fixture(scope="module")
def source(spark):
    return spark.createDataFrame(_ROWS, _COLS)


def _rows(frame) -> set[tuple[int, int, float]]:
    return {
        (int(r["a"]), int(r["b"]), round(float(r["score"]), 12))
        for r in frame.select("a", "b", "score").collect()
    }


@pytest.mark.parametrize("n_matchkeys", [1, 2])
def test_score_source_returns_the_same_rows_as_the_pair_frame_path(
    source, n_matchkeys
):
    from goldenmatch.spark.config_pipeline import (
        generate_candidates,
        score_candidates,
        score_source,
    )

    cfg = _config(n_matchkeys)
    cands = generate_candidates(source, cfg, id_col=_ID)
    want = _rows(score_candidates(cands, source, cfg, id_col=_ID))
    got = _rows(score_source(source, cfg, id_col=_ID))

    assert got == want
    assert want, "fixture scored nothing -- the comparison is vacuous"


def test_score_source_plans_no_join_onto_the_pair_frame(source):
    """The claim, as a plan property: one join per blocking pass and no more.

    The pair-frame path plans three joins per pass -- the block self-join, then
    one back to each side. Counting joins is what distinguishes them; wall clock
    needs a cluster and is not claimed here.
    """
    import contextlib
    import io

    from goldenmatch.spark.config_pipeline import (
        generate_candidates,
        score_candidates,
        score_source,
    )

    cfg = _config()
    sess = source.sparkSession
    prior = sess.conf.get("spark.sql.autoBroadcastJoinThreshold", "10485760")
    # Ten rows broadcast, and a BroadcastHashJoin is not the plan this claim is
    # about -- at 100M nothing broadcasts.
    sess.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")
    try:
        def plan(frame) -> str:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                frame.explain(True)
            return buf.getvalue()

        legacy = plan(
            score_candidates(
                generate_candidates(source, cfg, id_col=_ID), source, cfg, id_col=_ID
            )
        )
        fused = plan(score_source(source, cfg, id_col=_ID))
    finally:
        sess.conf.set("spark.sql.autoBroadcastJoinThreshold", prior)

    if "Exchange" not in legacy:
        pytest.skip("backend does not plan Spark exchanges")

    n_pass = 2
    assert fused.count("SortMergeJoin") == n_pass, (
        f"expected one join per pass, got {fused.count('SortMergeJoin')}\n{fused}"
    )
    assert fused.count("SortMergeJoin") < legacy.count("SortMergeJoin")
    assert fused.count("Exchange ") < legacy.count("Exchange ")
