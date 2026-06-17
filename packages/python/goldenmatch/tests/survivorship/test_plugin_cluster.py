"""E1 + E2 tests: GoldenStrategyPlugin.merge cluster= param + dispatcher threading."""
import inspect

import polars as pl

from goldenmatch.config.schemas import GoldenFieldRule
from goldenmatch.core.golden import merge_field
from goldenmatch.plugins.base import GoldenStrategyPlugin
from goldenmatch.plugins.registry import PluginRegistry


# ── E1 ────────────────────────────────────────────────────────────────────────


def test_protocol_merge_accepts_cluster():
    sig = inspect.signature(GoldenStrategyPlugin.merge)
    assert "cluster" in sig.parameters
    assert sig.parameters["cluster"].default is None


# ── E2 ────────────────────────────────────────────────────────────────────────


class _ClusterReadingPlugin:
    name = "reads_cluster"

    def merge(self, values, *, sources=None, dates=None, quality_weights=None,
              pair_scores=None, rule_kwargs=None, cluster=None):
        assert "when" not in (rule_kwargs or {})
        assert "validate_with" not in (rule_kwargs or {})
        idx = cluster["flag"].to_list().index(True)
        return (values[idx], 1.0, idx)


class _LegacyPlugin:
    name = "legacy2"

    def merge(self, values, *, sources=None, dates=None, quality_weights=None,
              pair_scores=None, rule_kwargs=None):
        return (values[0], 0.5)


def test_cluster_passed_only_when_accepted(monkeypatch):
    reg = PluginRegistry.instance()
    reg.discover()
    # Inject test plugins into the registry's golden-strategy store.
    monkeypatch.setitem(reg._golden_strategies, "reads_cluster", _ClusterReadingPlugin())
    monkeypatch.setitem(reg._golden_strategies, "legacy2", _LegacyPlugin())

    cluster = pl.DataFrame({"v": ["x", "y"], "flag": [False, True]})
    rule = GoldenFieldRule(strategy="custom:reads_cluster", when="a == 1")
    val, conf, idx = merge_field(["x", "y"], rule, cluster=cluster)
    assert val == "y" and idx == 1

    val2, conf2, idx2 = merge_field(
        ["x", "y"], GoldenFieldRule(strategy="custom:legacy2"), cluster=cluster
    )
    assert val2 == "x"
