#!/usr/bin/env python3
"""qis-gate: a fail-on-regression QUALITY gate for zero-config dedupe AT SCALE.

Why this exists
---------------
Zero-config entity-resolution quality (F1) was validated only at <=20K rows
(the autoconfig anchor gate, the gym-gate, Febrl3), while zero-config decides
everything from a 1,000-row sample and switches code paths at 50K (learned
blocking) and 100K (controller refuse + planner rungs). A quality regression
living in the >=500K paths was therefore structurally below the reach of every
gate that could fail -- and it sat broken for ~2 months before anyone noticed,
because the only harnesses that measure quality at that scale
(`quality_invariant_scale.py`, `scale_audit_5m.py`, the ER head-to-head bench)
are all `workflow_dispatch`-only and REPORT-ONLY (never assert a floor).

This gate closes that gap. It reuses the existing `quality_invariant_scale`
(QIS) harness -- which already generates LABELED, prefix-stable data at scale
and computes real F1 -- and turns it into an assertion:

  For a matrix of row counts that BRACKETS the behaviour-change thresholds
  (50K / 100K / 500K / 1M / 5M), run TRUE zero-config `dedupe_df` (no config
  passed -- exactly what regressed) on the labeled data, score pairwise F1, and
  FAIL if any rung breaches one of three complementary checks:

    1. scale-invariance  -- a larger rung's F1 must not fall more than
       ``--scale-tol`` below the smallest ("reference") rung's F1. QIS data is
       prefix-stable across N, so a scale-invariant zero-config gives ~equal F1
       at every rung; a scale-SPECIFIC regression (good small, bad large) trips
       this WITHOUT needing any historical baseline. This directly encodes the
       product's "scale-invariant correctness" commitment.
    2. baseline-delta    -- a rung's F1 must not fall more than ``--delta-tol``
       below the committed baseline scorecard (drift over time; blessable).
    3. absolute-floor    -- a rung's F1 must clear ``--abs-floor`` (a hard
       "zero-config must be at least this good" backstop for uniform decay).

Modes: ``--check`` (assert; exit 1 on any breach) and ``--bless`` (regenerate
the baseline scorecard from the current run and write it). Mirrors the repo's
existing gate idiom (``throughput_perf_gate.py --check`` / the gym-gate /
``scripts/suggest_quality`` bless flow).

The measurement (QIS `run_rung` + `score_quality`) is imported lazily so this
module's PURE assertion logic (``evaluate_gate``) is unit-testable with no
goldenmatch install -- see ``scripts/test_qis_gate.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO / "scripts" / "baselines" / "qis_scorecard.json"

# Row-count matrix. The tiers bracket the zero-config behaviour-change points:
# 50K = learned-blocking flip; 100K = controller-refuse + planner simple/fast-box
# rung; 500K/1M = the >=1000-row-sample projection regime (where the mid-May
# regression lived); 5M = distributed-golden / duckdb-pairs rung.
TIERS: dict[str, list[int]] = {
    "ci": [50_000, 100_000, 500_000, 1_000_000],
    "heavy": [5_000_000],
}

# Defaults (overridable via CLI). Deliberately conservative so the gate flags
# real regressions, not IEEE-noise or a single-cluster tie-break.
DEFAULT_SCALE_TOL = 0.03  # a larger rung may sit at most this far below the reference rung
DEFAULT_DELTA_TOL = 0.02  # a rung may sit at most this far below its committed baseline
DEFAULT_ABS_FLOOR = 0.80  # hard minimum pairwise F1 for zero-config at any gated scale
METRIC = "pairwise"       # headline metric; b_cubed/cluster are recorded for context


@dataclass
class Violation:
    rung: int
    check: str      # "scale_invariance" | "baseline_delta" | "absolute_floor"
    f1: float
    threshold: float
    detail: str

    def line(self) -> str:
        return (
            f"[{self.check}] n={self.rung}: f1={self.f1:.4f} < {self.threshold:.4f} "
            f"({self.detail})"
        )


@dataclass
class GateResult:
    rung_f1: dict[int, float]
    reference_n: int
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def evaluate_gate(
    rung_f1: dict[int, Optional[float]],
    baseline: Optional[dict[str, float]],
    *,
    rung_refused: Optional[dict[int, bool]] = None,
    scale_tol: float = DEFAULT_SCALE_TOL,
    delta_tol: float = DEFAULT_DELTA_TOL,
    abs_floor: float = DEFAULT_ABS_FLOOR,
) -> GateResult:
    """PURE gate logic: map measured per-rung F1 (+ refuse verdicts + optional
    committed baseline) to a list of violations. No IO, no goldenmatch --
    unit-testable.

    ``rung_f1`` maps row-count -> measured pairwise F1, or None if that rung
    REFUSED (the controller committed a RED config on a >=100K input and
    ``dedupe_df`` raised; we do NOT force-run the degenerate config -- it can OOM
    at scale). ``rung_refused`` maps row-count -> whether it refused. ``baseline``
    maps STRING row-count -> blessed F1 (JSON-safe keys), or None to skip the
    baseline-delta check (first-ever run before a bless).

    A refuse is itself a violation: refusing at scale while the reference rung
    stays confident is the scale-regression fingerprint. The smallest rung is the
    reference; if IT refuses, that is flagged directly (no valid reference to
    compare against)."""
    if not rung_f1:
        raise ValueError("evaluate_gate: no rung measurements supplied")
    refused = rung_refused or {}

    reference_n = min(rung_f1)
    reference_f1 = rung_f1[reference_n]
    reference_refused = refused.get(reference_n, False)
    res = GateResult(rung_f1=dict(rung_f1), reference_n=reference_n)

    for n in sorted(rung_f1):
        f1 = rung_f1[n]

        # A REFUSED rung has no F1 to floor/compare -- the refusal is the signal.
        if refused.get(n) or f1 is None:
            if n == reference_n:
                res.violations.append(
                    Violation(n, "scale_invariance", 0.0, abs_floor,
                              "zero-config REFUSED (RED config) even at the reference "
                              "scale -- no confident baseline could be established")
                )
            elif not reference_refused:
                res.violations.append(
                    Violation(n, "scale_invariance", 0.0, reference_f1 or 0.0,
                              f"zero-config REFUSED (RED config) at n={n} while confident "
                              f"at the n={reference_n} reference (f1={reference_f1:.4f})")
                )
            continue

        # (3) absolute floor -- every measured rung, including the reference.
        if f1 < abs_floor:
            res.violations.append(
                Violation(n, "absolute_floor", f1, abs_floor,
                          f"below the hard zero-config floor {abs_floor:.2f}")
            )

        # (1) scale-invariance -- rungs above a confident reference.
        if n > reference_n and not reference_refused and reference_f1 is not None:
            inv_threshold = reference_f1 - scale_tol
            if f1 < inv_threshold:
                res.violations.append(
                    Violation(n, "scale_invariance", f1, inv_threshold,
                              f"drops {reference_f1 - f1:.4f} below the n={reference_n} "
                              f"reference f1={reference_f1:.4f}")
                )

        # (2) baseline drift -- only when a committed baseline exists for this rung.
        if baseline is not None:
            base = baseline.get(str(n))
            if base is not None:
                base_threshold = base - delta_tol
                if f1 < base_threshold:
                    res.violations.append(
                        Violation(n, "baseline_delta", f1, base_threshold,
                                  f"drops {base - f1:.4f} below baseline f1={base:.4f}")
                    )

    return res


# --------------------------------------------------------------------------- IO


def load_baseline(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def measure_rungs(rungs: list[int], *, seed: int, shape: str, corruption: str) -> dict[int, dict]:
    """Run TRUE zero-config dedupe on the labeled QIS data at each rung and
    return row-count -> {pairwise, b_cubed, cluster, red}. Imports QIS (and thus
    goldenmatch) lazily so the pure gate logic stays importable without them.

    ``dedupe_df(df)`` RAISES ``ControllerNotConfidentError`` when the controller
    commits a RED config on a >=100K input (the refuse-at-scale guard). For a
    QUALITY gate we must still measure what zero-config WOULD produce, so we catch
    the refusal and re-run with ``confidence_required=False`` (runs the committed
    config anyway) and record ``red=True`` as diagnostic context. A rung that goes
    RED while the smaller rungs stay GREEN is a strong hint of a scale-specific
    regression -- but the gate's pass/fail is decided on measurable F1, not the
    verdict (RED with intact F1 is not necessarily a quality regression)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import quality_invariant_scale as qis  # noqa: E402
    import goldenmatch  # noqa: E402
    from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError  # noqa: E402

    out: dict[int, dict] = {}
    for n in rungs:
        df, gt = qis.generate_with_gt(n, seed=seed, shape=shape, corruption=corruption)
        try:
            result = goldenmatch.dedupe_df(df)
        except ControllerNotConfidentError:
            # RED config on a >=100K input. Do NOT re-run with
            # confidence_required=True/False to force it -- the degenerate config
            # can blow up to near-cartesian pairs and OOM the runner at scale.
            # The refusal IS the signal; record it and move on.
            out[n] = {"refused": True,
                      "pairwise": {"f1": None, "p": None, "r": None},
                      "b_cubed": {"f1": None, "p": None, "r": None},
                      "cluster": {"f1": None, "p": None, "r": None}}
            continue
        predicted: dict[int, list[int]] = {}
        for cid, c in (result.clusters or {}).items():
            members = c.get("members") or []
            if len(members) > 1:
                predicted[int(cid)] = list(members)
        rec = qis.score_quality(predicted, gt)
        rec["refused"] = False
        out[n] = rec
    return out


