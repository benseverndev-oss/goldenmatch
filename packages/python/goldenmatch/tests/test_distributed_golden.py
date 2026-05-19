import pytest

ray = pytest.importorskip("ray")


# ── Task 1: threshold helper ─────────────────────────────────────────────────

def test_default_threshold_is_5m():
    from goldenmatch.distributed.golden import _golden_cluster_threshold
    assert _golden_cluster_threshold() == 5_000_000


def test_env_override(monkeypatch):
    from goldenmatch.distributed.golden import _golden_cluster_threshold
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD", "1000")
    assert _golden_cluster_threshold() == 1000


def test_env_override_invalid_falls_back_to_default(monkeypatch):
    from goldenmatch.distributed.golden import _golden_cluster_threshold
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD", "not_an_int")
    assert _golden_cluster_threshold() == 5_000_000


# ── Task 2: build_golden_records_distributed + materialize_golden_dataframe ──

def test_build_golden_records_distributed_matches_in_memory(tmp_path):
    """Distributed golden output equals in-memory build_golden_records_batch
    for the simple uniform-strategy fast path."""
    import polars as pl
    import ray
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.golden import build_golden_records_batch
    from goldenmatch.distributed.golden import (
        build_golden_records_distributed,
        materialize_golden_dataframe,
    )

    multi_df = pl.DataFrame({
        "__row_id__": list(range(9)),
        "__cluster_id__": [1, 1, 1, 2, 2, 3, 3, 3, 3],
        "first_name": ["Alice", "alice", "ALICE", "Bob", "bob", "Carol", "carol", "CAROL", "Carrol"],
        "last_name": ["Smith"] * 3 + ["Jones"] * 2 + ["Brown"] * 4,
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")

    in_mem = build_golden_records_batch(multi_df, rules)

    ds = ray.data.from_arrow(multi_df.to_arrow())
    out_ds = build_golden_records_distributed(
        ds, rules, user_columns=["first_name", "last_name"],
    )
    distributed_df = materialize_golden_dataframe(out_ds)

    in_mem_sorted = sorted(in_mem, key=lambda r: r["__cluster_id__"])
    dist_rows = sorted(distributed_df.to_dicts(), key=lambda r: r["__cluster_id__"])
    assert len(in_mem_sorted) == len(dist_rows) == 3
    for a, b in zip(in_mem_sorted, dist_rows):
        assert a["__cluster_id__"] == b["__cluster_id__"]
        assert a["first_name"] == b["first_name"]
        assert a["last_name"] == b["last_name"]


def test_build_golden_records_distributed_co_locates_split_clusters():
    """Cluster rows scattered across input partitions must end up co-located."""
    import polars as pl
    import ray
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.distributed.golden import (
        build_golden_records_distributed,
        materialize_golden_dataframe,
    )

    rows = []
    for k in range(4):  # 4 input partitions
        for cid in range(1, 11):  # 10 clusters
            rows.append({
                "__row_id__": k * 100 + cid,
                "__cluster_id__": cid,
                "first_name": f"name_{cid}_{k}",
                "last_name": "fixed",
            })
    multi_df = pl.DataFrame(rows)
    # Force 4 input partitions
    ds = ray.data.from_arrow(multi_df.to_arrow()).repartition(4)

    out_ds = build_golden_records_distributed(
        ds, GoldenRulesConfig(default_strategy="most_complete"),
        user_columns=["first_name", "last_name"],
    )
    out_rows = materialize_golden_dataframe(out_ds).to_dicts()
    by_cid = {r["__cluster_id__"]: r for r in out_rows}
    assert len(out_rows) == 10
    assert set(by_cid.keys()) == set(range(1, 11))


# ── Task 3: build_golden_records_smart ───────────────────────────────────────

def test_distributed_golden_dispatches_to_in_memory_below_threshold(caplog):
    """Below the cluster-count threshold, route to in-memory builder."""
    import logging

    import polars as pl
    import ray
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.distributed.golden import build_golden_records_smart

    multi_df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__cluster_id__": [1, 1, 2, 2],
        "first_name": ["a", "ab", "b", "bc"],
    })
    ds = ray.data.from_arrow(multi_df.to_arrow())

    with caplog.at_level(logging.INFO):
        result = build_golden_records_smart(
            ds, GoldenRulesConfig(default_strategy="most_complete"),
            user_columns=["first_name"],
        )

    assert len(result) == 2
    msgs = [r.message.lower() for r in caplog.records]
    assert any("in-memory" in m for m in msgs), msgs


def test_distributed_golden_dispatches_to_distributed_above_threshold(monkeypatch, caplog):
    """Above the cluster-count threshold, route to distributed."""
    import logging

    import polars as pl
    import ray
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.distributed.golden import build_golden_records_smart

    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD", "1")
    multi_df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__cluster_id__": [1, 1, 2, 2],
        "first_name": ["a", "ab", "b", "bc"],
    })
    ds = ray.data.from_arrow(multi_df.to_arrow())

    with caplog.at_level(logging.INFO):
        result = build_golden_records_smart(
            ds, GoldenRulesConfig(default_strategy="most_complete"),
            user_columns=["first_name"],
        )

    assert len(result) == 2
    msgs = [r.message.lower() for r in caplog.records]
    assert any("distributed golden" in m for m in msgs), msgs


# ── Task 4: custom field rules fallback ──────────────────────────────────────

def test_distributed_golden_falls_back_on_custom_field_rules(monkeypatch, caplog):
    """Custom field rules force the in-memory path regardless of cluster count."""
    import logging

    import polars as pl
    import ray
    from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig
    from goldenmatch.distributed.golden import build_golden_records_smart

    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD", "1")
    pl_df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__cluster_id__": [1, 1, 2, 2],
        "first_name": ["a", "ab", "b", "bc"],
    })
    ds = ray.data.from_arrow(pl_df.to_arrow())
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"first_name": GoldenFieldRule(strategy="majority_vote")},
    )

    with caplog.at_level(logging.INFO):
        result = build_golden_records_smart(
            ds, rules, user_columns=["first_name"],
        )

    assert len(result) == 2
    msgs = [r.message.lower() for r in caplog.records]
    assert any("custom field rules" in m for m in msgs), msgs


# ── Task 5: polymorphic dispatch in build_golden_records_batch ───────────────

def test_build_golden_records_batch_dispatches_to_distributed_on_ray_dataset():
    import polars as pl
    import ray

    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.golden import build_golden_records_batch

    multi_df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__cluster_id__": [1, 1, 2, 2],
        "first_name": ["a", "ab", "b", "bc"],
    })
    ds = ray.data.from_arrow(multi_df.to_arrow())
    out = build_golden_records_batch(ds, GoldenRulesConfig(default_strategy="most_complete"))
    assert isinstance(out, list)
    assert len(out) == 2


def test_build_golden_records_batch_dispatches_to_in_memory_on_polars_df():
    import polars as pl
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.golden import build_golden_records_batch

    multi_df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__cluster_id__": [1, 1, 2, 2],
        "first_name": ["a", "ab", "b", "bc"],
    })
    out = build_golden_records_batch(multi_df, GoldenRulesConfig(default_strategy="most_complete"))
    assert isinstance(out, list)
    assert len(out) == 2
