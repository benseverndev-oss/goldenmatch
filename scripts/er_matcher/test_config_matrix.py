"""Tests for the pure config-matrix expansion (SP2 Task 4).

Expands a base training config into the two quantization variants the
benchmark runs (bf16-only LoRA vs QLoRA 4-bit) -- pure stdlib logic, no
torch/network (mirrors scripts/er_matcher/test_gpu_tiers.py conventions)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from config_matrix import expand_configs  # noqa: E402


def test_expands_to_two_named_variants():
    base = {"bf16": True, "qlora_4bit": False, "epochs": 2, "lora_r": 16}
    out = expand_configs(base)
    assert [name for name, _ in out] == ["bf16-lora", "qlora-4bit"]
    by_name = dict(out)
    assert by_name["bf16-lora"]["qlora_4bit"] is False
    assert by_name["qlora-4bit"]["qlora_4bit"] is True


def test_bf16_stays_true_in_both_variants():
    out = expand_configs({"bf16": True, "qlora_4bit": False})
    for _, cfg in out:
        assert cfg["bf16"] is True


def test_does_not_mutate_base():
    base = {"bf16": True, "qlora_4bit": False, "epochs": 2}
    expand_configs(base)
    assert base == {"bf16": True, "qlora_4bit": False, "epochs": 2}


def test_carries_other_fields_through():
    out = dict(expand_configs({"bf16": True, "qlora_4bit": False, "lora_r": 16, "epochs": 2}))
    assert out["bf16-lora"]["lora_r"] == 16 and out["bf16-lora"]["epochs"] == 2
