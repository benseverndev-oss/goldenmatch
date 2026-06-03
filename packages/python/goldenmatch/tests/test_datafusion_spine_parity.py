"""Stage C semantic-parity gate: the DataFusion spine vs the in-memory
frames-out path.

``run_spine`` threads ``score -> dedup`` through ONE DataFusion
``SessionContext`` then mirrors the in-memory frames-out tail
(``build_cluster_frames`` -> ``ClusterPairScores.from_frames`` ->
``build_golden_records_from_frames``). This file locks that the spine is
SEMANTICALLY identical to an in-memory comparand that shares the SAME
clustering/golden/id_prep functions and differs ONLY in the score+dedup
engine:

  (a) partition parity -- Rand 1.0: the frozenset-of-``__row_id__`` per
      cluster is identical (compared as a SET of member-sets, so the
      arbitrary entity-id / cluster-id labels never matter; singletons
      are size-1 clusters on both sides, which build_cluster_frames
      emits);
  (b) golden content -- equal per multi-member, non-oversized cluster
      (the from-frames join reorders within a cluster, so list cells are
      compared as frozensets);
  (c) id_prep -- ``ClusterPairScores.from_frames(assignments, raw_pairs)``
      yields identical ``for_cluster(cid)`` edge sets per cluster.

The comparand scores via ``score_blocks_datafusion`` (the Stage-B backend
reference, B1 native UDF) while the spine scores via the Stage-B FFI
ScalarUDFs. The fixture uses ``jaro_winkler`` only, whose FFI form
(goldenmatch-score-core) and native form are both rapidfuzz-equal
(``test_native_parity`` / ``test_datafusion_ffi_udf`` prove 1e-9), so the
RAW pair sets are identical and any partition/golden/edge divergence is a
spine-orchestration bug -- exactly what this gate must catch.

``pyarrow`` + ``datafusion`` are soft deps (skip if absent);
``goldenmatch_datafusion_udf`` is importorskip too so the file SKIPS where
the FFI wheel isn't built -- but the goldenmatch CI lane builds that crate,
so it RUNS for real there. The box hangs on ``import goldenmatch`` locally;
this file is validated via ruff + py_compile and runs in CI.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")  # noqa: F841
datafusion = pytest.importorskip("datafusion")  # noqa: F841
pytest.importorskip("goldenmatch_datafusion_udf")

from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)


def _fixture_df():
    """Synthetic shape exercising the cluster archetypes the parity gate
    cares about: a 2-member cluster, a 3-member chain, and two singletons,
    plus a small ``max_cluster_size`` over a dense block to force an
    oversized cluster (golden EXCLUDES it; partition + id_prep INCLUDE it).
    """
    import polars as pl

    last = (
        ["Aaaa", "Aaaa", "Aaaa", "Aaaa", "Aaaa"]   # dense -> oversized
        + ["Brown", "Brown"]                         # 2-member
        + ["Carter", "Carter", "Carter"]             # 3-member chain
        + ["Dixon", "Ellis"]                         # 2 singletons
    )
    zips = (
        ["10001"] * 5
        + ["20002", "20002"]
        + ["30003", "30003", "30003"]
        + ["40004", "50005"]
    )
    return pl.DataFrame({"last_name": last, "zip": zips})


def _config(*, max_cluster_size: int) -> GoldenMatchConfig:
    """Single-field weighted matchkey on ``last_name`` (jaro_winkler, NO
    transforms -- the DataFusion score path reads the RAW resolved field,
    so the comparand and spine both score raw ``last_name``). Block on
    ``zip`` so each archetype lands in its own block.
    """
    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_last",
                fields=[
                    MatchkeyField(
                        column="last_name",
                        transforms=[],
                        scorer="jaro_winkler",
                        weight=1.0,
                    ),
                ],
                comparison="weighted",
                threshold=0.85,
            ),
        ],
        output=OutputConfig(
            format="csv",
            run_name="spine_parity",
            lineage_provenance=False,
        ),
        golden_rules=GoldenRulesConfig(
            default=GoldenFieldRule(strategy="most_complete"),
            max_cluster_size=max_cluster_size,
            weak_cluster_threshold=0.3,
            # auto_split OFF so the dense block stays a persistent oversized
            # cluster -> golden exclusion exercised on both sides.
            auto_split=False,
        ),
    )


def _prepared_blocks(df, config):
    """Build ``blocked_candidates`` the way the pipeline does for a fuzzy
    matchkey: add ``__row_id__``, precompute matchkey transforms, then
    ``build_blocks`` over the static blocking config. Returns the block
    list both the spine and the comparand consume (identical input)."""
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.matchkey import precompute_matchkey_transforms

    with_ids = df.with_row_index("__row_id__")
    matchkeys = config.get_matchkeys()
    augmented = precompute_matchkey_transforms(with_ids, matchkeys)
    # Keep blocks LAZY (the production BlockResult.df shape). Both the spine
    # (_materialize_blocks_to_arrow / _all_ids_from_blocks / _slim_golden_source)
    # and the comparand collect internally / via the robust helpers.
    return build_blocks(augmented.lazy(), config.blocking)


def _inmemory_comparand(blocks, config):
    """Score via the Stage-B DataFusion backend (B1 native UDF) then run
    the SAME frames-out tail the spine runs -- the semantic reference.
    Returns ``(golden_df, assignments, raw_pairs)``.
    """
    import polars as pl
    from goldenmatch.backends.datafusion_spine import (
        _all_ids_from_blocks,
        _golden_rules_knobs,
        _slim_golden_source,
    )
    from goldenmatch.core.cluster import build_cluster_frames
    from goldenmatch.core.golden import build_golden_records_from_frames
    from rapidfuzz.distance import JaroWinkler

    # Independent reference scorer: brute-force within-block pairs via python
    # rapidfuzz jaro_winkler (== the spine's FFI jaro_winkler UDF;
    # test_native_parity / test_datafusion_ffi_udf prove rapidfuzz-equal at
    # 1e-9). NO _native, NO DataFusion -> any pair/cluster divergence is a
    # spine score+dedup orchestration bug. Block on `zip`, no transforms, so
    # the scored field is the RAW matchkey column (same value the spine's
    # self-join scores).
    mk = config.get_matchkeys()[0]
    field = mk.fields[0].column
    threshold = mk.threshold
    best: dict[tuple[int, int], float] = {}  # canonical (a<b) -> MAX score
    for b in blocks:
        bdf = b.df.collect() if isinstance(b.df, pl.LazyFrame) else b.df
        ids = bdf["__row_id__"].cast(pl.Int64).to_list()
        vals = bdf[field].to_list()
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                s = JaroWinkler.normalized_similarity(vals[i] or "", vals[j] or "")
                if s >= threshold:
                    a, c = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
                    if (a, c) not in best or s > best[(a, c)]:
                        best[(a, c)] = s
    raw_pairs = [(a, c, s) for (a, c), s in best.items()]

    # all_ids via the SAME helper the spine uses (LazyFrame-robust + identical
    # on both sides for parity).
    all_ids = _all_ids_from_blocks(blocks)

    max_cs, weak, auto_split, golden_rules = _golden_rules_knobs(config)
    frames = build_cluster_frames(
        raw_pairs, all_ids,
        max_cluster_size=max_cs,
        weak_cluster_threshold=weak,
        auto_split=auto_split,
    )
    golden_df, _ = build_golden_records_from_frames(
        _slim_golden_source(blocks),
        frames,
        golden_rules,
        quality_scores=None,
        provenance=config.output.lineage_provenance,
    )
    return golden_df, frames.assignments, raw_pairs


def _partition(assignments):
    """assignments frame -> a SET of frozensets of member ids (one per
    cluster). Label-independent: cluster_id / entity_id values never enter,
    so this is the Rand-1.0 partition comparand."""
    import polars as pl

    if assignments is None or assignments.height == 0:
        return set()
    grouped = assignments.group_by("cluster_id").agg(
        pl.col("member_id").alias("members")
    )
    return {
        frozenset(int(m) for m in row["members"])
        for row in grouped.iter_rows(named=True)
    }


def _golden_setrows(golden):
    """Golden frame -> order-independent comparable rows (list cells ->
    frozensets), matching test_pipeline_frames_out_parity's normalizer."""
    if golden is None:
        return None
    rows = []
    for row in golden.iter_rows(named=True):
        norm = {
            k: (frozenset(v) if isinstance(v, list) else v)
            for k, v in row.items()
        }
        rows.append(tuple(sorted(norm.items(), key=lambda kv: kv[0])))
    return sorted(rows, key=repr)


