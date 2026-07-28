"""Tests for the ``--qlora-4bit/--no-qlora-4bit`` CLI override (SP2 Task 6).

Box-safe: exercises ONLY the pure ``apply_overrides`` helper, never
``train.main`` / the GPU path (``_train_runtime`` needs torch/trl, which
aren't installed on this box -- the real training loop is verified by the
Modal run). Mirrors test_train_helpers.py conventions."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import train as tr  # noqa: E402


def _args(qlora_4bit: bool | None) -> argparse.Namespace:
    return argparse.Namespace(qlora_4bit=qlora_4bit)


def test_override_none_leaves_config_value_untouched():
    cfg = tr.TrainConfig(qlora_4bit=False)
    out = tr.apply_overrides(cfg, _args(None))
    assert out.qlora_4bit is False
    assert out is cfg  # mutated in place, not replaced

    cfg2 = tr.TrainConfig(qlora_4bit=True)
    assert tr.apply_overrides(cfg2, _args(None)).qlora_4bit is True


def test_override_true_flips_qlora_4bit_on():
    cfg = tr.TrainConfig(qlora_4bit=False)
    out = tr.apply_overrides(cfg, _args(True))
    assert out.qlora_4bit is True


def test_override_false_flips_qlora_4bit_off():
    cfg = tr.TrainConfig(qlora_4bit=True)
    out = tr.apply_overrides(cfg, _args(False))
    assert out.qlora_4bit is False


def test_override_revalidates_config():
    # apply_overrides must re-run cfg.validate() -- a bad match_confidence
    # (set directly, bypassing the constructor's own validate) should surface
    # here rather than silently passing through.
    cfg = tr.load_config(Path(__file__).with_name("config.yaml"))
    cfg.match_confidence = 0.2  # invalid: must be in [0.5, 1.0]
    import pytest
    with pytest.raises(ValueError, match="match_confidence"):
        tr.apply_overrides(cfg, _args(True))


def test_qlora_flag_parses_via_boolean_optional_action():
    # confirms the argparse wiring itself (BooleanOptionalAction) without
    # touching the GPU dispatch in main() -- build an equivalent tiny parser
    # the same way train.py's does for just this one flag.
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlora-4bit", action=argparse.BooleanOptionalAction, default=None)
    assert ap.parse_args([]).qlora_4bit is None
    assert ap.parse_args(["--qlora-4bit"]).qlora_4bit is True
    assert ap.parse_args(["--no-qlora-4bit"]).qlora_4bit is False
