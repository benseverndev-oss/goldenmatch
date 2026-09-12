"""Gate link-cut routing rows on the per-rule matrix (spec 2026-09-11-fs-cut-rule-routing-design, P4).

A routing table routes each dataset's probabilistic matchkeys from the ``cut_diagnostics`` the
matrix recorded on its baseline arm. The routed rule's arm must score at least
``default - 0.01`` on the dataset's gate metric (F1, or labelled-pairs F1 for Magellan) on
EVERY design dataset and EVERY held-out dataset. A dataset no row fires on keeps the default
and passes.

``--table shipped`` also requires each dataset's routed arm (the router run live) to have
picked what the table picks from the recorded diagnostics, and to reproduce the partition of
the arm pinning that rule.

Design verdicts are written in full. The held-out verdict is PASS/FAIL plus how many held-out
datasets the table fired on -- no dataset names, rules or scores -- so it can be read without
the held-out results reaching whoever writes rows.

Usage:
  python -m scripts.autoconfig_quality.cut_gate --design cut-rule-matrix.json \
    --holdout cut-rule-matrix-holdout.json --table candidates --out cut-rule-gate.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from goldenmatch.core import fs_cut_rules
from goldenmatch.core.fs_cut_rules import CutDiagnostics, CutRow, choose_cut_rule

from scripts.autoconfig_quality.corpus import ci_datasets
from scripts.autoconfig_quality.rule_sweep import BASELINE_ARM, ROUTED_ARM, TOLERANCE, metric_value


@dataclass(frozen=True)
class Verdict:
    """One dataset under one routing table."""

    passed: bool
    fired: bool
    rule: str | None
    delta: float | None
    problem: str | None


@dataclass(frozen=True)
class SetResult:
    """One corpus set (design or held-out) under one routing table."""

    passed: bool
    fired: int
    measured: int
    verdicts: dict[str, Verdict]
    missing: list[str]
    problems: list[str]


def _routes(record: dict, rows: Sequence[CutRow]) -> dict[str, str | None]:
    """``{matchkey: rule}`` that ``rows`` pick from the recorded diagnostics (None = default)."""
    diagnostics = record.get("cut_diagnostics") or {}
    routes: dict[str, str | None] = {}
    for mk in record.get("probabilistic_matchkeys") or []:
        d = diagnostics.get(mk)
        # Exactly what the live router reads: no admitted_fraction (P5).
        replay = CutDiagnostics(**{**d, "admitted_fraction": None}) if d else None
        choice = choose_cut_rule(replay, rows)
        routes[mk] = None if choice is None else choice[0]
    return routes


def route_record(record: dict, rows: Sequence[CutRow]) -> tuple[str | None, str | None]:
    """``(rule, problem)``: the one rule ``rows`` route this dataset's probabilistic matchkeys
    to (None = the default), or a problem when they route to different rules."""
    picks = set(_routes(record, rows).values())
    if len(picks) > 1:
        return None, "matchkeys route to different rules; no pinned arm measures that"
    return (picks.pop() if picks else None), None


def judge(record: dict, rows: Sequence[CutRow]) -> Verdict:
    """The verdict for one dataset: the routed rule's arm against the baseline arm."""
    metric = record.get("gate_metric")
    if metric is None:
        return Verdict(False, False, None, None, "record has no gate_metric: re-run the matrix")
    rule, problem = route_record(record, rows)
    if problem is not None:
        return Verdict(False, True, None, None, problem)
    baseline = metric_value(record["arms"][BASELINE_ARM], metric)
    if baseline is None:
        return Verdict(False, rule is not None, rule, None, "baseline arm has no measurement")
    if rule is None:
        return Verdict(True, False, None, 0.0, None)
    arm = record["arms"][rule]
    if arm.get("crashed") or arm.get("voided"):
        status = arm.get("crashed") or "voided"
        return Verdict(False, True, rule, None, f"{rule} arm {status}")
    value = metric_value(arm, metric)
    if value is None:
        return Verdict(False, True, rule, None, f"{rule} arm has no measurement")
    return Verdict(not value < baseline - TOLERANCE, True, rule, round(value - baseline, 4), None)


def routed_disagreement(record: dict, rows: Sequence[CutRow]) -> str | None:
    """For the SHIPPED table: a problem when the matrix's routed arm did not pick what ``rows``
    pick from the recorded diagnostics, or did not reproduce the partition of the arm pinning
    that rule. None when both hold."""
    if "routed_rules" not in record:
        return "record has no routed arm: re-run the matrix"
    routed_arm = (record.get("arms") or {}).get(ROUTED_ARM)
    if routed_arm is None:
        return "record has no routed arm: re-run the matrix"
    if routed_arm.get("crashed") or routed_arm.get("voided"):
        return f"the routed arm {routed_arm.get('crashed') or 'voided'}"
    if record["routed_rules"] != _routes(record, rows):
        return "the routed arm picked differently from the recorded diagnostics"
    if record.get("routed_matches") is False:
        return "the routed arm's partition differs from the arm pinning its rule"
    return None