def _edge_sets_by_partition(assignments, raw_pairs):
    """id_prep edge sets keyed by the cluster's MEMBER frozenset (so the
    arbitrary cluster_id label never enters the comparison). Uses
    ``ClusterPairScores.from_frames`` -- the exact id_prep view the spine
    builds."""
    from goldenmatch.core.cluster_pairscores import ClusterPairScores

    view = ClusterPairScores.from_frames(assignments, raw_pairs)
    members_by_cid: dict[int, frozenset[int]] = {}
    import polars as pl

    grouped = assignments.group_by("cluster_id").agg(
        pl.col("member_id").alias("members")
    )
    for row in grouped.iter_rows(named=True):
        members_by_cid[int(row["cluster_id"])] = frozenset(
            int(m) for m in row["members"]
        )

    out: dict[frozenset[int], frozenset[tuple[int, int]]] = {}
    for cid, key in members_by_cid.items():
        edges = view.for_cluster(cid)
        out[key] = frozenset(
            (min(a, b), max(a, b)) for (a, b) in edges.keys()
        )
    return out


def _run_spine(blocks, config):
    from goldenmatch.backends.datafusion_spine import run_spine

    return run_spine(blocks, config)


def _build_both(max_cluster_size):
    df = _fixture_df()
    config = _config(max_cluster_size=max_cluster_size)
    # Independent block lists so the two paths never share mutated frames.
    spine_blocks = _prepared_blocks(df, config)
    comp_blocks = _prepared_blocks(df, config)

    spine_golden, spine_assign = _run_spine(spine_blocks, config)
    comp_golden, comp_assign, comp_pairs = _inmemory_comparand(
        comp_blocks, config
    )
    # The spine's raw pairs are needed for the spine-side id_prep edge view;
    # re-derive them deterministically via the same backend over the spine
    # blocks (FFI vs native are jaro_winkler-equal, so identical to what the
    # spine ctx produced).
    from goldenmatch.backends.datafusion_backend import score_blocks_datafusion

    spine_pairs = score_blocks_datafusion(spine_blocks, config.get_matchkeys()[0], set())
    spine_pairs = [
        (a, b, s) if a < b else (b, a, s) for a, b, s in spine_pairs
    ]
    return (
        (spine_golden, spine_assign, spine_pairs),
        (comp_golden, comp_assign, comp_pairs),
    )


