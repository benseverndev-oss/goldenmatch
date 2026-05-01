"""Fit a confidence calibrator from benchmark cases.

Runs the engine (uncalibrated) on a corpus, collects ``(score, correct)``
pairs from every emitted mapping, splits train/holdout, fits the requested
calibrator, and reports ECE on the holdout uncalibrated vs calibrated.

The ECE computation intentionally reuses ``infermap_bench.metrics`` so that
the fitting loop sees the same metric the benchmark optimizes — no risk of
accidentally gaming a different measure.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from infermap.calibration import (
    Calibrator,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    save_calibrator,
)
from infermap.engine import MapEngine

from .cases import Case
from .metrics import Prediction, expected_calibration_error


METHODS: dict[str, type[Calibrator]] = {
    "identity": IdentityCalibrator,
    "isotonic": IsotonicCalibrator,
    "platt": PlattCalibrator,
}


@dataclass
class FitReport:
    method: str
    n_total: int
    n_train: int
    n_holdout: int
    ece_holdout_raw: float
    ece_holdout_cal: float
    ece_all_raw: float
    ece_all_cal: float


def collect_predictions(cases: Iterable[Case]) -> list[Prediction]:
    """Run the engine uncalibrated on each case, return flat predictions list."""
    engine = MapEngine()
    out: list[Prediction] = []
    for case in cases:
        try:
            result = engine.map_schemas(case.source_schema, case.target_schema)
        except Exception:
            continue
        expected_set = frozenset(
            (m["source"], m["target"]) for m in case.expected.mappings
        )
        for m in result.mappings:
            key = (m.source, m.target)
            out.append(Prediction(confidence=float(m.confidence), correct=key in expected_set))
    return out


def _split(
    preds: list[Prediction], holdout: float, seed: int
) -> tuple[list[Prediction], list[Prediction]]:
    rng = np.random.default_rng(seed)
    n = len(preds)
    idx = np.arange(n)
    rng.shuffle(idx)
    n_hold = int(round(n * holdout))
    hold_idx = set(idx[:n_hold].tolist())
    train = [p for i, p in enumerate(preds) if i not in hold_idx]
    hold = [p for i, p in enumerate(preds) if i in hold_idx]
    return train, hold


def fit_calibrator(
    cases: list[Case],
    method: str,
    holdout: float = 0.3,
    seed: int = 42,
) -> tuple[Calibrator, FitReport]:
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; choose from {list(METHODS)}")
    preds = collect_predictions(cases)
    if not preds:
        raise RuntimeError("no predictions collected — the corpus produced no mappings")

    train, hold = _split(preds, holdout, seed)
    if not train:
        raise RuntimeError("train split is empty — reduce --holdout")

    cal = METHODS[method]()
    cal.fit(
        np.array([p.confidence for p in train], dtype=float),
        np.array([1.0 if p.correct else 0.0 for p in train], dtype=float),
    )

    def _apply(ps: list[Prediction]) -> list[Prediction]:
        if not ps:
            return ps
        raw = np.array([p.confidence for p in ps], dtype=float)
        new = cal.transform(raw)
        return [Prediction(confidence=float(c), correct=p.correct) for p, c in zip(ps, new)]

    ece_hold_raw = expected_calibration_error(hold) if hold else 0.0
    ece_hold_cal = expected_calibration_error(_apply(hold)) if hold else 0.0
    ece_all_raw = expected_calibration_error(preds)
    ece_all_cal = expected_calibration_error(_apply(preds))

    return cal, FitReport(
        method=method,
        n_total=len(preds),
        n_train=len(train),
        n_holdout=len(hold),
        ece_holdout_raw=ece_hold_raw,
        ece_holdout_cal=ece_hold_cal,
        ece_all_raw=ece_all_raw,
        ece_all_cal=ece_all_cal,
    )


def run_calibrate_command(
    only: str | None,
    method: str,
    output: str,
    holdout: float,
    seed: int,
    self_test: bool = False,
) -> FitReport:
    """Programmatic entrypoint used by the CLI and tests."""
    from .cases import load_case
    from .cli import BENCHMARK_ROOT, SELF_TEST_ROOT, _matches_filter, _load_synthetic_cases
    from .manifest import load_manifest

    root = SELF_TEST_ROOT if self_test else BENCHMARK_ROOT
    manifest_path = root / "manifest.json"
    refs = load_manifest(manifest_path)
    if only:
        refs = [r for r in refs if _matches_filter(r, only)]
    cases: list[Case] = [load_case(root, ref) for ref in refs]

    if not self_test and (not only or "synthetic" in (only or "")):
        synth_path = BENCHMARK_ROOT / "cases" / "synthetic" / "generated.json"
        if synth_path.exists() and (not only or only == "category:synthetic"):
            cases.extend(_load_synthetic_cases(synth_path))

    if not cases:
        raise RuntimeError("no cases matched — check --only filter")

    cal, report = fit_calibrator(cases, method=method, holdout=holdout, seed=seed)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    save_calibrator(cal, output)
    return report
