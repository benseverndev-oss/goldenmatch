"""Integration tests for the advisory repair-plan wiring.

Guards the real ``ColumnType`` enum path: ``attach_repair_plan`` must resolve
``inferred_type`` via ``.value`` (``"date"``), NOT ``str(enum)``
(``"ColumnType.DATE"``). A string stand-in cannot catch a bad enum cast, so
these use a real ``ColumnContext`` with a real ``ColumnType``.
"""
from __future__ import annotations

import polars as pl
from goldenpipe.models.column_context import ColumnContext, ColumnType
from goldenpipe.models.context import PipeContext
from goldenpipe.repair_host import attach_repair_plan


def test_real_columntype_resolves_to_value_string():
    df = pl.DataFrame({"signup_date": ["2020-01-01", "2021-05-05"]})
    ctx = PipeContext()
    cc = ColumnContext(name="signup_date", inferred_type=ColumnType.DATE)
    findings = [{"column": "signup_date", "check": "future_dated", "message": "12 rows after today", "severity": "warning"}]
    plan = attach_repair_plan(ctx, findings, [cc], df)
    item = plan["repairs"][0]
    assert item["suggested_transforms"] == ["date_validate"]
    assert item["type_tag"] == "date"   # NOT "ColumnType.DATE"


def test_nonrepairable_finding_empty_plan():
    df = pl.DataFrame({"id": ["a", "b"]})
    ctx = PipeContext()
    cc = ColumnContext(name="id", inferred_type=ColumnType.STRING)
    plan = attach_repair_plan(ctx, [{"column": "id", "check": "unique", "message": "dupes", "severity": "warning"}], [cc], df)
    assert plan["repairs"] == []
    assert "repair_plan" not in ctx.reasoning   # no lines written
