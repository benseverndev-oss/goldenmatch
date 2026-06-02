"""Phase 2 SP3: DedupeResult.scored_pairs is sourced from the pre-cluster
scored-pair stream (normalized canonical + max-score deduped + sorted), NOT
reconstructed from cluster pair_scores. Documented behavior change: scored_pairs
is the FULL scored set (a superset of the post-split cluster pair_scores when
oversized clusters split), in canonical sorted order.
"""
import polars as pl
from goldenmatch import dedupe_df


def _cluster_pair_keys(result_clusters) -> set:
    keys = set()
    for cinfo in result_clusters.values():
        for (a, b) in cinfo.get("pair_scores", {}):
            keys.add((min(a, b), max(a, b)))
    return keys


def _dup_df():
    return pl.DataFrame({
        "name": ["Jon Smith", "Jon Smith", "Jane Doe", "Jane Doe", "Bob Lee"],
        "city": ["NYC", "NYC", "LA", "LA", "SF"],
    })


def test_scored_pairs_canonical_set_matches_clusters_no_split():
    res = dedupe_df(_dup_df(), exact=["name", "city"])
    sp_keys = {(min(a, b), max(a, b)) for (a, b, _s) in res.scored_pairs}
    assert sp_keys == _cluster_pair_keys(res.clusters)
    assert all(0.0 <= s <= 1.0 for (_a, _b, s) in res.scored_pairs)


def test_scored_pairs_sorted_and_deduped():
    res = dedupe_df(_dup_df(), exact=["name", "city"])
    pairs = [(a, b) for (a, b, _s) in res.scored_pairs]
    assert pairs == sorted(pairs)              # sorted by (a, b)
    assert len(pairs) == len(set(pairs))       # canonical-deduped


def test_cluster_pairs_are_subset_of_scored_pairs():
    # The decouple invariant that ALWAYS holds: every cluster pair_scores key is
    # present in scored_pairs (which is the full scored set). On no-split fixtures
    # they are equal; when an oversized cluster splits, scored_pairs is a strict
    # superset (it keeps the cross-cut edges auto-split removes from clusters).
    # (A strict-superset split fixture is omitted here: tuning a real auto-split
    # via the public API offline is fiddly, and the subset relation is the robust,
    # always-true form of the same invariant.)
    res = dedupe_df(_dup_df(), exact=["name", "city"])
    sp_keys = {(min(a, b), max(a, b)) for (a, b, _s) in res.scored_pairs}
    assert _cluster_pair_keys(res.clusters) <= sp_keys


def test_columnar_and_list_capture_normalize_identically():
    # The pipeline's two scored_pairs capture branches are
    #   list path:     dedup_pairs_max_score(all_pairs)
    #   columnar path: dedup_pairs_max_score(pairs_df_to_list(_columnar_pairs_df))
    # For the same scored pairs they MUST produce the identical normalized list.
    # Test that equivalence directly (independent of _is_columnar_eligible, which
    # an exact-matchkey fixture wouldn't trigger).
    import polars as pl
    from goldenmatch.core.pairs import dedup_pairs_max_score
    from goldenmatch.core.scorer import pairs_df_to_list

    pairs = [(2, 1, 0.9), (1, 2, 0.95), (3, 4, 0.8), (3, 4, 0.7)]
    df = pl.DataFrame(
        {"id_a": [2, 1, 3, 3], "id_b": [1, 2, 4, 4], "score": [0.9, 0.95, 0.8, 0.7]},
        schema={"id_a": pl.Int64, "id_b": pl.Int64, "score": pl.Float64},
    )
    assert dedup_pairs_max_score(pairs) == dedup_pairs_max_score(pairs_df_to_list(df))