def _scorecard_from_records(records: dict[int, dict], *, seed: int, shape: str,
                            corruption: str) -> dict:
    """Build the committed baseline scorecard from measured rung records. Refused
    rungs (no F1) are recorded in ``detail`` but omitted from the flat ``f1`` map
    that drives the baseline-delta check -- there is no confident value to bless."""
    def _r(x):
        return None if x is None else round(x, 6)

    f1 = {str(n): _r(records[n][METRIC]["f1"])
          for n in sorted(records) if not records[n].get("refused")}
    full = {
        str(n): {
            "pairwise": {k: _r(records[n]["pairwise"][k]) for k in ("f1", "p", "r")},
            "b_cubed": {k: _r(records[n]["b_cubed"][k]) for k in ("f1", "p", "r")},
            "cluster": {k: _r(records[n]["cluster"][k]) for k in ("f1", "p", "r")},
            "refused": bool(records[n].get("refused")),
        }
        for n in sorted(records)
    }
    return {
        "_comment": "Baseline for scripts/qis_gate.py. Regenerate with `qis_gate.py "
                    "--bless` via the bench-quality-scale workflow (bless in CI, not "
                    "locally -- native/version parity). Headline metric = pairwise f1.",
        "metric": METRIC,
        "seed": seed,
        "shape": shape,
        "corruption": corruption,
        "f1": f1,          # the flat rung -> f1 map evaluate_gate() reads
        "detail": full,    # p/r + b_cubed/cluster for trend context
    }


