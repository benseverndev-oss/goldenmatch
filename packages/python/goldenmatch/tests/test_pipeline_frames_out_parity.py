"""SP-B Task 2: in-memory dedupe pipeline frames-out parity.

When ``GOLDENMATCH_CLUSTER_FRAMES_OUT`` is ON, ``_run_dedupe_pipeline``
consumes the SP-A ``ClusterFrames`` for the GOLDEN + STATS + DUPES legs
(identity stays on the dict -- Task 3). This file locks that the gate-ON
output is byte-identical to the gate-OFF (dict) path on those legs.

Asserted byte-identical (gate ON vs OFF), on a fixture that exercises
singletons, a 2-member cluster, a weak chain, and an oversized cluster
(via a small ``max_cluster_size`` over a dense block):

- golden records: equal by content, member/list fields compared as a SET
  (the from-frames join reorders within a cluster);
- ``dupe_row_ids``: equal as a set (derived from ``results["clusters"]``,
  which the pipeline rebuilds from the frames under gate-ON);
- stats: ``cluster_count``, multi-member count, oversized count, and
  ``cluster_sizes`` (as a sorted multiset).

Native is parametrized ["1", "0"] with a skip guard when the kernel is
absent (mirrors ``tests/test_cluster_frames_out_parity.py``). A
provenance=True run covers the golden SLOW-path branch.

The box hangs on ``import goldenmatch`` / ``import polars`` locally; this
file is validated via ruff + py_compile and runs for real in CI.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)
from goldenmatch.core.pipeline import run_dedupe_df


def _skip_if_no_native(native):
    if native == "1":
        from goldenmatch.core._native_loader import native_module

        nm = native_module()
        if nm is None or getattr(nm, "build_clusters_arrow", None) is None:
            pytest.skip("native cluster kernel absent; native=1 validated in CI")


def _fixture_df():
    """Synthetic shape with the four cluster archetypes.

    - dense block of 8 near-identical "Aaa"/"10001" rows -> one big cluster
      that goes oversized when max_cluster_size is small;
    - 2-member "Bob Brown" cluster;
    - weak chain "Carl/Carla/Karl Carter" (one looser edge);
    - two singletons (Dana, Evan).
    """
    import polars as pl

    first = (
        ["Aaron", "Aaron", "Aron", "Aaron", "Aaran", "Aaron", "Aaron", "Aaron"]
        + ["Bob", "Bobby"]
        + ["Carl", "Carla", "Karl"]
        + ["Dana", "Evan"]
    )
    last = (
        ["Aaaa"] * 8
        + ["Brown", "Brown"]
        + ["Carter", "Carter", "Carter"]
        + ["Dixon", "Ellis"]
    )
    zips = (
        ["10001"] * 8
        + ["20002", "20002"]
        + ["30003", "30003", "30003"]
        + ["40004", "50005"]
    )
    return pl.DataFrame({"first_name": first, "last_name": last, "zip": zips})


def _config(*, max_cluster_size: int, provenance: bool):
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_name_zip",
                fields=[
                    MatchkeyField(
                        column="last_name",
                        transforms=["lowercase", "strip"],
                        scorer="jaro_winkler",
                        weight=0.4,
                    ),
                    MatchkeyField(
                        column="first_name",
                        transforms=["lowercase", "strip"],
                        scorer="jaro_winkler",
                        weight=0.3,
                    ),
                    MatchkeyField(
                        column="zip",
                        transforms=["strip"],
                        scorer="exact",
                        weight=0.3,
                    ),
                ],
                comparison="weighted",
                threshold=0.7,
            ),
        ],
        output=OutputConfig(
            format="csv",
            run_name="frames_out_parity",
            lineage_provenance=provenance,
        ),
        golden_rules=GoldenRulesConfig(
            default=GoldenFieldRule(strategy="most_complete"),
            max_cluster_size=max_cluster_size,
            weak_cluster_threshold=0.85,
            auto_split=True,
        ),
    )


def _run(monkeypatch, *, frames_out: str, max_cluster_size: int, provenance: bool):
    if frames_out == "1":
        monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    else:
        monkeypatch.delenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", raising=False)
    cfg = _config(max_cluster_size=max_cluster_size, provenance=provenance)
    return run_dedupe_df(_fixture_df(), cfg, source_name="t")


def _cluster_stats(results):
    clusters = results["clusters"]
    cluster_count = len(clusters)
    multi = sum(1 for c in clusters.values() if c["size"] > 1)
    oversized = sum(1 for c in clusters.values() if c["oversized"])
    sizes = sorted(c["size"] for c in clusters.values())
    return cluster_count, multi, oversized, sizes


def _dupe_row_ids(results):
    clusters = results["clusters"]
    out: set[int] = set()
    for c in clusters.values():
        if c["size"] > 1:
            out.update(int(m) for m in c["members"])
    return out


def _golden_as_setrows(results):
    """Golden frame -> a comparable, order-independent structure.

    The from-frames join reorders rows within a cluster, so list-valued
    survivorship fields can differ in element order. Compare the set of
    rows keyed by content with any list cell normalized to a frozenset.
    """
    golden = results.get("golden")
    if golden is None:
        return None
    rows = []
    for row in golden.iter_rows(named=True):
        norm = {}
        for k, v in row.items():
            norm[k] = frozenset(v) if isinstance(v, list) else v
        rows.append(tuple(sorted(norm.items(), key=lambda kv: kv[0])))
    return sorted(rows, key=repr)


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_out_pipeline_parity(monkeypatch, native):
    """Golden + dupes + stats byte-identical, gate ON vs OFF (fast golden)."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    _skip_if_no_native(native)

    off = _run(monkeypatch, frames_out="0", max_cluster_size=4, provenance=False)
    on = _run(monkeypatch, frames_out="1", max_cluster_size=4, provenance=False)

    # Stats parity.
    assert _cluster_stats(on) == _cluster_stats(off)
    # An oversized cluster must actually be exercised by this fixture/config.
    assert _cluster_stats(off)[2] >= 1, "fixture did not produce an oversized cluster"

    # Dupes parity (oversized-INCLUDED, like the dict path).
    assert _dupe_row_ids(on) == _dupe_row_ids(off)

    # Golden parity (content equal; member/list fields as a set).
    assert _golden_as_setrows(on) == _golden_as_setrows(off)


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_out_pipeline_parity_provenance_slow(monkeypatch, native):
    """Same parity with provenance=True so the golden SLOW path is covered."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    _skip_if_no_native(native)

    off = _run(monkeypatch, frames_out="0", max_cluster_size=4, provenance=True)
    on = _run(monkeypatch, frames_out="1", max_cluster_size=4, provenance=True)

    assert _cluster_stats(on) == _cluster_stats(off)
    assert _dupe_row_ids(on) == _dupe_row_ids(off)
    assert _golden_as_setrows(on) == _golden_as_setrows(off)
