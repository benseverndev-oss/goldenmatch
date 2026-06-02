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
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]
        ),
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


# ── SP-B Task 3: identity-enabled parity ──────────────────────────────────
#
# With identity ENABLED and GOLDENMATCH_CLUSTER_FRAMES_OUT ON, the dict that
# reaches resolve_clusters is rebuilt from the frames (cluster_frames_to_dict)
# and carries pair_scores={}. Without Task 3's ClusterPairScores view that dict
# yields ZERO evidence edges, so ResolveSummary.edges_added / conflicts_flagged
# diverge from the gate-OFF dict path. This test is the gate that catches that:
# it runs the pipeline gate-ON vs gate-OFF against identical seeded stores and
# asserts (a) the record->entity PARTITION is identical and (b) the
# ResolveSummary key counts match.


def _identity_df():
    """People shape with a strong 2-member cluster + a weak-bottleneck cluster.

    - Alice/Alyce Smith: strong fuzzy pair (same email) -> 2-member identity.
    - Carl / Carla / Karl Carter: a 3-member cluster anchored by a shared email
      (``c@y.com``, exact -> 0.3 of the weighted score) plus similar-but-NOT-
      identical names (Carl/Carla/Karl), so the in-cluster edges all clear the
      0.7 matchkey threshold (the cluster reliably forms) yet the cluster
      confidence (0.4*min_edge + 0.3*avg_edge + 0.3*connectivity) is strictly
      below 1.0 because no name pair is identical. Paired with the config's
      raised ``weak_confidence_threshold=0.99`` (see ``_identity_config``), that
      sub-1.0 confidence trips the resolver's weak-bottleneck branch, which
      emits a CONFLICTS_WITH edge (``conflicts_flagged >= 1``). That branch is
      the SECOND view-read site on frames-out: it calls
      ``pair_score_view.score_for(...)`` to fetch the bottleneck score (falling
      back to ``info["pair_scores"]`` only when the view is absent, i.e. the
      gate-OFF dict path).
    - Dave Singleton: lone record (singleton identity).

    ``source_pk_column="id"`` makes record ids deterministic (``src:<id>``),
    so the partition can be compared across two runs without depending on the
    random UUIDv7 entity-ids.
    """
    import polars as pl

    return pl.DataFrame({
        "id":    ["1", "2", "3", "4", "5", "6"],
        "name":  [
            "Alice Smith", "Alyce Smith",
            "Carl Carter", "Carla Carter", "Karl Carter",
            "Dave Singleton",
        ],
        "email": [
            "a@x.com", "a@x.com",
            "c@y.com", "c@y.com", "c@y.com",
            "d@z.com",
        ],
        "zip":   ["10001", "10001", "20002", "20002", "20002", "30003"],
    })


def _identity_config(identity_path: str, run_name: str):
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        IdentityConfig,
    )

    return GoldenMatchConfig(
        output=OutputConfig(run_name=run_name),
        matchkeys=[MatchkeyConfig(
            name="people_fuzzy",
            type="weighted",
            threshold=0.7,
            fields=[
                MatchkeyField(field="name",  scorer="jaro_winkler", weight=0.7),
                MatchkeyField(field="email", scorer="exact",        weight=0.3),
            ],
        )],
        blocking=BlockingConfig(strategy="static", keys=[
            BlockingKeyConfig(fields=["zip"]),
        ]),
        identity=IdentityConfig(
            enabled=True, path=identity_path, source_pk_column="id",
            dataset="people-test",
            # Raised so the weak-bottleneck branch fires reliably: any
            # multi-member cluster whose confidence (0.4*min_edge +
            # 0.3*avg_edge + 0.3*connectivity) is below this trips it. With the
            # matchkey threshold pinned at 0.7, in-cluster edges can't fall
            # below ~0.7, so the default 0.6 threshold is effectively
            # unreachable here. 0.99 makes the Carter cluster confidently
            # below-threshold (its confidence is strictly < 1.0 because the
            # Carl/Carla/Karl names are not identical), so the resolver's
            # weak-bottleneck `score_for(...)` view-read runs on frames-out.
            weak_confidence_threshold=0.99,
        ),
    )


def _record_partition(db_path: str, source: str, ids: list[str]):
    """record->entity PARTITION as a set of frozensets of record-ids.

    Entity-ids are random UUIDv7 (``new_entity_id``), so we never compare them
    literally across runs -- we group record-ids by their resolved entity and
    compare the resulting partition.
    """
    from goldenmatch.identity import IdentityStore

    groups: dict[str, set[str]] = {}
    with IdentityStore(path=db_path) as s:
        for rid in ids:
            record_id = f"{source}:{rid}"
            eid = s.find_entity_by_record(record_id)
            if eid is None:
                continue
            groups.setdefault(eid, set()).add(record_id)
    return {frozenset(members) for members in groups.values()}


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_out_identity_parity(monkeypatch, tmp_path, native):
    """Identity partition + ResolveSummary key counts identical, gate ON vs OFF.

    This is the Task 3 gate: catches the empty-evidence-edge bug on frames-out.
    """
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    _skip_if_no_native(native)

    df = _identity_df()
    ids = ["1", "2", "3", "4", "5", "6"]
    source = "src"

    # Gate OFF: identity off the dict path (real per-cluster pair_scores).
    off_db = str(tmp_path / "identity_off.db")
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", raising=False)
    off = run_dedupe_df(
        df, _identity_config(off_db, "off"), source_name=source,
    )

    # Gate ON: identity off the frames-rebuilt dict (pair_scores={} -> needs the
    # ClusterPairScores view that Task 3 builds).
    on_db = str(tmp_path / "identity_on.db")
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    on = run_dedupe_df(
        df, _identity_config(on_db, "on"), source_name=source,
    )

    assert off["identity_summary"] is not None
    assert on["identity_summary"] is not None

    # Primary gate: the record->entity PARTITION is identical (frozensets of
    # record-ids; entity-ids themselves are random and not compared).
    off_part = _record_partition(off_db, source, ids)
    on_part = _record_partition(on_db, source, ids)
    assert on_part == off_part
    # The fixture must actually produce a multi-member identity (else the
    # evidence-edge path is never exercised).
    assert any(len(g) > 1 for g in off_part), "fixture produced no multi-member identity"

    # Secondary gate: ResolveSummary key counts match. edges_added +
    # conflicts_flagged are the counts that collapse to 0 under the bug.
    assert on["identity_summary"] == off["identity_summary"]
    assert off["identity_summary"]["edges_added"] >= 1, (
        "fixture produced no evidence edges"
    )
    # The fixture must also exercise the resolver's weak-bottleneck branch (the
    # SECOND view-consumption site on frames-out, via pair_score_view.score_for).
    # Assert on the gate-OFF side -- the bug-free dict reference -- so the gate
    # catches a frames-out view that mishandles the bottleneck score lookup.
    # (on == off above already locks the gate-ON count to this same value.)
    assert off["identity_summary"]["conflicts_flagged"] >= 1, (
        "fixture did not trip the weak-bottleneck branch (score_for not exercised)"
    )