def write_step_summary(result: GateResult, records: dict[int, dict]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## qis-gate: zero-config quality at scale", ""]
    lines.append("| rows | pairwise F1 | B³ F1 | cluster F1 | controller |")
    lines.append("| ---: | ---: | ---: | ---: | :--- |")

    def _f(x):
        return "—" if x is None else f"{x:.4f}"

    for n in sorted(records):
        r = records[n]
        verdict = "🔴 RED (refused)" if r.get("refused") else "🟢 confident"
        lines.append(f"| {n:,} | {_f(r['pairwise']['f1'])} | "
                     f"{_f(r['b_cubed']['f1'])} | {_f(r['cluster']['f1'])} | {verdict} |")
    lines.append("")
    if result.ok:
        lines.append(f"🟢 all rungs within tolerance (reference n={result.reference_n:,}).")
    else:
        lines.append(f"🔴 **{len(result.violations)} violation(s):**")
        for v in result.violations:
            lines.append(f"- {v.line()}")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", choices=sorted(TIERS), default="ci",
                    help="which row-count matrix to run (ci: <=1M, heavy: 5M)")
    ap.add_argument("--rows", type=int, nargs="*",
                    help="explicit row counts (overrides --tier)")
    ap.add_argument("--mode", choices=["check", "bless"], default="check")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--shape", default="realistic")
    ap.add_argument("--corruption", default="light")
    ap.add_argument("--scale-tol", type=float, default=DEFAULT_SCALE_TOL)
    ap.add_argument("--delta-tol", type=float, default=DEFAULT_DELTA_TOL)
    ap.add_argument("--abs-floor", type=float, default=DEFAULT_ABS_FLOOR)
    ap.add_argument("--out-json", type=Path, help="write the full per-rung records here")
    args = ap.parse_args(argv)

    rungs = args.rows if args.rows else TIERS[args.tier]
    print(f"qis-gate {args.mode}: rungs={rungs} shape={args.shape} corruption={args.corruption}")

    records = measure_rungs(rungs, seed=args.seed, shape=args.shape, corruption=args.corruption)
    rung_f1 = {n: records[n][METRIC]["f1"] for n in records}
    rung_refused = {n: bool(records[n].get("refused")) for n in records}
    for n in sorted(rung_f1):
        f1, refused = rung_f1[n], rung_refused[n]
        shown = "REFUSED (RED)" if refused else f"{METRIC}_f1={f1:.4f}"
        print(f"  n={n:>9,}  {shown}")

    if args.out_json:
        args.out_json.write_text(json.dumps(
            {str(n): records[n] for n in sorted(records)}, indent=2, default=str))

    if args.mode == "bless":
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        card = _scorecard_from_records(records, seed=args.seed, shape=args.shape,
                                       corruption=args.corruption)
        args.baseline.write_text(json.dumps(card, indent=2) + "\n")
        print(f"blessed baseline -> {args.baseline}")
        return 0

    baseline = load_baseline(args.baseline)
    base_f1 = (baseline or {}).get("f1") if baseline else None
    result = evaluate_gate(rung_f1, base_f1, rung_refused=rung_refused,
                           scale_tol=args.scale_tol, delta_tol=args.delta_tol,
                           abs_floor=args.abs_floor)
    write_step_summary(result, records)

    if result.ok:
        print(f"OK: all {len(rung_f1)} rungs within tolerance "
              f"(reference n={result.reference_n:,}, f1={rung_f1[result.reference_n]:.4f})")
        return 0
    print(f"::error::qis-gate FAILED with {len(result.violations)} violation(s):", file=sys.stderr)
    for v in result.violations:
        print(f"::error::{v.line()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
