"""Compare GOLDENMATCH_BLOCKING_COST_AWARE OFF (0) vs ON (1) on the WEIGHTED
zero-config path — the path `build_blocking`'s cost-aware demotion (#2021) actually
affects (`run_goldenmatch.py --mode zeroconfig`).

Reads per-(shape, flag) result + eval JSONs produced by the bench job and emits a
markdown verdict. Gate semantics that clear the default flip:

  * every shape: F1(ON) >= F1(OFF) - ``--f1-tol``  (no F1 regression), AND
  * bibliographic shape: OFF == ON on candidate pairs (the domain routing must make
    cost-aware a NO-OP there -- a publication year is a legitimate blocking signal).

The WIN it documents (not gated, since it's the whole point): on the person shape,
ON collapses candidate pairs vs OFF (the birth_year-primary explosion is demoted).

Usage:
    python compare_cost_aware.py --dir ca_out --shapes person,biblio \
        --out compare_cost_aware.md [--fail-on-regression] [--f1-tol 0.01]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _load(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 -- a missing/failed run is reported, not fatal
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _f1(evalj: dict) -> float | None:
    # evaluate.py writes {"pairwise": {"f1": ...}, "b_cubed": {...}}
    pw = evalj.get("pairwise") or {}
    v = pw.get("f1")
    return float(v) if isinstance(v, (int, float)) else None


def _pairs(resj: dict) -> int | None:
    v = resj.get("scored_pairs")
    return int(v) if isinstance(v, (int, float)) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=pathlib.Path, required=True)
    ap.add_argument("--shapes", default="person,biblio")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--f1-tol", type=float, default=0.01)
    ap.add_argument("--fail-on-regression", action="store_true")
    args = ap.parse_args()

    shapes = [s.strip() for s in args.shapes.split(",") if s.strip()]
    lines = ["# Cost-aware blocking — OFF vs ON (weighted zero-config)", ""]
    lines.append("| shape | F1 OFF | F1 ON | ΔF1 | pairs OFF | pairs ON | pair Δ | verdict |")
    lines.append("|---|---|---|---|---|---|---|---|")

    violations: list[str] = []
    for shape in shapes:
        off_eval = _load(args.dir / f"{shape}_flag0_eval.json")
        on_eval = _load(args.dir / f"{shape}_flag1_eval.json")
        off_res = _load(args.dir / f"{shape}_flag0.json")
        on_res = _load(args.dir / f"{shape}_flag1.json")

        f1_off, f1_on = _f1(off_eval), _f1(on_eval)
        p_off, p_on = _pairs(off_res), _pairs(on_res)

        verdict = "ok"
        # F1 non-regression (every shape).
        if f1_off is not None and f1_on is not None:
            if f1_on < f1_off - args.f1_tol:
                verdict = "F1 REGRESSION"
                violations.append(
                    f"{shape}: F1 {f1_off:.4f} -> {f1_on:.4f} (> {args.f1_tol} drop)")
        else:
            verdict = "unmeasurable"
            violations.append(f"{shape}: F1 unmeasurable (OFF={f1_off} ON={f1_on})")

        # Bibliographic must be domain-routed to a NO-OP (OFF == ON on pairs).
        if shape in ("biblio", "bibliographic") and p_off is not None and p_on is not None:
            if p_off != p_on:
                verdict = "BIBLIO NOT EXEMPT"
                violations.append(
                    f"{shape}: cost-aware changed candidate pairs on bibliographic "
                    f"data ({p_off} -> {p_on}); the year primary must be preserved")

        def _fmt(v: object) -> str:
            return "-" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))

        df1 = "-" if (f1_off is None or f1_on is None) else f"{f1_on - f1_off:+.4f}"
        dp = "-" if (p_off is None or p_on is None) else (
            f"{(p_on - p_off) / p_off:+.1%}" if p_off else str(p_on - p_off))
        lines.append(
            f"| {shape} | {_fmt(f1_off)} | {_fmt(f1_on)} | {df1} | "
            f"{_fmt(p_off)} | {_fmt(p_on)} | {dp} | {verdict} |")

    lines.append("")
    if violations:
        lines.append("## Violations")
        lines.extend(f"- {v}" for v in violations)
    else:
        lines.append("All shapes: no F1 regression; bibliographic is domain-exempt (OFF == ON). ✅")

    md = "\n".join(lines) + "\n"
    args.out.write_text(md)
    print(md)
    summary = pathlib.Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", ""))
    if str(summary):
        try:
            with summary.open("a") as fh:
                fh.write(md)
        except Exception:  # noqa: BLE001 -- best-effort summary
            pass

    if violations and args.fail_on_regression:
        print(f"[compare_cost_aware] FAIL: {len(violations)} violation(s)", file=sys.stderr)
        sys.exit(1)
    print("[compare_cost_aware] OK" if not violations else
          "[compare_cost_aware] advisory violations (not gating)")


if __name__ == "__main__":
    main()
