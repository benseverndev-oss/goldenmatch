"""The clustering decision must honor an explicit strategy over the env
threshold, and fall back to the env threshold when no strategy is given."""
from goldenmatch.distributed import clustering


def test_explicit_in_memory_overrides_env_threshold_zero(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
    # With strategy="in_memory", threshold=0 must NOT force label-prop.
    decided = clustering._resolve_use_label_prop(
        pair_count=1000, clustering_strategy="in_memory")
    assert decided is False


def test_env_fallback_when_no_strategy(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
    decided = clustering._resolve_use_label_prop(
        pair_count=1000, clustering_strategy=None)
    assert decided is True  # threshold 0 => always distribute (today's behavior)


def test_explicit_distributed_forces_label_prop(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", raising=False)
    decided = clustering._resolve_use_label_prop(
        pair_count=10, clustering_strategy="distributed_wcc")
    assert decided is True
