"""Time intelligence (PR-13).

Detect the primary time dimension (with a data-inferred grain + drill granularities) and
derive per-measure MTD / YoY / rolling variants, so the semantic layer can compute time
comparisons. Deterministic, default-on, emitted natively (MetricFlow agg_time_dimension +
cumulative/derived metrics). Driven on an orders-with-order_date fixture.
"""
from __future__ import annotations

import datetime as dt

import pyarrow as pa
import yaml
from goldenmatch.semantic import discover_semantic_model
from goldenmatch.semantic.discovery.measures import Measure


def _orders_daily() -> pa.Table:
    return pa.table({
        "order_id": ["o1", "o2", "o3", "o4"],
        "order_date": pa.array(
            [dt.date(2026, 1, 3), dt.date(2026, 1, 5), dt.date(2026, 2, 9), dt.date(2026, 3, 2)],
            type=pa.date32(),
        ),
        "amount": [10.0, 20.0, 30.0, 40.0],
    })


def _monthly() -> pa.Table:
    # every value on a month start -> grain should be inferred as "month".
    return pa.table({
        "snap_id": ["s1", "s2", "s3"],
        "period": pa.array(
            [dt.date(2026, 1, 1), dt.date(2026, 2, 1), dt.date(2026, 3, 1)], type=pa.date32()
        ),
        "revenue": [100.0, 200.0, 300.0],
    })


# --- time dimension detection + grain inference ---------------------------------


def test_discover_time_dimension_infers_day_grain():
    from goldenmatch.semantic.discovery.measures import Dimension
    from goldenmatch.semantic.discovery.time_intelligence import discover_time_dimension

    dims = [Dimension(column="order_date", kind="date")]
    td = discover_time_dimension(_orders_daily(), dims, table_name="orders")
    assert td is not None
    assert td.column == "order_date"
    assert td.grain == "day"
    assert td.granularities == ["day", "week", "month", "quarter", "year"]


def test_grain_inferred_as_month_for_month_starts():
    from goldenmatch.semantic.discovery.measures import Dimension
    from goldenmatch.semantic.discovery.time_intelligence import discover_time_dimension

    td = discover_time_dimension(_monthly(), [Dimension(column="period", kind="date")],
                                 table_name="snap")
    assert td.grain == "month"
    assert td.granularities == ["month", "quarter", "year"]


def test_no_time_dimension_without_a_date_column():
    from goldenmatch.semantic.discovery.measures import Dimension
    from goldenmatch.semantic.discovery.time_intelligence import discover_time_dimension

    assert discover_time_dimension(_orders_daily(), [Dimension(column="x", kind="categorical")],
                                   table_name="t") is None


# --- time metric variants -------------------------------------------------------


def test_time_metrics_mtd_yoy_rolling_per_measure():
    from goldenmatch.semantic.discovery.time_intelligence import (
        TimeDimension,
        discover_time_metrics,
    )

    measures = [Measure(column="amount", aggregations=["sum"], safe_to_sum=True)]
    td = TimeDimension(table="orders", column="order_date", grain="day",
                       granularities=["day", "month", "year"])
    tms = discover_time_metrics(measures, td)
    by = {m.name: m for m in tms}
    assert by["amount_mtd"].kind == "mtd"
    assert by["amount_yoy"].kind == "yoy"
    assert by["amount_rolling_7d"].kind == "rolling"


# --- integration + native MetricFlow emit ---------------------------------------


def test_time_intelligence_flows_through_model_and_to_dict():
    m = discover_semantic_model({"orders": _orders_daily()})
    assert m.time_dimensions and m.time_dimensions[0].column == "order_date"
    assert any(tm.name == "amount_mtd" for tm in m.time_metrics)
    d = m.to_dict()
    assert d["time_dimensions"][0]["grain"] == "day"
    assert any(x["name"] == "amount_mtd" for x in d["time_metrics"])


def test_metricflow_emits_agg_time_dimension_and_cumulative_metric():
    m = discover_semantic_model({"orders": _orders_daily()})
    doc = yaml.safe_load(m.yaml)
    sm = doc["semantic_models"][0]
    # native agg_time_dimension set on the model.
    assert sm["defaults"]["agg_time_dimension"] == "order_date"
    # MTD emitted as a native cumulative metric.
    cumulative = [x for x in doc.get("metrics", []) if x.get("type") == "cumulative"]
    assert any(x["name"] == "amount_mtd" for x in cumulative)
