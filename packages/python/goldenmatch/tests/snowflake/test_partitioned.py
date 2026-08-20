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


def test_valid_key_keeps_the_empty_string():
    """Empty string is a real value (#390); only missing sentinels are dropped."""
    pred = valid_key_sql("K")
    assert "''" not in pred.replace("'nan'", "").replace("'null'", "").replace("'none'", "")


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
