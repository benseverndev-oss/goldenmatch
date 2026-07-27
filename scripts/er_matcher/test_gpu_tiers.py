"""Tests for the pure GPU-tier table + cheapest-fit selection (SP2 Task 1).

Feeds the ER-matcher training cost gate: given a measured peak GPU memory,
pick the cheapest Modal GPU tier that fits (mirrors scripts/er_matcher/test_perf_report.py
conventions -- pure stdlib logic, no GPU/torch/network)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from gpu_tiers import TIERS, select_cheapest_tier  # noqa: E402


def test_tiers_sorted_and_have_capacity_and_rate():
    for t in TIERS:
        assert t.capacity_gb > 0 and t.usd_per_hour > 0 and t.name
    # sorted ascending by price
    assert [t.usd_per_hour for t in TIERS] == sorted(t.usd_per_hour for t in TIERS)


def test_selects_cheapest_that_fits():
    t = select_cheapest_tier(19.5)          # fits L4 (24GB) at 0.9 headroom? 24*0.9=21.6 >= 19.5 yes
    assert t.name in {"L4", "A10G"}


def test_escalates_when_large():
    assert select_cheapest_tier(30.0).name.startswith("A100")


def test_raises_when_nothing_fits():
    with pytest.raises(ValueError):
        select_cheapest_tier(999.0)
