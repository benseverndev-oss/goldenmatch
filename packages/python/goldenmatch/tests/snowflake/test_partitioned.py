"""Unit tests for the partitioned Snowflake tier.

Everything here runs WITHOUT Snowflake. The plan is pure string building, the
clustering is the engine's own union-find, and ``score_partition`` needs the
engine but not a warehouse -- so the only thing that genuinely needs a live
session is gluing the two together, which is an integration concern.
"""

from __future__ import annotations

import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.snowflake.partitioned import (
    PARTITION_KEY_COLUMN,
    PartitionedPlan,
    UnsupportedBlockingError,
    block_key_sql,
    blocking_passes,
    cluster_pairs,
    plan_passes,
    valid_key_sql,
)


class _Cfg:
    """Minimal config stand-in: these helpers only ever read ``.blocking``."""

    def __init__(self, blocking):
        self.blocking = blocking


# ── block keys ──────────────────────────────────────────────────────────────


def test_single_field_key_needs_no_separator():
    sql, fields = block_key_sql(BlockingKeyConfig(fields=["npi"]))
    assert fields == ["npi"]
    assert "||" not in sql
    assert "npi" in sql


def test_multi_field_key_joins_with_the_separator():
    sql, fields = block_key_sql(BlockingKeyConfig(fields=["last_name", "zip5"]))
    assert fields == ["last_name", "zip5"]
    assert "'||'" in sql.replace(" ", "")


def test_any_null_part_nulls_the_whole_key():
    """``concat_ws`` SKIPS nulls, which would collide ("a", null) with ("a", "").

    The guard is what keeps a partial key from sharing a partition with a
    complete one, so it is asserted rather than assumed.
    """
    sql, _ = block_key_sql(BlockingKeyConfig(fields=["a", "b"]))
    assert sql.startswith("CASE WHEN ")
    assert sql.count("IS NULL") == 2
    assert " OR " in sql


def test_identifiers_are_BARE_by_default_so_lower_case_fields_resolve():
    """Snowflake folds unquoted identifiers to upper case at DDL time, so a
    table created as ``NPI`` is NOT matched by ``"npi"``. GoldenMatch field
    names are conventionally lower case, so quoting them by default would fail
    to resolve against an ordinary relation -- found by running it."""
    sql, _ = block_key_sql(BlockingKeyConfig(fields=["npi"]))
    assert "npi" in sql
    assert '"npi"' not in sql


def test_quoting_is_available_for_relations_that_really_are_mixed_case():
    sql, _ = block_key_sql(BlockingKeyConfig(fields=["npi"]), quote=True)
    assert '"npi"' in sql


def test_a_name_that_is_not_a_legal_bare_identifier_is_quoted_anyway():
    """Otherwise the emitted SQL simply cannot parse, whatever the caller asked."""
    sql, _ = block_key_sql(BlockingKeyConfig(fields=["odd name"]))
    assert '"odd name"' in sql


def test_an_embedded_quote_cannot_escape_the_identifier():
    sql, _ = block_key_sql(BlockingKeyConfig(fields=['ev"il']))
    assert '"ev""il"' in sql


# ── validity predicate ──────────────────────────────────────────────────────


def test_valid_key_drops_the_stringified_missing_sentinels():
    pred = valid_key_sql("K")
    for sentinel in ("nan", "null", "none"):
        assert "'" + sentinel + "'" in pred


def test_the_empty_string_is_a_REAL_KEY_not_a_null():
    """#390: the empty string is a real key value, and blocks on it must survive.

    This is a regression guard with a measured cost. An earlier version wrapped
    every part in ``NULLIF(x, '')``, which maps '' to NULL; the any-null guard
    then nulled the whole key and ``valid_key_sql`` dropped it. On one 209k
    relation that silently deleted a 100-member block -- 4,950 pairs, and the
    entire equivalence gap against the single-node path.

    Asserted on the KEY expression, not on the predicate. The predicate-only
    version of this test passed throughout, because the emptiness was already
    destroyed upstream of it.
    """
    sql, _ = block_key_sql(BlockingKeyConfig(fields=["last_name"]))
    assert "NULLIF" not in sql.upper()


def test_valid_key_drops_only_the_missing_sentinels():
    """The predicate half: sentinels go, everything else -- '' included -- stays."""
    pred = valid_key_sql("K")
    assert "IS NOT NULL" in pred
    assert pred.count("'") == 6  # exactly the three sentinels, nothing more


