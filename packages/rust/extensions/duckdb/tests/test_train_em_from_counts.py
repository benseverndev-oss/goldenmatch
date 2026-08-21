"""DuckDB trains Fellegi-Sunter from COUNTED comparison vectors.

Phase 3 of docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md.

`goldenmatch_train_em` takes ROWS and samples pairs from them, so its training
set is capped by the sampler no matter how much data exists. This one starts
from vectors the engine has already counted -- the shape a `GROUP BY` produces
-- so the input stops being a sample.

The anchors are the SAME numbers
`packages/rust/extensions/score-core/tests/fixtures/em_counts_parity.json`
carries, emitted from the Python reference. Every surface that trains from
counts asserts against those, so they cannot each agree with their own copy and
nothing else.
"""
from __future__ import annotations

import json

import duckdb
import goldenmatch_duckdb
import pytest

MK = json.dumps(
    {
        "name": "fs",
        "type": "probabilistic",
        "fields": [
            {"field": "first", "scorer": "jaro_winkler", "levels": 2,
             "partial_threshold": 0.8},
            {"field": "last", "scorer": "jaro_winkler", "levels": 2,
             "partial_threshold": 0.8},
        ],
    }
)
U = json.dumps({"first": [0.9, 0.1], "last": [0.85, 0.15]})


@pytest.fixture()
def con():
    c = duckdb.connect()
    goldenmatch_duckdb.register(c)
    return c


def _train(con, counts, u=U, params="{}", mk=MK):
    (out,) = con.execute(
        "SELECT goldenmatch_train_em_from_counts(?, ?, ?, ?)",
        [mk, json.dumps(counts), u, params],
    ).fetchone()
    return json.loads(out)


def test_counted_training_matches_the_shared_anchors(con):
    """Fixture case `two_level_learnable_only`."""
    em = _train(con, [[1, 1, 500], [0, 1, 300], [1, 0, 150], [0, 0, 50]])

    assert "error" not in em, em
    assert em["match_weights"]["first"][0] == pytest.approx(-1.374233, abs=1e-5)
    assert em["match_weights"]["first"][1] == pytest.approx(2.706681, abs=1e-5)
    # Field 1 too: asserting only field 0 would pass with the second field's
    # weights dropped or copied from the first.
    assert em["match_weights"]["last"][0] == pytest.approx(-2.111677, abs=1e-5)
    assert em["match_weights"]["last"][1] == pytest.approx(2.421028, abs=1e-5)


def test_a_conditioned_field_takes_the_bounded_ramp(con):
    """Fixture case `near_unique_blocking_field_1836`.

    A near-unique blocking key whose u is LEARNED collapses toward the smoothing
    floor, which explodes log2(m/u) past 20 bits and lets one field dominate
    every other (measured F1 0.83 -> 0.57). Every wrong variant still returns a
    valid probability vector, which is what makes it worth pinning here rather
    than trusting it to hold across a JSON boundary.
    """
    em = _train(
        con, [[1, 1, 500], [0, 1, 300]],
        u=json.dumps({"first": [0.9, 0.1], "last": [0.999, 0.001]}),
        params=json.dumps({"conditioned_fields": ["last"]}),
    )

    assert "error" not in em, em
    assert em["match_weights"]["last"] == [-3.0, 3.0]
    assert em["u_probs"]["last"] == [0.5, 0.5]
    assert em["match_weights"]["first"] != [-3.0, 3.0]


def test_the_counts_are_counts_not_proportions(con):
    """The same SHAPE at 1/100th the counts must NOT give the same model.

    EM's 1e-6 smoothing is additive, so its pull shrinks as the totals grow. A
    boundary that normalised the counts on the way through would pass every
    other test here and shift the low-probability cells -- which is where FS
    weights are largest.
    """
    big = _train(con, [[1, 1, 500], [0, 1, 300], [1, 0, 150], [0, 0, 50]])
    small = _train(con, [[1, 1, 5], [0, 1, 3], [1, 0, 1], [0, 0, 1]])

    assert big["match_weights"]["first"][0] != pytest.approx(
        small["match_weights"]["first"][0], abs=1e-6
    )


def test_it_trains_from_a_real_GROUP_BY(con):
    """The shape this exists for: counts produced by the engine, not by hand.

    Hand-written literals would never catch a boundary that mis-parses what
    `json_group_array` actually emits, which is the only thing standing between
    a DuckDB aggregation and the trainer.
    """
    con.execute(
        "CREATE TABLE gammas AS SELECT * FROM (VALUES "
        "(1,1),(1,1),(1,1),(0,1),(0,1),(1,0),(0,0)) AS t(g_first, g_last)"
    )
    (counts_json,) = con.execute(
        "SELECT json_group_array(json_array(g_first, g_last, n)) FROM ("
        "  SELECT g_first, g_last, count(*) AS n FROM gammas"
        "  GROUP BY g_first, g_last)"
    ).fetchone()
    (out,) = con.execute(
        "SELECT goldenmatch_train_em_from_counts(?, ?, ?, '{}')",
        [MK, counts_json, U],
    ).fetchone()
    em = json.loads(out)

    assert "error" not in em, em
    assert set(em["m_probs"]) == {"first", "last"}
    assert len(em["match_weights"]["first"]) == 2


def test_missing_u_is_refused_rather_than_invented(con):
    """u comes from RANDOM unblocked pairs, which counted vectors never contain.

    Defaulting it would produce weights wrong in a direction nobody could spot
    from the m estimates.
    """
    em = _train(con, [[1, 1, 5]], u="{}")
    assert "u_probs_json is required" in em["error"]


def test_a_malformed_counts_row_is_an_envelope_not_a_crash(con):
    """Fail-soft, like every other UDF in this package: a bad call inside a
    larger query returns an error object rather than aborting it."""
    em = _train(con, [[1]])
    assert "each counts row" in em["error"]


def test_negative_evidence_is_still_refused(con):
    """NE needs a per-pair matrix nothing outside the pair loop can rebuild.

    The refusal has to survive the JSON boundary -- a config whose NE fields
    were dropped in parsing would train silently and return a model the config
    never asked for.
    """
    mk = json.loads(MK)
    mk["negative_evidence"] = [
        {"field": "dob", "scorer": "exact", "threshold": 0.9}
    ]
    em = _train(con, [[1, 1, 5]], mk=json.dumps(mk))
    assert "negative-evidence" in em["error"]
