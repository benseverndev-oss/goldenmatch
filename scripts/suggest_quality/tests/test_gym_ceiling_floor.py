"""Degenerate-ceiling guard: a dataset whose zero-config ceiling F1 is below the
floor is skipped from the gym recovery evaluation, so its raw-diagnostic
recovery blow-ups can't poison the headline mean.

Pins the fix for the historical_50k pathology: the weighted zero-config path
reaches only F1 ~0.26 there, the damage gaps are tiny, and the verify=False
convergence blindly over-applies threshold moves -> recovery_pct explodes to
-10x/-16x and drags headline_raw negative. recovery_pct is only meaningful
against a competent ceiling, so a degenerate ceiling is skipped like a no-gt
dataset.
"""
import math

import polars as pl

from scripts.suggest_quality import gym
from scripts.suggest_quality.datasets import Dataset


def _ds(name: str) -> Dataset:
    def _loader() -> tuple[pl.DataFrame, set]:
        df = pl.DataFrame({"name": ["a", "b", "c"]})
        return df, {(0, 1)}  # non-empty gt so it isn't skipped as blocking-shape

    return Dataset(name, "real", _loader)


class _Pert:
    """Minimal perturbation stand-in; only .name is read by the eval stub."""

    name = "threshold_too_low"


def _stub_pipeline(monkeypatch, ceilings_in_order: list[float]) -> None:
    """Stub the per-dataset ceiling build so _compute_f1 yields the supplied
    f1_ceiling values in dataset order, and make evaluate_perturbation cheap."""
    monkeypatch.setattr(gym, "_auto_configure_no_rerank", lambda df: object())
    monkeypatch.setattr(gym, "_run_config", lambda df, cfg: ({}, []))

    queue = list(ceilings_in_order)

    def _fake_compute(clusters, scored, gt):  # noqa: ARG001
        return queue.pop(0)

    monkeypatch.setattr(gym, "_compute_f1", _fake_compute)
    monkeypatch.setattr(
        gym, "evaluate_perturbation",
        lambda df, gt, pert, cfg, f1c: {"status": "ok", "name": pert.name},
    )


def _skip_records(records: list[dict]) -> set:
    return {r.get("dataset") for r in records
            if r.get("status") == "skipped_degenerate_ceiling"}


def _evaluated(records: list[dict]) -> set:
    # datasets with a real per-perturbation record (not a skip sentinel)
    return {r.get("dataset") for r in records
            if r.get("status") != "skipped_degenerate_ceiling"}


def test_degenerate_ceiling_dataset_is_skipped(monkeypatch) -> None:
    # datasets processed in list order -> ceilings consumed in that order.
    _stub_pipeline(monkeypatch, [0.96, 0.26])
    records = gym.run_catalog([_ds("competent"), _ds("degenerate")], [_Pert()])

    assert "competent" in _evaluated(records)   # competent ceiling -> evaluated
    assert "competent" not in _skip_records(records)
    # 0.26 < 0.50 floor -> NOT evaluated, but emits a VISIBLE skip sentinel so the
    # gate can tell a degenerate skip from an erroring/absent dataset.
    assert "degenerate" not in _evaluated(records)
    assert "degenerate" in _skip_records(records)


def test_nan_ceiling_is_skipped(monkeypatch) -> None:
    _stub_pipeline(monkeypatch, [math.nan])
    records = gym.run_catalog([_ds("nan_ceiling")], [_Pert()])
    assert _skip_records(records) == {"nan_ceiling"}
    assert _evaluated(records) == set()  # no per-perturbation records


def test_ceiling_at_floor_is_kept(monkeypatch) -> None:
    # The floor is inclusive (>= floor passes); a dataset exactly at the floor
    # is a valid target.
    _stub_pipeline(monkeypatch, [gym._CEILING_FLOOR_DEFAULT])
    records = gym.run_catalog([_ds("at_floor")], [_Pert()])
    assert _evaluated(records) == {"at_floor"}
    assert _skip_records(records) == set()


def test_floor_is_env_overridable(monkeypatch) -> None:
    # Raising the floor above a previously-competent ceiling now skips it.
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR", "0.98")
    _stub_pipeline(monkeypatch, [0.96])
    records = gym.run_catalog([_ds("now_too_low")], [_Pert()])
    assert _skip_records(records) == {"now_too_low"}


def test_ceiling_floor_reader(monkeypatch) -> None:
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR", raising=False)
    assert gym._ceiling_floor() == gym._CEILING_FLOOR_DEFAULT

    monkeypatch.setenv("GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR", "0.8")
    assert gym._ceiling_floor() == 0.8

    # unparseable / out-of-range fall back to the blessed default
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR", "nonsense")
    assert gym._ceiling_floor() == gym._CEILING_FLOOR_DEFAULT
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR", "1.5")
    assert gym._ceiling_floor() == gym._CEILING_FLOOR_DEFAULT