@pytest.mark.parametrize("native", ["1", "0"])
def test_spine_partition_parity(monkeypatch, native):
    """(a) Rand 1.0: identical member-set partition, label-independent."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    (spine, comp) = _build_both(max_cluster_size=3)

    spine_part = _partition(spine[1])
    comp_part = _partition(comp[1])
    assert spine_part == comp_part
    # The oversized cluster (dense block, size 5 > max_cluster_size 3) must
    # actually exist so the golden-exclusion leg below is exercised.
    assert any(len(s) >= 4 for s in comp_part), (
        "fixture did not produce an oversized cluster"
    )


@pytest.mark.parametrize("native", ["1", "0"])
def test_spine_golden_parity(monkeypatch, native):
    """(b) golden content equal (list cells as frozensets)."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    (spine, comp) = _build_both(max_cluster_size=3)
    assert _golden_setrows(spine[0]) == _golden_setrows(comp[0])


@pytest.mark.parametrize("native", ["1", "0"])
def test_spine_idprep_edge_parity(monkeypatch, native):
    """(c) id_prep ``for_cluster`` edge sets equal per cluster (keyed by
    member frozenset so cluster-id labels never matter)."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    (spine, comp) = _build_both(max_cluster_size=3)

    spine_edges = _edge_sets_by_partition(spine[1], spine[2])
    comp_edges = _edge_sets_by_partition(comp[1], comp[2])
    assert spine_edges == comp_edges
