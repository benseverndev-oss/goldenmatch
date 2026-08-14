"""#2540: detecting a frame that is several sources concatenated together.

`dedupe_df` compares pairs WITHIN one frame, so when that frame is two catalogues
stacked the within-source pairs can never be true matches -- 48.3% of DBLP-ACM's
candidate set and 73.4% of Abt-Buy's, measured. The detector reports that shape
and routes to `match_df`; it must never constrain matching itself.

The load-bearing property is CONTIGUITY. A genuine `pl.concat` leaves a step:
some column goes null, or changes value-shape, at one row boundary and stays
that way. Scattered single-source messiness must NOT look like that, or the
warning becomes noise on exactly the datasets dedupe is right for.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.concat_sources import (
    detect_concatenated_sources,
    warn_if_concatenated_sources,
)


def _stacked(n_a: int = 60, n_b: int = 40) -> pl.DataFrame:
    """Two sources with disjoint schemas, as `diagonal_relaxed` leaves them."""
    a = pl.DataFrame({
        "id": [f"a{i}" for i in range(n_a)],
        "name": [f"widget {i}" for i in range(n_a)],
    })
    b = pl.DataFrame({
        "id": [f"b{i}" for i in range(n_b)],
        "name": [f"widget {i}" for i in range(n_b)],
        "vendor": [f"vendor{i % 5}" for i in range(n_b)],
    })
    return pl.concat([a, b], how="diagonal_relaxed")


# ── it fires on the real shape ────────────────────────────────────────────────

def test_detects_a_null_step_from_disjoint_schemas():
    ev = detect_concatenated_sources(_stacked(60, 40))
    assert ev is not None
    assert "vendor" in ev.columns
    assert ev.kinds[ev.columns.index("vendor")] == "null"
    assert ev.boundary == 60, f"boundary should be the true split, got {ev.boundary}"


def test_detects_a_format_step_when_schemas_are_identical():
    """DBLP-ACM's shape: same columns, different id families."""
    df = pl.DataFrame({
        "id": [f"conf/vldb/p{i}" for i in range(70)] + [str(100000 + i) for i in range(50)],
        "title": [f"paper about topic {i}" for i in range(120)],
    })
    ev = detect_concatenated_sources(df)
    assert ev is not None
    assert "id" in ev.columns
    assert ev.kinds[ev.columns.index("id")] == "format"
    assert ev.boundary == 70


def test_tolerates_exceptions_within_a_block():
    """abt_buy's actual shape: `manufacturer` is null for every Abt row AND a
    handful of Buy rows. An exact two-run test misses that -- and did."""
    vendor = [None] * 60 + [f"v{i}" for i in range(40)]
    vendor[65], vendor[77], vendor[91] = None, None, None  # 3 stragglers past the split
    df = pl.DataFrame({"id": [f"r{i}" for i in range(100)], "vendor": vendor})
    ev = detect_concatenated_sources(df)
    assert ev is not None
    assert "vendor" in ev.columns
    assert abs(ev.boundary - 60) <= 2


# ── it stays silent where dedupe is the right tool ────────────────────────────

def test_silent_on_a_genuine_single_source_frame():
    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(200)],
        "name": [f"person {i % 50}" for i in range(200)],
        "city": [["Boston", "Denver", "Austin"][i % 3] for i in range(200)],
    })
    assert detect_concatenated_sources(df) is None


def test_silent_on_scattered_nulls():
    """The tolerance must not turn ordinary sparsity into a step. A column that
    is ~25% null at random is the common case dedupe is FOR."""
    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(200)],
        "phone": [None if i % 4 == 0 else f"555-{i:04d}" for i in range(200)],
    })
    assert detect_concatenated_sources(df) is None


def test_silent_when_one_side_is_too_small_to_be_a_source():
    """A 3-row tail is a data-entry artifact, not a second catalogue."""
    vendor = [None] * 197 + ["v1", "v2", "v3"]
    df = pl.DataFrame({"id": [f"r{i}" for i in range(200)], "vendor": vendor})
    assert detect_concatenated_sources(df) is None


def test_silent_on_a_tiny_frame():
    df = pl.DataFrame({"id": ["a", "b", "c"], "v": [None, None, "x"]})
    assert detect_concatenated_sources(df) is None


# ── it is advisory, and cannot break the caller ───────────────────────────────

def test_fail_open_on_garbage_input():
    assert detect_concatenated_sources(object()) is None
    assert detect_concatenated_sources(None) is None


def test_warn_logs_and_names_match_df(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.concat_sources"):
        ev = warn_if_concatenated_sources(_stacked(60, 40))
    assert ev is not None
    msg = " ".join(r.getMessage() for r in caplog.records
                   if r.name == "goldenmatch.core.concat_sources")
    assert "match_df" in msg, "the warning must route to the tool that owns linkage"
    assert "concatenated" in msg


def test_warn_is_silent_on_single_source(caplog):
    import logging
    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(200)],
        "name": [f"person {i % 50}" for i in range(200)],
    })
    with caplog.at_level(logging.WARNING, logger="goldenmatch.core.concat_sources"):
        assert warn_if_concatenated_sources(df) is None
    assert not [r for r in caplog.records if r.name == "goldenmatch.core.concat_sources"]


# ── striding must not lose the signal ─────────────────────────────────────────

def test_survives_striding_on_a_large_frame():
    """Large frames are strided to bound Python-level work. A step stays a step
    under uniform striding -- unlike head-sampling, which would see one source."""
    n_a, n_b = 30_000, 20_000
    df = pl.DataFrame({
        "id": [f"r{i}" for i in range(n_a + n_b)],
        "vendor": [None] * n_a + [f"v{i}" for i in range(n_b)],
    })
    ev = detect_concatenated_sources(df)
    assert ev is not None
    assert "vendor" in ev.columns
    # boundary is reported in original row space, within one stride
    assert abs(ev.boundary - n_a) <= (n_a + n_b) // 20_000 + 1
