"""Tests for the local bf16-vs-QLoRA benchmark orchestrator (SP2 Task 8).

Pure stdlib + json only -- no torch/modal/network (mirrors
scripts/er_matcher/test_perf_report.py + test_gpu_tiers.py conventions).
The Modal-calling ``main`` execute step is NOT exercised here; these tests
cover only ``load_sweep_metrics`` / ``assemble_scorecard`` / ``format_scorecard``."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from run_benchmark import (  # noqa: E402
    assemble_scorecard,
    format_scorecard,
    load_sweep_metrics,
)


def _bf16_metrics(total_steps: int = 995) -> dict:
    return {
        "peak_mem_gb": 30.0,
        "smoke_steps": 100,
        "smoke_wall_s": 200.0,
        "gpu_util": 0.95,
        "total_steps": total_steps,
        "learning_curve": [{"frac": 0.5, "eval_loss": 0.5}, {"frac": 1.0, "eval_loss": 0.4}],
    }


def _qlora_metrics(total_steps: int = 995) -> dict:
    return {
        "peak_mem_gb": 19.5,
        "smoke_steps": 100,
        "smoke_wall_s": 220.0,
        "gpu_util": 0.90,
        "total_steps": total_steps,
        "learning_curve": [{"frac": 0.5, "eval_loss": 0.55}, {"frac": 1.0, "eval_loss": 0.45}],
    }


def test_load_sweep_metrics_reads_both_files(tmp_path):
    bf16 = _bf16_metrics()
    qlora = _qlora_metrics()
    (tmp_path / "sweep_metrics_bf16-lora.json").write_text(json.dumps(bf16))
    (tmp_path / "sweep_metrics_qlora-4bit.json").write_text(json.dumps(qlora))

    out = load_sweep_metrics(tmp_path)

    assert set(out.keys()) == {"bf16-lora", "qlora-4bit"}
    assert out["bf16-lora"] == bf16
    assert out["qlora-4bit"] == qlora


def test_load_sweep_metrics_skips_missing_file_gracefully(tmp_path, capsys):
    (tmp_path / "sweep_metrics_bf16-lora.json").write_text(json.dumps(_bf16_metrics()))
    # qlora-4bit file intentionally absent

    out = load_sweep_metrics(tmp_path)

    assert set(out.keys()) == {"bf16-lora"}
    captured = capsys.readouterr()
    assert "qlora-4bit" in captured.out


def test_assemble_scorecard_two_rows_correct_tiers_and_measured_steps():
    metrics_by_config = {
        "bf16-lora": _bf16_metrics(total_steps=995),
        "qlora-4bit": _qlora_metrics(total_steps=995),
    }

    rows = assemble_scorecard(metrics_by_config)

    assert len(rows) == 2
    by_config = {r["config"]: r for r in rows}
    assert by_config["bf16-lora"]["fits_tier"] == "A100-40GB"
    assert by_config["qlora-4bit"]["fits_tier"] == "L4"
    assert "winner" not in by_config["bf16-lora"]
    assert "winner" not in by_config["qlora-4bit"]

    # uses each metrics' MEASURED total_steps -- a different total_steps changes cost
    metrics_by_config_more_steps = {
        "bf16-lora": _bf16_metrics(total_steps=1990),
        "qlora-4bit": _qlora_metrics(total_steps=995),
    }
    rows_more_steps = assemble_scorecard(metrics_by_config_more_steps)
    by_config_more = {r["config"]: r for r in rows_more_steps}
    assert by_config_more["bf16-lora"]["full_cost_usd"] > by_config["bf16-lora"]["full_cost_usd"]
    # qlora-4bit unchanged since its total_steps didn't change
    assert by_config_more["qlora-4bit"]["full_cost_usd"] == by_config["qlora-4bit"]["full_cost_usd"]


def test_format_scorecard_contains_both_configs():
    metrics_by_config = {
        "bf16-lora": _bf16_metrics(),
        "qlora-4bit": _qlora_metrics(),
    }
    rows = assemble_scorecard(metrics_by_config)

    table = format_scorecard(rows)

    assert isinstance(table, str)
    assert "bf16-lora" in table
    assert "qlora-4bit" in table
