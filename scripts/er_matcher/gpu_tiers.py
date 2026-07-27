#!/usr/bin/env python3
"""Pure GPU-tier table + cheapest-fit selection for the ER-matcher training cost
gate (SP2 Task 1).

Rates are defaults; confirm current Modal pricing at run time -- Modal's
published per-GPU-hour rates change independently of this repo. This module
is stdlib-only (dataclasses), box-safe (no torch/network), and feeds
``scripts/er_matcher/perf_report.py``'s GO/NO-GO gate: given a training run's
measured peak GPU memory, pick the cheapest tier that fits with headroom.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    name: str
    capacity_gb: float
    usd_per_hour: float


# Sorted ascending by usd_per_hour. Note L4 has MORE capacity than A10G but is
# cheaper -- that's real Modal pricing, not a bug; keep sorted by price.
TIERS: list[Tier] = [
    Tier("L4", 24.0, 0.80),
    Tier("A10G", 22.0, 1.10),
    Tier("A100-40GB", 40.0, 2.10),
    Tier("A100-80GB", 80.0, 2.50),
]


def select_cheapest_tier(peak_mem_gb: float, *, headroom: float = 0.9) -> Tier:
    """Return the cheapest tier (by ``usd_per_hour``) whose ``capacity_gb *
    headroom >= peak_mem_gb``. Raises ``ValueError`` if no tier fits."""
    for tier in TIERS:
        if tier.capacity_gb * headroom >= peak_mem_gb:
            return tier
    raise ValueError(f"no GPU tier fits {peak_mem_gb} GB")
