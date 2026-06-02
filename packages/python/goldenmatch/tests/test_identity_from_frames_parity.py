"""SP-C durability gate: ``resolve_clusters(cluster_frames=..., pair_score_view=...)``
must resolve run-local clusters to durable identities BYTE-IDENTICALLY to the legacy
``resolve_clusters(clusters_dict, ...)`` path. Identity entity-ids are the durable
contract, so any divergence between the two iteration sources splits/merges identities
differently across the cutover -- this test locks them.

Two assertions:
  - PARTITION equality (entity_id is a random UUIDv7, so compare the
    record_id -> entity_id *partition*, not literal ids).
  - Literal equality under a deterministic mint (monkeypatch ``new_entity_id``
    to a counter) -- locks ResolveSummary + the literal record->entity map.

The fixture exercises absorb/merge/legacy-fallback (PRIOR-run seeded entities),
a weak-bottleneck conflict cluster, and an oversized-split cluster. Native leg
SKIPS locally, runs in CI (mirrors ``test_cluster_frames_out_parity``).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.cluster import (
    build_cluster_frames,
    cluster_frames_to_dict,
)
from goldenmatch.core.cluster_pairscores import ClusterPairScores
from goldenmatch.identity import IdentityStore, resolve_clusters


def _skip_if_no_native(native):
    if native == "1":
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        if nm is None or getattr(nm, "build_clusters_arrow", None) is None:
            pytest.skip("native cluster kernel absent; native=1 validated in CI")


# Records 0..15. No-PK (content-hash ids via source_pk_col=None) so the
# absorb/merge/legacy paths key off the h1 fingerprint -- the durability-
# critical case. Distinct names keep each record's fingerprint unique.
_NAMES = [
    "Alice", "Alyce", "Bob", "Bobby", "Carol", "Caroline", "Dave", "Davy",
    "Eve", "Eva", "Frank", "Franklin", "Grace", "Gracie", "Heidi", "Heidy",
]


def _df(member_ids=None):
    ids = list(range(16)) if member_ids is None else member_ids
    return pl.DataFrame({
        "__row_id__": ids,
        "__source__": ["src"] * len(ids),
        "name": [_NAMES[i] for i in ids],
    })


def _pairs():
    """Pairs feeding build_cluster_frames. Shapes:
      - {0,1} simple merge-into-existing (seeded as two priors below)
      - {2,3} simple absorb (one prior covers record 2)
      - {4,5,6} weak chain -> weak bottleneck (4-6 edge below threshold)
      - {10..16}-ish oversized barbell that auto-splits (max_cluster_size=4)
      - 8, 9 singletons
    """
    pairs = [
        (0, 1, 0.95),                                   # merge cluster
        (2, 3, 0.95),                                   # absorb cluster
        (4, 5, 0.99), (5, 6, 0.40),                     # weak chain bottleneck
        # barbell {10,11,12} - {13,14,15}, weak bridge 12-13 -> splits
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (13, 14, 0.99), (14, 15, 0.99), (13, 15, 0.99),
        (12, 13, 0.31),
    ]
    all_ids = list(range(16))
    return pairs, all_ids


def _build(monkeypatch, native):
    """Build frames + dict + raw-pairs view under a consistent gate setup."""
    pairs, all_ids = _pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    _skip_if_no_native(native)
    kw = dict(all_ids=all_ids, max_cluster_size=4,
              weak_cluster_threshold=0.3, auto_split=True)
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")
    frames = build_cluster_frames(pairs, **kw)
    clusters_dict = cluster_frames_to_dict(frames)
    view = ClusterPairScores.from_frames(frames.assignments, pairs)
    return frames, clusters_dict, view, pairs


def _seed_one(store, orig_id, run_name):
    """Seed a singleton identity for the record whose payload matches original
    row ``orig_id`` (no-PK h1 fingerprint is payload-derived, so the mini-df's
    __row_id__ is irrelevant -- only name+source determine the record id)."""
    df = pl.DataFrame({
        "__row_id__": [0],
        "__source__": ["src"],
        "name": [_NAMES[orig_id]],
    })
    resolve_clusters(
        {0: {"members": [0], "size": 1, "oversized": False,
             "pair_scores": {}, "confidence": 1.0}},
        df, [], "seed", store, run_name=run_name, source_pk_col=None,
    )


def _seed_priors(store, run_name="seed-run"):
    """Seed PRIOR-run identities so the current run exercises merge + absorb.
    Records 0 and 1 each get their OWN identity (so the current {0,1} cluster
    MERGES the two); record 2 gets an identity (so {2,3} ABSORBS record 3)."""
    _seed_one(store, 0, run_name + "-a")
    _seed_one(store, 1, run_name + "-b")
    _seed_one(store, 2, run_name + "-c")


def _record_to_entity(store) -> dict[str, str]:
    """record_id -> entity_id over all ACTIVE + retired records in the store."""
    out: dict[str, str] = {}
    for node in store.list_identities():
        for rec in store.get_records_for_entity(node.entity_id):
            out[rec.record_id] = rec.entity_id
    return out


def _partition(rec_to_eid: dict[str, str]) -> set[frozenset]:
    by_eid: dict[str, set] = {}
    for rid, eid in rec_to_eid.items():
        by_eid.setdefault(eid, set()).add(rid)
    return {frozenset(v) for v in by_eid.values()}


def _assert_nonvacuous(summary, partition: set[frozenset]) -> None:
    """Guard the durability gate against a vacuous pass.

    ``part_a == part_b`` / ``sum_a == sum_b`` are byte-equal even when BOTH
    runs degenerate to all-singletons or empty stores. Assert the REFERENCE
    (dict-path) run actually produced a multi-record entity AND did real
    resolution work, so a fixture regression can't make the gate pass empty.

    ``ResolveSummary.edges_added`` is the evidence-edge count -- this fixture
    seeds a merge ({0,1}), an absorb ({2,3}), a weak-bottleneck chain
    ({4,5,6}), and an oversized barbell that auto-splits, so every multi-member
    cluster contributes >= 1 edge. edges_added is reliably well >= 1.
    """
    assert any(len(s) > 1 for s in partition), \
        "fixture produced no multi-record entity"
    assert summary.edges_added >= 1, \
        f"reference run did no resolution work: {summary.as_dict()}"


def _make_store(tmp_path, name):
    return IdentityStore(path=str(tmp_path / name))


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_path_partition_matches_dict_path(tmp_path, monkeypatch, native):
    frames, clusters_dict, view, pairs = _build(monkeypatch, native)
    df = _df()

    # DICT path against a freshly-seeded store.
    store_a = _make_store(tmp_path, "dict.db")
    try:
        _seed_priors(store_a)
        sum_a = resolve_clusters(
            clusters_dict, df, pairs, "wd", store_a, run_name="run-x",
            source_pk_col=None, pair_score_view=view,
        )
        part_a = _partition(_record_to_entity(store_a))
    finally:
        store_a.close()

    # FRAMES path against an IDENTICALLY freshly-seeded store.
    store_b = _make_store(tmp_path, "frames.db")
    try:
        _seed_priors(store_b)
        sum_b = resolve_clusters(
            cluster_frames=frames, df=df, scored_pairs=pairs,
            matchkey_name="wd", store=store_b, run_name="run-x",
            source_pk_col=None, pair_score_view=view,
        )
        part_b = _partition(_record_to_entity(store_b))
    finally:
        store_b.close()

    # Anti-vacuous: the reference run must be non-trivial (multi-record entity
    # + real resolution work), else two degenerate runs pass byte-equal.
    _assert_nonvacuous(sum_a, part_a)
    assert part_a == part_b, f"partition diverged:\n dict={part_a}\n frames={part_b}"
    # Summary counts (no entity-id literals) must match too.
    assert sum_a == sum_b


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_path_literal_identical_under_deterministic_mint(
    tmp_path, monkeypatch, native,
):
    import goldenmatch.identity.resolve as resolve_mod

    frames, clusters_dict, view, pairs = _build(monkeypatch, native)
    df = _df()

    def _det_minter():
        counter = {"n": 0}

        def _mint():
            counter["n"] += 1
            return f"det-entity-{counter['n']:04d}"

        return _mint

    # DICT path with deterministic mint.
    monkeypatch.setattr(resolve_mod, "new_entity_id", _det_minter())
    store_a = _make_store(tmp_path, "dict_det.db")
    try:
        _seed_priors(store_a)
        sum_a = resolve_clusters(
            clusters_dict, df, pairs, "wd", store_a, run_name="run-x",
            source_pk_col=None, pair_score_view=view,
        )
        map_a = _record_to_entity(store_a)
    finally:
        store_a.close()

    # FRAMES path with a FRESH deterministic mint (same starting counter).
    monkeypatch.setattr(resolve_mod, "new_entity_id", _det_minter())
    store_b = _make_store(tmp_path, "frames_det.db")
    try:
        _seed_priors(store_b)
        sum_b = resolve_clusters(
            cluster_frames=frames, df=df, scored_pairs=pairs,
            matchkey_name="wd", store=store_b, run_name="run-x",
            source_pk_col=None, pair_score_view=view,
        )
        map_b = _record_to_entity(store_b)
    finally:
        store_b.close()

    # Anti-vacuous: reference run must be non-trivial (multi-record entity +
    # real resolution work) so the literal-map equality can't pass empty.
    _assert_nonvacuous(sum_a, _partition(map_a))
    assert map_a == map_b, (
        "literal record->entity map diverged under deterministic mint:\n"
        f" only dict={set(map_a.items()) - set(map_b.items())}\n"
        f" only frames={set(map_b.items()) - set(map_a.items())}"
    )
    assert sum_a == sum_b


def test_frames_path_requires_pair_score_view(tmp_path, monkeypatch):
    frames, _clusters_dict, _view, pairs = _build(monkeypatch, "0")
    df = _df()
    store = _make_store(tmp_path, "noview.db")
    try:
        with pytest.raises(ValueError, match="pair_score_view"):
            resolve_clusters(
                cluster_frames=frames, df=df, scored_pairs=pairs,
                matchkey_name="wd", store=store, run_name="run-x",
                source_pk_col=None,
            )
    finally:
        store.close()


def test_resolve_clusters_rejects_both_sources(tmp_path, monkeypatch):
    frames, clusters_dict, view, pairs = _build(monkeypatch, "0")
    df = _df()
    store = _make_store(tmp_path, "both.db")
    try:
        with pytest.raises(ValueError, match="exactly one"):
            resolve_clusters(
                clusters_dict, df, pairs, "wd", store, run_name="run-x",
                source_pk_col=None, pair_score_view=view,
                cluster_frames=frames,
            )
    finally:
        store.close()
