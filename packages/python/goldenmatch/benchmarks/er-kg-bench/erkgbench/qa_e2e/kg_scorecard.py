"""Slice D: KG-vs-KG capability scorecard. Model each KG framework's documented ER strategy as
a record_key dial and run it through two ER-driven capability metrics (bridge-recall from slice A,
aggregation set-F1 from slice B1). Gates that goldengraph's fuzzy ER beats the exact-match
frameworks (LightRAG/MS-GraphRAG, which coincide on this single-entity-type corpus). Plus an
opt-in real-framework confirmation lane.

NO new dial: the scorecard maps framework labels to EXISTING dials.py keyfns. The deterministic
metrics + gate + render are wheel-free EXCEPT the per-dial graph helpers (they reach
ablation._build_store). The answer->set parser + gate shape are wheel-free.
"""
from __future__ import annotations

from dataclasses import dataclass

#: best -> worst ER (by merge-recall). Labels map to EXISTING dials.py keyfns in run_scorecard.
DIAL_TIERS = ("oracle", "goldengraph", "exact_match", "none")

#: Frozen from the measured grid (run_kg_scorecard). Placeholders -- TIGHTEN after measuring.
MOAT_MARGIN = 0.15   # goldengraph - exact_match must be >= this on EVERY capability
MONO_TOL = 1e-9      # tolerance for the oracle>=goldengraph>=exact_match>=none chain
EPS = 0.02           # exact_match <= none + EPS on bridge-recall (exact-match ~= no-merge)


def parse_entity_set(answer: str, s2c: dict) -> set:
    """Scan the framework's free-text answer for known surfaces; return the set of canonical ids.
    `s2c` is a FIRST-WINS scalar surface->canonical map (matches set_f1's scalar gold members)."""
    low = answer.lower()
    out: set = set()
    for surf, canon in s2c.items():
        if surf.lower() in low:
            out.add(canon)
    return out


@dataclass
class ScorecardResult:
    bridge_recall: dict   # dial -> mean whole-chain bridge-recall
    aggregation_f1: dict  # dial -> mean set-F1


def evaluate_assertions(res: ScorecardResult):
    """[(label, passed, is_hard), ...]. HARD gates; soft only warns.

    The exact_match column models the LightRAG/MS-GraphRAG ER strategy (exact-surface merge) as a
    record_key policy, NOT the full framework runtime; the real-framework confirmation is the
    opt-in lane. Claim: a store built under that ER strategy loses the capability."""
    metrics = {"bridge_recall": res.bridge_recall, "aggregation_f1": res.aggregation_f1}

    # 1. HEADLINE: fuzzy ER beats the exact-match tier on EVERY capability.
    worst_moat = min(m["goldengraph"] - m["exact_match"] for m in metrics.values())
    moat = worst_moat >= MOAT_MARGIN
    # 2. ER-quality monotonicity (merge-recall direction) per metric.
    mono = all(
        m["oracle"] + MONO_TOL >= m["goldengraph"]
        and m["goldengraph"] + MONO_TOL >= m["exact_match"]
        and m["exact_match"] + MONO_TOL >= m["none"]
        for m in metrics.values()
    )
    # 3. exact-match ER ~= no-merge on reachability (the slice-A name_only==none finding).
    exact_inert = res.bridge_recall["exact_match"] <= res.bridge_recall["none"] + EPS

    return [
        (f"goldengraph beats exact-match on every capability (worst moat {worst_moat:.3f} >= {MOAT_MARGIN})", moat, True),
        ("ER-quality monotonic per metric (oracle>=goldengraph>=exact_match>=none)", mono, True),
        (f"exact-match ~= no-merge on bridge-recall (exact <= none + {EPS})", exact_inert, True),
    ]


def gate_exit_code(res: ScorecardResult) -> int:
    hard_failed = any(is_hard and not ok for _l, ok, is_hard in evaluate_assertions(res))
    return 1 if hard_failed else 0


_LABEL = {
    "oracle": "oracle (perfect ER)",
    "goldengraph": "goldengraph (fuzzy)",
    "exact_match": "exact-match (LightRAG / MS-GraphRAG)",
    "none": "none (no merge)",
}


def render_scorecard_md(res: ScorecardResult) -> str:
    lines = [
        "# GoldenGraph KG-vs-KG capability scorecard (ER dial x capability, no LLM)",
        "",
        "Each KG framework's documented ER strategy as a record_key dial, run through two",
        "ER-driven capabilities. Does weak (exact-match) ER cost the frameworks vs goldengraph's",
        "fuzzy ER? The exact-match column models the LightRAG/MS-GraphRAG ER STRATEGY, not the full",
        "framework runtime (the opt-in real lane is the faithfulness check).",
        "",
        "| ER tier | bridge_recall | aggregation_setF1 |",
        "|---|---|---|",
    ]
    for d in DIAL_TIERS:
        lines.append(f"| {_LABEL[d]} | {res.bridge_recall[d]:.3f} | {res.aggregation_f1[d]:.3f} |")
    lines += ["", "## verdicts", "",
              "(assertions are measurement-frozen empirical gates, not structural guarantees)"]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"
