#!/usr/bin/env python3
"""Local bf16-LoRA vs QLoRA A/B benchmark orchestrator (SP2 Task 8).

Turns the two MEASURED sweep-metrics files (emitted by the Modal-side
config-matrix sweep) into the bf16-vs-QLoRA A/B scorecard from
``perf_report.build_ab_scorecard``. The scorecard is data for a human
decision -- no config is picked as a "winner" here or in ``perf_report``.

Trigger note -- the full flow (this script only consumes the LAST step):
  1. ``modal run scripts/er_matcher/modal_train.py::benchmark``
     (launches both config-matrix variants' smoke-scale learning-curve sweeps)
  2. ``modal volume get er-matcher-out 'sweep_metrics_*.json' <sweep-dir>``
     (pulls the two measured metrics files down to a local directory)
  3. ``python scripts/er_matcher/run_benchmark.py --sweep-dir <sweep-dir>``
     (this script -- loads both files, builds the scorecard, prints it)

Steps 1-2 are the Modal-calling execute step and are NOT importable/testable
without Modal/network; they live in ``modal_train.py``. This module's helpers
(``load_sweep_metrics`` / ``assemble_scorecard`` / ``format_scorecard``) are
pure (stdlib + json only, no torch/modal/network) so they're unit-tested
without a GPU -- only ``main`` touches the filesystem for real, and even it
never calls Modal directly (it just reads whatever `modal volume get`
already pulled down)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

import perf_report  # noqa: E402

# Matches config_matrix.expand_configs's two named variants + modal_train.py's
# `sweep_metrics_{name}.json` output naming.
_SWEEP_CONFIG_NAMES = ("bf16-lora", "qlora-4bit")

_COLUMNS = (
    "config", "fits_tier", "full_cost_usd", "full_wall_h",
    "curve_slope", "final_eval_loss", "gpu_util", "peak_mem_gb",
)


def load_sweep_metrics(sweep_dir: Path) -> dict[str, dict[str, Any]]:
    """Read the two sweep-metrics JSON files out of ``sweep_dir``, keyed by
    config name (``"bf16-lora"`` / ``"qlora-4bit"``).

    Each file is ``sweep_metrics_<config_name>.json`` -- the shape
    ``modal_train.py::train_sweep`` writes via ``train.py --sweep
    --metrics-out``. A missing file is skipped gracefully (printed, not
    raised) so a partial pull (e.g. one variant's sweep still running) still
    yields a usable partial scorecard."""
    out: dict[str, dict[str, Any]] = {}
    for name in _SWEEP_CONFIG_NAMES:
        path = sweep_dir / f"sweep_metrics_{name}.json"
        if not path.exists():
            print(f"load_sweep_metrics: skipping {name!r} -- {path} not found")
            continue
        out[name] = json.loads(path.read_text())
    return out


def assemble_scorecard(metrics_by_config: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the A/B scorecard rows from loaded sweep metrics.

    Uses each config's own MEASURED ``total_steps`` (present in the metrics
    dict -- see ``_train_runtime.py``'s sweep-metrics emission) rather than
    recomputing it from rows/batch; delegates the actual scoring
    (tier-fit + cost extrapolation + learning-curve slope) to
    ``perf_report.build_ab_scorecard``, which already returns one row per
    entry with no "winner" picked."""
    entries = [
        {"config_name": name, "metrics": metrics, "total_steps": metrics["total_steps"]}
        for name, metrics in metrics_by_config.items()
    ]
    return perf_report.build_ab_scorecard(entries)


def format_scorecard(rows: list[dict[str, Any]]) -> str:
    """Render scorecard ``rows`` as a readable fixed-width table string."""
    def cell(row: dict[str, Any], col: str) -> str:
        value = row.get(col)
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    table_rows = [[cell(row, col) for col in _COLUMNS] for row in rows]
    widths = [
        max(len(col), *(len(r[i]) for r in table_rows)) if table_rows else len(col)
        for i, col in enumerate(_COLUMNS)
    ]

    def fmt_line(values: list[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, widths))

    lines = [fmt_line(list(_COLUMNS)), fmt_line(["-" * w for w in widths])]
    lines.extend(fmt_line(r) for r in table_rows)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Build the bf16-LoRA vs QLoRA A/B scorecard from a local pull of "
            "the Modal sweep-metrics files. See this module's docstring for "
            "the full modal run / modal volume get / run_benchmark.py flow."
        ),
    )
    ap.add_argument(
        "--sweep-dir", type=Path, default=Path("sweep_out"),
        help="Directory holding sweep_metrics_bf16-lora.json / "
             "sweep_metrics_qlora-4bit.json (target of `modal volume get "
             "er-matcher-out 'sweep_metrics_*.json' <sweep-dir>`).",
    )
    args = ap.parse_args(argv)

    metrics_by_config = load_sweep_metrics(args.sweep_dir)
    if not metrics_by_config:
        print(f"no sweep metrics found under {args.sweep_dir}; run the benchmark first "
              "(see this module's docstring for the modal run / modal volume get steps)")
        return 1

    rows = assemble_scorecard(metrics_by_config)
    print(format_scorecard(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
