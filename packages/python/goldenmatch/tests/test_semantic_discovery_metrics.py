"""Metrics derivation (PR-12).

Derive certifiable business metrics from the grain-gated measures: per sum-safe
measure an average (SUM/COUNT at a trustworthy grain), and per sum-safe measure pair a
ratio (SUM/SUM). Deterministic, default-on, emitted natively per dialect. Driven on an
orders fixture with two measures.
"""
from __future__ import annotations

import pyarrow as pa
import yaml
from goldenmatch.semantic import discover_semantic_model
from goldenmatch.semantic.discovery.measures import Measure


def _orders() -> pa.Table:
    return pa.table({
        "order_id": ["o1", "o2", "o3", "o4"],
        "amount": [10.0, 20.0, 30.0, 40.0],
        "quantity": [1, 2, 3, 4],
    })


# --- discover_metrics ------------------------------------------------------------


def test_discover_metrics_averages_and_ratio():
    from goldenmatch.semantic.discovery.metrics import discover_metrics

    measures = [
        Measure(column="amount", aggregations=["sum"], safe_to_sum=True),
        Measure(column="quantity", aggregations=["sum"], safe_to_sum=True),
    ]
    ms = discover_metrics(measures, grain=["order_id"], table_name="orders")
    by_name = {m.name: m for m in ms}

    # per-measure averages (SUM(m)/COUNT(grain)).
    assert by_name["avg_amount"].kind == "average"
    assert by_name["avg_amount"].numerator == "amount"
    assert "COUNT(order_id)" in by_name["avg_amount"].expression
    assert "avg_quantity" in by_name

    # a measure-pair ratio (SUM/SUM), deterministically sorted numerator/denominator.
    ratio = by_name["amount_per_quantity"]
    assert ratio.kind == "ratio"
    assert (ratio.numerator, ratio.denominator) == ("amount", "quantity")


def test_no_metrics_without_trustworthy_sum_safe_measures():
    from goldenmatch.semantic.discovery.metrics import discover_metrics

    # fanned-out grain -> nothing is sum-safe -> no metrics (they'd double-count).
    measures = [Measure(column="amount", aggregations=["count"], safe_to_sum=False)]
    assert discover_metrics(measures, grain=["order_id"], table_name="orders") == []


def test_ratio_pairs_are_capped():
    from goldenmatch.semantic.discovery.metrics import _METRIC_PAIR_POOL_CAP, discover_metrics

    measures = [Measure(column=f"m{i}", aggregations=["sum"], safe_to_sum=True)
                for i in range(10)]
    ratios = [m for m in discover_metrics(measures, grain=["k"], table_name="t")
              if m.kind == "ratio"]
    cap = _METRIC_PAIR_POOL_CAP
    assert len(ratios) == cap * (cap - 1) // 2  # C(cap, 2), not C(10, 2)


# --- integration + native emit --------------------------------------------------


def test_metrics_flow_through_model_and_to_dict():
    m = discover_semantic_model({"orders": _orders()})
    names = {mm.name for mm in m.metrics}
    assert {"avg_amount", "avg_quantity", "amount_per_quantity"} <= names
    d = m.to_dict()
    assert any(x["name"] == "avg_amount" for x in d["metrics"])


def test_metrics_emitted_natively_metricflow_and_osi():
    # MetricFlow: a top-level metrics: block.
    mf = discover_semantic_model({"orders": _orders()})
    doc = yaml.safe_load(mf.yaml)
    assert "metrics" in doc
    assert any(x["name"] == "avg_amount" for x in doc["metrics"])

    # OSI: OsiMetrics carry the derived metrics.
    osi = discover_semantic_model({"orders": _orders()}, dialect="osi")
    metrics = [mt for model in yaml.safe_load(osi.yaml)["semantic_model"]
               for mt in model["metrics"]]
    assert any(mt["name"] == "avg_amount" for mt in metrics)