def gate_set(
    card: dict, expected: Sequence[str], rows: Sequence[CutRow], *, check_routed: bool
) -> SetResult:
    """Every dataset of one merged matrix card under ``rows``. The set passes only when every
    ``expected`` dataset was measured, the run recorded no failures, and every verdict passed."""
    datasets = card.get("datasets") or {}
    missing = [name for name in expected if name not in datasets]
    problems: list[str] = []
    if missing:
        problems.append(f"{len(missing)} of {len(expected)} expected datasets not measured")
    failures = (card.get("meta") or {}).get("failures") or []
    if failures:
        problems.append(f"the matrix run recorded {len(failures)} failures")
    verdicts: dict[str, Verdict] = {}
    for name in sorted(datasets):
        verdict = judge(datasets[name], rows)
        if check_routed and verdict.problem is None:
            disagreement = routed_disagreement(datasets[name], rows)
            if disagreement is not None:
                verdict = Verdict(False, verdict.fired, verdict.rule, verdict.delta, disagreement)
        verdicts[name] = verdict
    passed = not problems and all(v.passed for v in verdicts.values())
    fired = sum(1 for v in verdicts.values() if v.fired)
    return SetResult(passed, fired, len(verdicts), verdicts, missing, problems)


def tables(kind: str) -> list[tuple[str, tuple[CutRow, ...]]]:
    """``(label, rows)`` to gate.

    - ``shipped``: the shipped ``fs_cut_rules.ROWS`` as one table.
    - ``candidates``: each ``cut_rows.CANDIDATES`` row appended, on its own, after the shipped
      rows."""
    from scripts.autoconfig_quality.cut_rows import CANDIDATES

    shipped = tuple(fs_cut_rules.ROWS)
    if kind == "shipped":
        names = ", ".join(row.name for row in shipped) or "no rows"
        return [(f"shipped table ({names})", shipped)]
    return [(f"candidate {row.name} -> {row.rule}", (*shipped, row)) for row in CANDIDATES]


def _word(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def render(label: str, design: SetResult, holdout: SetResult) -> str:
    """Markdown for one table: full design detail, then the held-out verdict with counts only."""
    lines = [
        f"### {label}",
        "",
        f"**Design: {_word(design.passed)}** (fired on {design.fired} of {design.measured})",
        "",
    ]
    lines += [f"- {problem}" for problem in design.problems]
    lines += [f"- missing: {name}" for name in design.missing]
    shown = [
        (name, v) for name, v in design.verdicts.items() if v.fired or v.problem or not v.passed
    ]
    if shown:
        lines += [
            "",
            "| dataset | routed rule | Δ vs default | verdict | problem |",
            "|---|---|---|---|---|",
        ]
        for name, v in shown:
            delta = "" if v.delta is None else f"{v.delta:+.4f}"
            lines.append(
                f"| {name} | {v.rule or 'default'} | {delta} | {_word(v.passed)} | {v.problem or ''} |"
            )
    lines += [
        "",
        f"**Held-out: {_word(holdout.passed)}** (fired on {holdout.fired} of {holdout.measured})",
        "",
    ]
    lines += [f"- {problem}" for problem in holdout.problems]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate link-cut routing rows on the per-rule matrix.")
    ap.add_argument("--design", required=True, type=Path, help="merged design matrix JSON")
    ap.add_argument("--holdout", required=True, type=Path, help="merged held-out matrix JSON")
    ap.add_argument("--table", required=True, choices=["shipped", "candidates"])
    ap.add_argument("--out", required=True, type=Path, help="markdown verdicts to write")
    ap.add_argument("--report-only", action="store_true", help="exit 0 even when a table fails")
    args = ap.parse_args(argv)

    design_card = json.loads(args.design.read_text(encoding="utf-8"))
    holdout_card = json.loads(args.holdout.read_text(encoding="utf-8"))
    if design_card["meta"].get("git_sha") != holdout_card["meta"].get("git_sha"):
        print(
            "cut-gate: the design and held-out matrices come from different commits",
            file=sys.stderr,
        )
        return 2

    entries = tables(args.table)
    out = [
        f"## Link-cut routing gate ({args.table})",
        "",
        f"A routed rule must score at least default - {TOLERANCE} on every design and every "
        "held-out dataset (labelled-pairs F1 on Magellan). Held-out verdicts show counts only.",
        "",
    ]
    all_passed = True
    for label, rows in entries:
        check_routed = args.table == "shipped"
        design = gate_set(design_card, ci_datasets("design"), rows, check_routed=check_routed)
        holdout = gate_set(holdout_card, ci_datasets("holdout"), rows, check_routed=check_routed)
        all_passed = all_passed and design.passed and holdout.passed
        out.append(render(label, design, holdout))
        print(
            f"[cut-gate] {label}: design {_word(design.passed)}, held-out {_word(holdout.passed)}",
            file=sys.stderr,
        )
    if not entries:
        out.append("No candidate rows.\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out), encoding="utf-8")
    return 0 if all_passed or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
