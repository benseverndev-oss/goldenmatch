#!/usr/bin/env python3
"""ER-matcher eval harness + ship gate (P5).

Runs a matcher (a callable ``(a, b) -> verdict|None``, e.g.
``LocalMatcherClient.score_pair``) over the labeled JSONL splits from
``gen_pairs.py``, computes pair-F1 + calibration (ECE) + abstain rate per
split/domain, and applies the SHIP GATE: the model ships only if it clears an
absolute F1 floor, beats the hosted-LLM boost baseline, adds over the pure-FS
baseline, and is adequately calibrated (spec/plan 2026-07-26 §10).

The metric + gate functions are pure (dict/number in, verdict out) and unit-tested
without any model -- mirroring scripts/qis_gate.py's ``evaluate_gate`` pattern.
The CLI wires a real ``LocalMatcherClient`` (Path A) for the measured run.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

Matcher = Callable[[dict, dict], "dict | None"]


# --- pure metrics -----------------------------------------------------------
def confusion(pred_match: list[bool], true_match: list[bool]) -> dict[str, int]:
    tp = fp = fn = tn = 0
    for p, t in zip(pred_match, true_match, strict=True):
        if p and t:
            tp += 1
        elif p and not t:
            fp += 1
        elif not p and t:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def prf1(c: dict[str, int]) -> dict[str, float]:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def expected_calibration_error(
    confidences: list[float], correct: list[bool], bins: int = 10
) -> float:
    """ECE: mean |accuracy - confidence| over confidence bins, weighted by bin
    population. 0 = perfectly calibrated. Empty input -> 0.0."""
    n = len(confidences)
    if n == 0:
        return 0.0
    edges = [i / bins for i in range(bins + 1)]
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        idx = [i for i, c in enumerate(confidences)
               if (c > lo or (lo == 0.0 and c == 0.0)) and c <= hi]
        if not idx:
            continue
        acc = sum(correct[i] for i in idx) / len(idx)
        conf = sum(confidences[i] for i in idx) / len(idx)
        ece += (len(idx) / n) * abs(acc - conf)
    return ece


# --- run ---------------------------------------------------------------------
@dataclass
class GroupScore:
    n: int
    abstained: int
    confusion: dict[str, int]
    precision: float
    recall: float
    f1: float
    ece: float


def _score_group(rows: list[dict], matcher: Matcher) -> GroupScore:
    preds: list[bool] = []
    trues: list[bool] = []
    confs: list[float] = []
    correct: list[bool] = []
    abstained = 0
    for r in rows:
        true = r["label"] == "match"
        v = matcher(r["a"], r["b"])
        if v is None:
            abstained += 1
            pred, conf = False, 0.0  # abstain -> conservative no_match for F1
        else:
            pred, conf = bool(v["match"]), float(v["confidence"])
        preds.append(pred)
        trues.append(true)
        confs.append(conf)
        correct.append(pred == true)
    c = confusion(preds, trues)
    m = prf1(c)
    return GroupScore(
        n=len(rows), abstained=abstained, confusion=c,
        precision=m["precision"], recall=m["recall"], f1=m["f1"],
        ece=expected_calibration_error(confs, correct),
    )


def run_eval(splits: dict[str, list[dict]], matcher: Matcher) -> dict[str, Any]:
    """Score each split overall + broken down by domain. Returns a scorecard dict."""
    scorecard: dict[str, Any] = {"splits": {}}
    for split, rows in splits.items():
        overall = _score_group(rows, matcher)
        by_domain: dict[str, Any] = {}
        domains = sorted({r["domain"] for r in rows})
        for d in domains:
            drows = [r for r in rows if r["domain"] == d]
            by_domain[d] = asdict(_score_group(drows, matcher))
        scorecard["splits"][split] = {"overall": asdict(overall), "by_domain": by_domain}
    return scorecard


# --- ship gate (pure) --------------------------------------------------------
@dataclass
class GateResult:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "passed": ok, "detail": detail})
        if not ok:
            self.passed = False


def evaluate_gate(
    scorecard: dict[str, Any],
    *,
    split: str = "test",
    abs_floor: float = 0.80,
    max_ece: float = 0.15,
    hosted_boost_f1: float | None = None,
    fs_baseline_f1: float | None = None,
    min_gain_over_fs: float = 0.0,
    baseline_f1: float | None = None,
    regression_tol: float = 0.02,
) -> GateResult:
    """Decide ship / no-ship from a scorecard. Pure; unit-tested without a model.

    Checks (all must pass): absolute F1 floor, calibration (ECE), beats the hosted
    LLM boost, adds over the pure-FS baseline, and no regression vs a committed
    baseline scorecard (when provided)."""
    res = GateResult(passed=True)
    grp = scorecard.get("splits", {}).get(split, {}).get("overall")
    if not grp:
        res.add("scorecard_present", False, f"no '{split}' split overall score")
        return res
    f1 = grp["f1"]
    ece = grp["ece"]
    res.add("absolute_floor", f1 >= abs_floor, f"F1 {f1:.4f} vs floor {abs_floor}")
    res.add("calibration", ece <= max_ece, f"ECE {ece:.4f} vs max {max_ece}")
    if hosted_boost_f1 is not None:
        res.add("beats_hosted_boost", f1 >= hosted_boost_f1,
                f"F1 {f1:.4f} vs hosted {hosted_boost_f1:.4f}")
    if fs_baseline_f1 is not None:
        res.add("adds_over_fs", f1 >= fs_baseline_f1 + min_gain_over_fs,
                f"F1 {f1:.4f} vs FS {fs_baseline_f1:.4f} + {min_gain_over_fs}")
    if baseline_f1 is not None:
        res.add("no_regression", f1 >= baseline_f1 - regression_tol,
                f"F1 {f1:.4f} vs baseline {baseline_f1:.4f} - tol {regression_tol}")
    return res


# --- zero-shot scorecard (SP3 Task 3) ----------------------------------------
def build_zeroshot_scorecard(per_benchmark: dict[str, dict]) -> dict:
    """Build a per-benchmark zero-shot scorecard: F1 + calibration + a SOTA
    display column, gated ONLY on the F1 floor + calibration (no baseline
    comparisons -- those are for the measured-run gate, not this display).

    ``per_benchmark`` maps benchmark name -> {"f1", "raw_ece", "calibrated_ece",
    "n"}. Pure -- composes ``sota_baselines.sota_for`` and the existing
    ``evaluate_gate`` without modifying either."""
    from sota_baselines import sota_for

    rows: dict[str, Any] = {}
    for name, entry in per_benchmark.items():
        f1 = entry["f1"]
        raw_ece = entry["raw_ece"]
        calibrated_ece = entry["calibrated_ece"]
        n = entry["n"]
        synthetic_scorecard = {
            "splits": {"test": {"overall": {"f1": f1, "ece": calibrated_ece}}}
        }
        gate = evaluate_gate(
            synthetic_scorecard,
            abs_floor=0.65,
            hosted_boost_f1=None,
            fs_baseline_f1=None,
            baseline_f1=None,
        )
        rows[name] = {
            "f1": f1,
            "raw_ece": raw_ece,
            "calibrated_ece": calibrated_ece,
            "n": n,
            "sota": sota_for(name),
            "gate": gate,
        }
    return rows


# --- CLI ---------------------------------------------------------------------
def _load_splits(data_dir: Path) -> dict[str, list[dict]]:
    splits: dict[str, list[dict]] = {}
    for name in ("train", "val", "test"):
        p = data_dir / f"{name}.jsonl"
        if p.exists():
            splits[name] = [json.loads(line) for line in p.read_text().splitlines() if line]
    return splits


def _build_matcher(base_url: str, model: str) -> Matcher:
    # Imported lazily so the pure metric/gate functions have no goldenmatch dep.
    from goldenmatch.core.er_matcher.client import LocalMatcherClient
    client = LocalMatcherClient(base_url=base_url, model=model)
    return client.score_pair


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Eval the ER-matcher + apply the ship gate.")
    ap.add_argument("--data-dir", type=Path, default=Path("data/er_matcher"))
    ap.add_argument("--base-url", default="http://localhost:8080/v1",
                    help="OpenAI-compatible local ER-matcher server (Path A).")
    ap.add_argument("--model", default="goldenmatch-er-matcher")
    ap.add_argument("--out", type=Path, default=Path("data/er_matcher/scorecard.json"))
    ap.add_argument("--abs-floor", type=float, default=0.80)
    ap.add_argument("--hosted-boost-f1", type=float, default=None)
    ap.add_argument("--fs-baseline-f1", type=float, default=None)
    args = ap.parse_args(list(argv) if argv is not None else None)

    splits = _load_splits(args.data_dir)
    if not splits:
        raise SystemExit(f"no split JSONL under {args.data_dir} (run gen_pairs.py first)")
    matcher = _build_matcher(args.base_url, args.model)
    scorecard = run_eval(splits, matcher)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scorecard, indent=2, sort_keys=True))
    gate = evaluate_gate(scorecard, abs_floor=args.abs_floor,
                         hosted_boost_f1=args.hosted_boost_f1,
                         fs_baseline_f1=args.fs_baseline_f1)
    print(json.dumps({"gate_passed": gate.passed, "checks": gate.checks}, indent=2))
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
