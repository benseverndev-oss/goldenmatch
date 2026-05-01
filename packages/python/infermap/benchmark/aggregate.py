"""Aggregate Python and TS reports into a PR sticky comment."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from infermap_bench.compare import compute_delta

COMMENT_HEADER = "<!-- infermap-benchmark comment-schema-version=1 -->"
METRIC_KEYS = ("f1", "top1", "mrr", "ece")


def load_report(path: Path | None) -> dict | None:
    if path is None or not Path(path).exists():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_baseline(path: Path | None) -> dict | None:
    if path is None or not Path(path).exists():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def classify_verdict(delta: dict, threshold: float = 0.02) -> str:
    f1 = delta.get("f1", 0.0)
    top1 = delta.get("top1", 0.0)
    mrr = delta.get("mrr", 0.0)
    ece = delta.get("ece", 0.0)

    if f1 < -threshold:
        return "🛑 large regression"

    goodness = (f1, top1, mrr, -ece)

    if all(abs(g) < 0.005 for g in goodness):
        return "🟢 no change"

    has_positive = any(g > 0.005 for g in goodness)
    has_negative = any(g < -0.005 for g in goodness)

    if has_positive and has_negative:
        return "⚠️ mixed"

    if has_positive and not has_negative:
        return "✅ improved"

    if has_negative and not has_positive:
        return "🔻 regressed"

    return "⚠️ mixed"


def _arrow(delta: float, *, invert: bool = False) -> str:
    if invert:
        return "🔽" if delta <= 0 else "🔺"
    return "🔺" if delta >= 0 else "🔻"


def _render_headline_row(
    language: str,
    current: dict,
    baseline: dict | None,
    regression_threshold: float = 0.02,
) -> str:
    sc = current["scorecard"]["overall"]
    if baseline is None:
        return (
            f"| {language:<11} | **{sc['f1']:.3f}** "
            f"| {sc['top1']:.3f} "
            f"| {sc['mrr']:.3f} "
            f"| {sc['ece']:.3f} "
            f"| 🆕 first run |"
        )

    base_sc = baseline["scorecard"]["overall"]
    deltas = {k: float(sc[k]) - float(base_sc[k]) for k in METRIC_KEYS}
    verdict = classify_verdict(deltas, threshold=regression_threshold)

    return (
        f"| {language:<11} "
        f"| **{sc['f1']:.3f}** {_arrow(deltas['f1'])} {deltas['f1']:+.3f} "
        f"| {sc['top1']:.3f} {_arrow(deltas['top1'])} {deltas['top1']:+.3f} "
        f"| {sc['mrr']:.3f} {_arrow(deltas['mrr'])} {deltas['mrr']:+.3f} "
        f"| {sc['ece']:.3f} {_arrow(deltas['ece'], invert=True)} {deltas['ece']:+.3f} "
        f"| {verdict} |"
    )


def _render_failed_row(language: str) -> str:
    return f"| {language:<11} | — | — | — | — | 🛑 runner failed |"


def render_comment(
    python_report: dict | None,
    ts_report: dict | None,
    baseline: dict | None,
    regression_threshold: float = 0.02,
) -> str:
    lines = [COMMENT_HEADER, "", "## 🧭 infermap benchmark", ""]

    if python_report is None and ts_report is None:
        lines.append("> 🛑 **Both runners failed.** See job logs.")
        return "\n".join(lines) + "\n"

    if baseline is None:
        lines.append("**First run** — no baseline exists yet. This run establishes the initial scorecard.")
    else:
        commit = baseline.get("commit", "unknown")
        lines.append(f"**PR** vs baseline `{commit[:7]}`")

    lines.append("")
    lines.append("### Headline")
    lines.append("")
    lines.append("| language    | F1 Δ | top-1 Δ | MRR Δ | ECE Δ | verdict |")
    lines.append("|-------------|-----:|--------:|------:|------:|:--------|")

    base_py = baseline["python"] if baseline else None
    base_ts = baseline["typescript"] if baseline else None

    if python_report is not None:
        lines.append(_render_headline_row("Python", python_report, base_py, regression_threshold))
    else:
        lines.append(_render_failed_row("Python"))

    if ts_report is not None:
        lines.append(_render_headline_row("TypeScript", ts_report, base_ts, regression_threshold))
    else:
        lines.append(_render_failed_row("TypeScript"))

    lines.append("")

    if baseline:
        mover_lines: list[str] = []
        for lang_name, current, base_report in (
            ("Python", python_report, base_py),
            ("TypeScript", ts_report, base_ts),
        ):
            if current is None or base_report is None:
                continue
            delta = compute_delta(base_report, current)
            regressions, improvements = delta.top_movers(n=10, threshold=0.05)
            if not regressions and not improvements:
                continue
            mover_lines.append(f"**{lang_name}:**")
            mover_lines.append("")
            if regressions:
                mover_lines.append("*Regressed:*")
                mover_lines.append("| case | baseline | current | Δ |")
                mover_lines.append("|------|---------:|--------:|--:|")
                for cid, base_f1, curr_f1, d in regressions:
                    mover_lines.append(f"| `{cid}` | {base_f1:.3f} | {curr_f1:.3f} | {d:+.3f} |")
                mover_lines.append("")
            if improvements:
                mover_lines.append("*Improved:*")
                mover_lines.append("| case | baseline | current | Δ |")
                mover_lines.append("|------|---------:|--------:|--:|")
                for cid, base_f1, curr_f1, d in improvements:
                    mover_lines.append(f"| `{cid}` | {base_f1:.3f} | {curr_f1:.3f} | {d:+.3f} |")
                mover_lines.append("")

        if mover_lines:
            lines.append("<details>")
            lines.append("<summary>🔍 Cases that moved significantly</summary>")
            lines.append("")
            lines.extend(mover_lines)
            lines.append("</details>")
            lines.append("")

    crashed: list[tuple[str, str, str | None]] = []
    for lang_name, report in (("Python", python_report), ("TypeScript", ts_report)):
        if report is None:
            continue
        failed_ids = report.get("failed_cases", [])
        if not failed_ids:
            continue
        reasons = {
            pc["id"]: pc.get("failure_reason")
            for pc in report.get("per_case", [])
            if pc.get("id") in failed_ids
        }
        for cid in failed_ids:
            crashed.append((cid, lang_name, reasons.get(cid)))

    if crashed:
        lines.append("<details>")
        lines.append(f"<summary>⚠️ Cases that crashed the engine ({len(crashed)})</summary>")
        lines.append("")
        lines.append("| case | language | reason |")
        lines.append("|------|----------|--------|")
        for cid, lang_name, reason in crashed:
            reason_str = reason or "(no reason)"
            lines.append(f"| `{cid}` | {lang_name} | {reason_str} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines) + "\n"


def main_impl(
    python_path: Path,
    ts_path: Path,
    baseline_path: Path | None,
    markdown_path: Path,
    output_path: Path | None,
    regression_threshold: float = 0.02,
    fail_over: float | None = None,
) -> int:
    py_report = load_report(python_path)
    ts_report = load_report(ts_path)
    baseline = load_baseline(baseline_path)

    if py_report is None and ts_report is None and baseline is None:
        print("no reports to aggregate", file=sys.stderr)
        return 1

    markdown = render_comment(py_report, ts_report, baseline, regression_threshold)
    Path(markdown_path).write_text(markdown, encoding="utf-8")

    if output_path is not None:
        delta_data: dict = {}
        if baseline and py_report:
            d = compute_delta(baseline["python"], py_report)
            delta_data["python"] = {"overall": d.overall}
        if baseline and ts_report:
            d = compute_delta(baseline["typescript"], ts_report)
            delta_data["typescript"] = {"overall": d.overall}
        Path(output_path).write_text(json.dumps(delta_data, indent=2) + "\n", encoding="utf-8")

    if fail_over is not None and baseline is not None:
        max_regression = 0.0
        if py_report:
            d = compute_delta(baseline["python"], py_report)
            max_regression = min(max_regression, d.overall.get("f1", 0.0))
        if ts_report:
            d = compute_delta(baseline["typescript"], ts_report)
            max_regression = min(max_regression, d.overall.get("f1", 0.0))
        if max_regression < -fail_over:
            print(
                f"REGRESSION: worst F1 drop is {-max_regression:.4f}, exceeds {fail_over}",
                file=sys.stderr,
            )
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--ts", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--regression-threshold", type=float, default=0.02)
    parser.add_argument("--fail-over", type=float, default=None)
    args = parser.parse_args()
    return main_impl(
        python_path=args.python,
        ts_path=args.ts,
        baseline_path=args.baseline,
        markdown_path=args.markdown,
        output_path=args.output,
        regression_threshold=args.regression_threshold,
        fail_over=args.fail_over,
    )


if __name__ == "__main__":
    raise SystemExit(main())
