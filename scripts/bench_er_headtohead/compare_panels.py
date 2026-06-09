#!/usr/bin/env python
"""Diff two probabilistic-panel runs (FS auto-config v1 vs v2) per dataset.

Reads the ``panel.json`` produced by run_panel.py for the GoldenMatch
probabilistic path under two settings of ``GOLDENMATCH_FS_AUTOCONFIG_V2`` and
emits a per-dataset F1/precision/recall delta table plus a verdict on whether v2
is safe to make the default. Splink rows are identical across the two runs (the
env var only affects GoldenMatch) and are carried as a reference column.

The verdict flags a REGRESSION when v2's GoldenMatch F1 drops by more than
``--eps`` on any dataset that ran under BOTH settings — the signal that gates
flipping ``GOLDENMATCH_FS_AUTOCONFIG_V2`` on by default (DBLP-ACM is the one we
can't measure on a laptop, so this lane is where it gets cleared).

Exit code is 0 by default (diagnostic). With ``--fail-on-regression`` it exits 1
when any dataset regressed, so the workflow step can be promoted to a gate later.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _gm_rows(panel_path: Path) -> dict[str, dict]:
    """Map dataset -> the goldenmatch row from a panel.json (status ok only)."""
    if not panel_path.exists():
        return {}
    rows = json.loads(panel_path.read_text())
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("engine") == "goldenmatch":
            out[r.get("dataset", "?")] = r
    return out


def _splink_f1(panel_path: Path) -> dict[str, float | None]:
    if not panel_path.exists():
        return {}
    rows = json.loads(panel_path.read_text())
    return {
        r.get("dataset", "?"): r.get("f1")
        for r in rows
        if r.get("engine") == "splink" and r.get("status") == "ok"
    }


def _fmt(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "-"


def _delta(a, b) -> str:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        d = b - a
        return f"{d:+.4f}"
    return "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", type=Path, required=True, help="panel.json with V2 OFF")
    ap.add_argument("--v2", type=Path, required=True, help="panel.json with V2 ON")
    ap.add_argument("--out", type=Path, default=None, help="write markdown here")
    ap.add_argument("--eps", type=float, default=0.005,
                    help="F1 drop beyond this counts as a regression")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit 1 if any dataset's v2 F1 regressed beyond --eps")
    args = ap.parse_args()

    v1 = _gm_rows(args.v1)
    v2 = _gm_rows(args.v2)
    splink = _splink_f1(args.v1) or _splink_f1(args.v2)
    datasets = sorted(set(v1) | set(v2))

    lines = [
        "## FS auto-config v1 vs v2 — GoldenMatch probabilistic path",
        "",
        "| Dataset | v1 F1 | v2 F1 | ΔF1 | v1 P | v2 P | ΔP | v1 R | v2 R | ΔR | Splink F1 | flag |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    ran_both: list[str] = []
    regressions: list[str] = []
    improvements: list[str] = []
    for ds in datasets:
        a, b = v1.get(ds, {}), v2.get(ds, {})
        a_ok, b_ok = a.get("status") == "ok", b.get("status") == "ok"
        flag = ""
        if a_ok and b_ok:
            ran_both.append(ds)
            df1 = b["f1"] - a["f1"]
            if df1 < -args.eps:
                flag = "⚠️ REGRESSION"
                regressions.append(f"{ds} ({df1:+.4f})")
            elif df1 > args.eps:
                flag = "✅ gain"
                improvements.append(f"{ds} ({df1:+.4f})")
            else:
                flag = "≈ neutral"
        elif not (a_ok or b_ok):
            flag = f"skipped ({a.get('status') or b.get('status') or '?'})"
        else:
            flag = "⚠️ status differs"
        lines.append(
            "| {ds} | {v1f1} | {v2f1} | {df1} | {v1p} | {v2p} | {dp} | {v1r} | {v2r} | {dr} | {sf1} | {flag} |".format(
                ds=ds,
                v1f1=_fmt(a.get("f1")), v2f1=_fmt(b.get("f1")), df1=_delta(a.get("f1"), b.get("f1")),
                v1p=_fmt(a.get("precision")), v2p=_fmt(b.get("precision")), dp=_delta(a.get("precision"), b.get("precision")),
                v1r=_fmt(a.get("recall")), v2r=_fmt(b.get("recall")), dr=_delta(a.get("recall"), b.get("recall")),
                sf1=_fmt(splink.get(ds)), flag=flag,
            )
        )

    lines.append("")
    if not ran_both:
        verdict = "**VERDICT: inconclusive** — no dataset ran under both settings (check dataset availability)."
    elif regressions:
        verdict = f"**VERDICT: v2 REGRESSES** {', '.join(regressions)} — do NOT flip the default yet."
    else:
        gain_str = f"gains on {', '.join(improvements)}; " if improvements else ""
        verdict = (
            f"**VERDICT: v2 safe to default** — {gain_str}no F1 regression beyond eps "
            f"on any of the {len(ran_both)} dataset(s) that ran under both settings "
            f"({', '.join(ran_both)})."
        )
    lines.append(verdict)
    md = "\n".join(lines) + "\n"

    print(md)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(md)

    if args.fail_on_regression and regressions:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
