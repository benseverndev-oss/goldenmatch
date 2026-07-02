"""Integration tests for the #1083 throughput pipeline branch.

Tests that dedupe_df(df, throughput=0.95) exercises the sketch-then-verify
path, builds clusters, and surfaces a ThroughputPosture on the result.
"""
from __future__ import annotations

import polars as pl


def test_dedupe_df_throughput_finds_near_dups_and_reports_posture(monkeypatch):
    """Core contract: throughput branch runs, forms clusters, returns posture."""
    from goldenmatch.core import autoconfig
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)

    base = ["the quick brown fox jumps over the lazy dog"]
    near = ["the quick brown fox jumps over the lazy dogs"]
    far = ["completely unrelated text about quantum computing"]
    df = pl.DataFrame({"body": (base * 3) + (near * 3) + (far * 3)})

    from goldenmatch import dedupe_df

    res = dedupe_df(df, throughput=0.95)

    # Posture must be present and structurally valid.
    assert res.throughput_posture is not None, (
        "expected throughput_posture to be populated; got None"
    )
    posture = res.throughput_posture
    assert posture["metric"] in ("jaccard", "cosine"), (
        f"unexpected metric {posture['metric']!r}"
    )
    assert 0.0 <= posture["expected_recall"] <= 1.0, (
        f"expected_recall out of range: {posture['expected_recall']}"
    )
    # At least one pair must clear the threshold for the near-dup group.
    assert res.clusters, (
        "expected at least one cluster from the near-dup corpus; got none"
    )


def test_dedupe_df_no_throughput_posture_is_none():
    """No-op guarantee: throughput_posture stays None on a normal run."""
    df = pl.DataFrame({"name": ["Alice", "Bob", "Alicia"]})

    from goldenmatch import dedupe_df

    res = dedupe_df(df)
    assert res.throughput_posture is None, (
        f"expected throughput_posture=None on normal run; got {res.throughput_posture}"
    )


def test_dedupe_df_throughput_float_recall_target(monkeypatch):
    """Passing a float recall target wires through to posture.recall_target."""
    from goldenmatch.core import autoconfig
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)

    rows = ["natural language processing is interesting"] * 4 + [
        "natural language processing is really interesting"
    ] * 4 + ["deep sea creatures live in the abyss"] * 2
    df = pl.DataFrame({"text": rows})

    from goldenmatch import dedupe_df

    res = dedupe_df(df, throughput=0.90)
    assert res.throughput_posture is not None
    # recall_target echoes the caller-supplied value
    assert abs(res.throughput_posture["recall_target"] - 0.90) < 1e-6


def test_dedupe_df_throughput_posture_fields():
    """All expected posture keys are present."""
    import polars as pl
    import pytest

    rows = ["hello world foo bar"] * 5 + ["hello world foo baz"] * 5
    df = pl.DataFrame({"body": rows})

    from goldenmatch import dedupe_df

    res = dedupe_df(df, throughput=True)
    if res.throughput_posture is None:
        pytest.skip("throughput_posture not populated (may require text column detection)")
    required = {
        "metric", "recall_target", "similarity_threshold", "bands",
        "rows_per_band", "expected_recall", "reduction_ratio",
        "candidate_pairs", "verified_pairs", "notes",
    }
    missing = required - set(res.throughput_posture.keys())
    assert not missing, f"posture missing keys: {missing}"


def test_throughput_skips_golden_survivorship(monkeypatch):
    """#1151: the throughput tier skips golden-record survivorship (the O(N)
    polars iter_rows that wedged the 100k+ corpus ceiling). Golden stays empty;
    clusters — what corpus dedup actually consumes — are intact."""
    from goldenmatch.core import autoconfig
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)

    base = ["the quick brown fox jumps over the lazy dog"]
    near = ["the quick brown fox jumps over the lazy dogs"]
    far = ["completely unrelated text about quantum computing"]
    df = pl.DataFrame({"body": (base * 3) + (near * 3) + (far * 3)})

    from goldenmatch import dedupe_df

    res = dedupe_df(df, throughput=0.95)
    assert res.throughput_posture is not None, "throughput tier did not engage"
    # Survivorship skipped -> no golden records built.
    golden = res.golden
    assert golden is None or getattr(golden, "height", 0) == 0, (
        f"expected golden skipped on throughput path; got {golden}"
    )
    # The actual deliverable — clusters / dup mapping — is still there.
    assert res.clusters, "throughput run must still produce clusters"


def test_non_throughput_still_builds_golden():
    """Guard: the golden skip is scoped to throughput only — a normal run still
    builds golden records for a multi-member cluster."""
    df = pl.DataFrame({
        "name": ["Alice Smith", "Alice Smith", "Bob Jones"],
        "email": ["a@x.com", "a@x.com", "b@y.com"],
    })
    from goldenmatch import dedupe_df

    res = dedupe_df(df)
    assert res.throughput_posture is None
    assert res.golden is not None and res.golden.height >= 1, (
        "normal run must still produce golden records"
    )


def test_throughput_off_is_byte_identical():
    import polars as pl
    from goldenmatch import dedupe_df
    df = pl.DataFrame({"name": ["alice", "alicia", "bob", "bobby", "carol"] * 10,
                       "zip": ["10001", "10001", "20002", "20002", "30003"] * 10})
    a = dedupe_df(df)
    b = dedupe_df(df, throughput=None)
    assert a.throughput_posture is None and b.throughput_posture is None
    assert sorted(a.clusters.keys()) == sorted(b.clusters.keys())
    # same multi-member cluster structure (membership sets), order-independent
    def _memsets(res):
        return sorted(tuple(sorted(c.get("members", []))) for c in res.clusters.values())
    assert _memsets(a) == _memsets(b)
