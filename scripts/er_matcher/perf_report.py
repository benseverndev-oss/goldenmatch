#!/usr/bin/env python3
"""P3a perf-calibration GO/NO-GO gate for the ER-matcher training run.

The hard rule (plan §Phase 3): no multi-hour GPU run is launched cold. A cheap
smoke run (a few hundred steps on a small slice, on the cheapest adequate GPU)
emits measured metrics; this module turns those into a GO/NO-GO scorecard.

Like ``scripts/er_matcher/eval.py``'s ``evaluate_gate`` and ``scripts/qis_gate.py``,
the decision logic is PURE (numbers in, verdict out) and unit-tested WITHOUT a GPU
-- ``train.py``/``modal_train.py`` emit a ``smoke_metrics.json`` that the CLI feeds
here.

The four gate checks (plan §Phase 3a):
  1. GPU util >= floor      -- else the data/pack path is the bottleneck; fix it
                               before spending on the full run (a data-bound run
                               wastes GPU $).
  2. extrapolated $ <= budget -- the full run must fit the budget.
  3. learning curve still climbing at the chosen data size -- else more data/epochs
                               won't help (train no more than helps).
  4. peak mem <= GPU capacity -- confirms the GPU tier (else bump the tier).
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import gpu_tiers


# --- extrapolation (pure) ----------------------------------------------------
def extrapolate_full_run(
    *,
    smoke_steps: int,
    smoke_wall_s: float,
    total_steps: int,
    gpu_cost_per_hour_usd: float,
) -> dict[str, float]:
    """Project full-run wall-clock + $ from the smoke run's measured step rate.

    Linear in steps (SFT step time is ~constant once GPU-bound). Returns
    ``{s_per_step, full_wall_s, full_wall_h, full_cost_usd}``."""
    if smoke_steps <= 0:
        raise ValueError("smoke_steps must be > 0")
    s_per_step = smoke_wall_s / smoke_steps
    full_wall_s = s_per_step * total_steps
    full_wall_h = full_wall_s / 3600.0
    return {
        "s_per_step": s_per_step,
        "full_wall_s": full_wall_s,
        "full_wall_h": full_wall_h,
        "full_cost_usd": full_wall_h * gpu_cost_per_hour_usd,
    }


def extrapolate_on_tier(
    *,
    smoke_steps: int,
    smoke_wall_s: float,
    total_steps: int,
    tier: gpu_tiers.Tier,
) -> dict[str, float | str]:
    """Same projection as ``extrapolate_full_run`` but priced at ``tier``'s
    ``usd_per_hour``. Reuses ``extrapolate_full_run`` for the step-rate math
    (one source of truth) and tags the result with the tier name."""
    ext = extrapolate_full_run(
        smoke_steps=smoke_steps,
        smoke_wall_s=smoke_wall_s,
        total_steps=total_steps,
        gpu_cost_per_hour_usd=tier.usd_per_hour,
    )
    return {**ext, "tier": tier.name}


def extrapolate_cheapest(metrics: dict[str, Any], total_steps: int) -> dict[str, float | str]:
    """Pick the cheapest GPU tier that fits ``metrics["peak_mem_gb"]`` (via
    ``gpu_tiers.select_cheapest_tier``) and extrapolate the full-run cost on
    it. Returns the ``extrapolate_on_tier`` dict, including the chosen
    ``tier`` name and its ``usd_per_hour``."""
    tier = gpu_tiers.select_cheapest_tier(metrics["peak_mem_gb"])
    ext = extrapolate_on_tier(
        smoke_steps=metrics["smoke_steps"],
        smoke_wall_s=metrics["smoke_wall_s"],
        total_steps=total_steps,
        tier=tier,
    )
    return {**ext, "usd_per_hour": tier.usd_per_hour}


def learning_curve_slope(curve: list[dict[str, float]]) -> float:
    """Marginal eval-loss improvement per the LAST data-fraction step of the mini
    learning curve (10/25/50/100% slices). Curve entries: ``{frac, eval_loss}``,
    ascending by ``frac``. Returns ``prev_loss - last_loss`` (POSITIVE = still
    improving = more data would help). < 2 points -> 0.0 (can't tell → treat as
    flat, which fails the climbing check unless the floor is <= 0)."""
    pts = sorted(curve, key=lambda p: p["frac"])
    if len(pts) < 2:
        return 0.0
    return float(pts[-2]["eval_loss"] - pts[-1]["eval_loss"])


# --- gate (pure) -------------------------------------------------------------
@dataclass
class PerfGateResult:
    go: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    extrapolation: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "passed": ok, "detail": detail})
        if not ok:
            self.go = False


def evaluate_perf_gate(
    metrics: dict[str, Any],
    *,
    min_gpu_util: float = 0.90,
    budget_usd: float = 50.0,
    min_curve_slope: float = 0.0,
    total_steps: int | None = None,
    gpu_cost_per_hour_usd: float | None = None,
    gpu_capacity_gb: float | None = None,
) -> PerfGateResult:
    """Decide GO / NO-GO for the full run from a smoke-run ``metrics`` dict.

    ``metrics`` (emitted by train.py's smoke mode):
      ``gpu_util`` (0-1 mean), ``peak_mem_gb``, ``smoke_steps``, ``smoke_wall_s``,
      ``tokens_per_s``, ``learning_curve`` ([{frac, eval_loss}]),
      and optionally ``total_steps`` / ``gpu_cost_per_hour_usd`` / ``gpu_capacity_gb``
      (CLI flags override).

    Pure; unit-tested without a GPU."""
    res = PerfGateResult(go=True)

    util = float(metrics.get("gpu_util", 0.0))
    res.add(
        "gpu_util",
        util >= min_gpu_util,
        f"GPU util {util:.2%} vs floor {min_gpu_util:.0%} "
        f"({'GPU-bound' if util >= min_gpu_util else 'DATA-BOUND -- fix the pack/dataloader path'})",
    )

    steps = int(metrics.get("smoke_steps", 0))
    wall = float(metrics.get("smoke_wall_s", 0.0))
    total = total_steps if total_steps is not None else int(metrics.get("total_steps", 0))
    cost_h = (
        gpu_cost_per_hour_usd
        if gpu_cost_per_hour_usd is not None
        else float(metrics.get("gpu_cost_per_hour_usd", 0.0))
    )
    if steps > 0 and total > 0 and cost_h > 0:
        ext = extrapolate_full_run(
            smoke_steps=steps, smoke_wall_s=wall,
            total_steps=total, gpu_cost_per_hour_usd=cost_h,
        )
        res.extrapolation = ext
        res.add(
            "budget",
            ext["full_cost_usd"] <= budget_usd,
            f"extrapolated ${ext['full_cost_usd']:.2f} "
            f"({ext['full_wall_h']:.2f} h) vs budget ${budget_usd:.2f}",
        )
    else:
        res.add("budget", False, "cannot extrapolate: need smoke_steps/total_steps/gpu_cost_per_hour_usd")

    slope = learning_curve_slope(metrics.get("learning_curve", []))
    res.add(
        "learning_curve",
        slope > min_curve_slope,
        f"marginal eval-loss improvement {slope:+.4f} vs floor {min_curve_slope} "
        f"({'still climbing' if slope > min_curve_slope else 'plateaued -- more data/epochs will not help'})",
    )

    cap = gpu_capacity_gb if gpu_capacity_gb is not None else metrics.get("gpu_capacity_gb")
    peak = metrics.get("peak_mem_gb")
    if cap is not None and peak is not None:
        res.add(
            "memory",
            float(peak) <= float(cap),
            f"peak mem {float(peak):.1f} GB vs GPU capacity {float(cap):.1f} GB",
        )
    # memory check is advisory when capacity/peak absent (smoke may not report cap)
    return res


# --- CLI ---------------------------------------------------------------------
def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ER-matcher P3a perf GO/NO-GO gate.")
    ap.add_argument("--metrics", type=Path, required=True,
                    help="smoke_metrics.json emitted by train.py --smoke")
    ap.add_argument("--min-gpu-util", type=float, default=0.90)
    ap.add_argument("--budget-usd", type=float, default=50.0)
    ap.add_argument("--min-curve-slope", type=float, default=0.0)
    ap.add_argument("--total-steps", type=int, default=None)
    ap.add_argument("--gpu-cost-per-hour-usd", type=float, default=None)
    ap.add_argument("--gpu-capacity-gb", type=float, default=None)
    args = ap.parse_args(list(argv) if argv is not None else None)

    metrics = json.loads(args.metrics.read_text())
    gate = evaluate_perf_gate(
        metrics,
        min_gpu_util=args.min_gpu_util,
        budget_usd=args.budget_usd,
        min_curve_slope=args.min_curve_slope,
        total_steps=args.total_steps,
        gpu_cost_per_hour_usd=args.gpu_cost_per_hour_usd,
        gpu_capacity_gb=args.gpu_capacity_gb,
    )
    print(json.dumps({"go": gate.go, "checks": gate.checks,
                      "extrapolation": gate.extrapolation}, indent=2))
    print("\n" + ("GO -- launch the full run (P3b)" if gate.go
                  else "NO-GO -- fix the flagged check before spending on the full run"))
    return 0 if gate.go else 1


if __name__ == "__main__":
    raise SystemExit(main())