# ── the pass plan ───────────────────────────────────────────────────────────


def test_multi_pass_reads_passes_not_keys():
    """The recall trap: ``keys`` holds ONE pass on a multi_pass config.

    Reading ``keys`` would generate candidates from a single pass out of N and
    look like a clean run, so this pins the precedence.
    """
    passes = [
        BlockingKeyConfig(fields=["npi"]),
        BlockingKeyConfig(fields=["email"]),
        BlockingKeyConfig(fields=["phone"]),
    ]
    cfg = _Cfg(BlockingConfig(strategy="multi_pass", keys=[passes[0]], passes=passes))
    assert len(blocking_passes(cfg)) == 3
    assert len(plan_passes(cfg).passes) == 3


def test_static_config_falls_back_to_keys():
    cfg = _Cfg(BlockingConfig(keys=[BlockingKeyConfig(fields=["npi"])]))
    assert len(plan_passes(cfg).passes) == 1


def test_a_transform_chain_is_REFUSED_not_approximated():
    """Re-expressing a GoldenMatch transform as SQL is a second implementation.

    If the two ever disagreed the symptom would be a changed candidate set,
    which reads as a tuning difference rather than a bug -- so the tier refuses
    and names the reason.
    """
    cfg = _Cfg(
        BlockingConfig(keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase"])])
    )
    with pytest.raises(UnsupportedBlockingError) as exc:
        plan_passes(cfg)
    assert "lowercase" in str(exc.value)
    assert "Precompute" in str(exc.value)


def test_a_per_field_transform_is_refused_too():
    cfg = _Cfg(
        BlockingConfig(
            keys=[
                BlockingKeyConfig(fields=["last_name"], field_transforms={"last_name": ["strip"]})
            ]
        )
    )
    with pytest.raises(UnsupportedBlockingError):
        plan_passes(cfg)


def test_an_empty_per_field_transform_map_is_not_a_transform():
    """``field_transforms={"x": []}`` declares nothing; refusing it would be a
    false positive that blocks a perfectly expressible config."""
    cfg = _Cfg(
        BlockingConfig(keys=[BlockingKeyConfig(fields=["npi"], field_transforms={"npi": []})])
    )
    assert len(plan_passes(cfg).passes) == 1


def test_pair_sql_unions_every_pass_and_partitions_by_the_block_key():
    passes = [BlockingKeyConfig(fields=["npi"]), BlockingKeyConfig(fields=["email"])]
    cfg = _Cfg(BlockingConfig(strategy="multi_pass", keys=[passes[0]], passes=passes))
    plan = plan_passes(cfg, id_column="unique_id", columns=["npi", "email"])
    sql = plan.pair_sql("SRC", "gm_score_partition")
    assert sql.count("UNION ALL") == 1
    assert sql.count("PARTITION BY") == 2
    assert PARTITION_KEY_COLUMN in sql


def test_udtf_arguments_are_listed_explicitly_not_star():
    """``TABLE(f(s.*) OVER (...))`` does not parse in Snowflake.

    The column list is also the contract ``end_partition`` names its frame by,
    so its order is asserted rather than left implicit.
    """
    passes = [BlockingKeyConfig(fields=["npi"])]
    plan = plan_passes(
        _Cfg(BlockingConfig(keys=passes)),
        id_column="unique_id",
        columns=["npi", "email"],
    )
    sql = plan.pair_sql("SRC", "udtf")
    assert "s.*" not in sql
    args = sql.split("udtf(", 1)[1].split(")", 1)[0]
    assert args.split(", ") == [
        's."' + PARTITION_KEY_COLUMN + '"',
        "s.unique_id",
        "s.npi",
        "s.email",
    ]


def test_a_plan_with_no_passes_refuses_rather_than_emitting_empty_sql():
    """``BlockingConfig`` already rejects empty ``keys``, so this cannot arrive
    through a config -- the guard exists for a plan assembled another way, and
    an empty ``UNION ALL`` would otherwise be a syntax error at the warehouse
    instead of a named refusal here."""
    with pytest.raises(ValueError):
        PartitionedPlan(passes=[]).pair_sql("SRC", "udtf")


# ── global clustering ───────────────────────────────────────────────────────


def test_clustering_is_transitive_across_passes():
    """a-b from one pass and b-c from another must land in ONE cluster.

    This is the property the whole design rests on: partitioned scoring may
    never see a and c together, and only the global union-find joins them.
    """
    out = cluster_pairs([("a", "b", 1.0), ("b", "c", 1.0)])
    assert out["a"] == out["b"] == out["c"]


def test_a_duplicate_edge_from_two_passes_changes_nothing():
    once = cluster_pairs([("a", "b", 1.0)])
    twice = cluster_pairs([("a", "b", 1.0), ("a", "b", 0.9)])
    assert len({once["a"], once["b"]}) == 1
    assert len({twice["a"], twice["b"]}) == 1
    assert len(twice) == 2


def test_pairs_are_DEDUPED_before_clustering_so_the_split_is_deterministic():
    """Union-find shrugs at duplicate edges; the MST auto-split above it does not.

    A pair emitted by two blocking passes arrives twice, in UNION ALL order, and
    an un-deduped edge list made the split order-dependent: measured on one 209k
    relation, identical cluster COUNT and identical pair COUNT but 579 pairs
    placed differently in each direction. So the property under test is not
    "clusters are right" -- it is that edge ORDER and MULTIPLICITY cannot change
    the answer.
    """

    class _Rules:
        max_cluster_size = 4
        weak_cluster_threshold = 0.3
        auto_split = True
        split_edge_budget = None

    class _WithRules:
        golden_rules = _Rules()

    ids = [f"r{i}" for i in range(12)]
    chain = [(ids[i], ids[i + 1], 0.9 + i / 100) for i in range(len(ids) - 1)]
    plain = cluster_pairs(chain, all_ids=ids, config=_WithRules())
    shuffled = cluster_pairs(list(reversed(chain)), all_ids=ids, config=_WithRules())
    duplicated = cluster_pairs(chain + chain, all_ids=ids, config=_WithRules())

    def shape(m):
        groups = {}
        for k, v in m.items():
            groups.setdefault(v, set()).add(k)
        return sorted(sorted(g) for g in groups.values())

    assert shape(plain) == shape(shuffled), "edge ORDER changed the split"
    assert shape(plain) == shape(duplicated), "edge MULTIPLICITY changed the split"


def test_unpaired_ids_survive_as_singletons_when_declared():
    out = cluster_pairs([("a", "b", 1.0)], all_ids=["a", "b", "lonely"])
    assert out["a"] == out["b"]
    assert out["lonely"] != out["a"]
    assert len(set(out.values())) == 2


def test_disjoint_groups_stay_disjoint():
    out = cluster_pairs([("a", "b", 1.0), ("c", "d", 1.0)])
    assert out["a"] == out["b"]
    assert out["c"] == out["d"]
    assert out["a"] != out["c"]


def test_string_ids_round_trip_through_the_integer_kernel():
    """The kernel is integer union-find; ids are interned and restored.

    A silent int-ification would return cluster members that name the wrong
    rows, so the identity of the keys is pinned.
    """
    out = cluster_pairs([("id-xyz", "id-abc", 1.0)])
    assert set(out) == {"id-xyz", "id-abc"}


def test_oversized_clusters_are_SPLIT_like_the_one_box_path():
    """``build_clusters`` auto-splits above ``max_cluster_size``; plain
    union-find does not, and that difference is the whole remaining equivalence
    gap if you get it wrong.

    Measured: a 107-member hub block the one-box path split into 8 clusters came
    back as ONE cluster while this delegated to ``connected_components``.
    """

    class _Rules:
        max_cluster_size = 4
        weak_cluster_threshold = 0.3
        auto_split = True
        split_edge_budget = None

    class _WithRules:
        golden_rules = _Rules()

    ids = [f"r{i}" for i in range(12)]
    chain = [(ids[i], ids[i + 1], 0.99) for i in range(len(ids) - 1)]
    out = cluster_pairs(chain, all_ids=ids, config=_WithRules())
    assert len(set(out.values())) > 1, "a 12-member chain must split at size 4"


def test_clustering_without_a_config_still_works():
    """``config`` is optional; the pipeline's own defaults apply."""
    out = cluster_pairs([("a", "b", 1.0)], all_ids=["a", "b"])
    assert out["a"] == out["b"]
