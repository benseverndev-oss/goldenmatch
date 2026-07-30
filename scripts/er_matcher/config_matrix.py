#!/usr/bin/env python3
"""Pure config-matrix expansion for the ER-matcher training benchmark (SP2
Task 4).

The benchmark runs the trainer for TWO configs that differ only in
quantization: full bf16 LoRA vs QLoRA 4-bit. This module is stdlib-only
(dict copies), box-safe (no torch/network), and feeds the benchmark driver
that calls ``scripts/er_matcher/train.py`` once per variant.

``bf16`` is deliberately left untouched in both variants -- the 4-bit path
still uses ``bnb_4bit_compute_dtype=bfloat16`` and passes ``bf16=cfg.bf16``
through to the trainer, so flipping it off would wrongly route the QLoRA
run onto fp16.
"""
from __future__ import annotations


def expand_configs(base: dict) -> list[tuple[str, dict]]:
    """Expand ``base`` into the two named quantization variants.

    Returns ``[("bf16-lora", cfg), ("qlora-4bit", cfg)]`` where each ``cfg``
    is a full shallow copy of ``base`` with only ``qlora_4bit`` overridden.
    ``base`` is never mutated.
    """
    return [
        ("bf16-lora", {**base, "qlora_4bit": False}),
        ("qlora-4bit", {**base, "qlora_4bit": True}),
    ]
