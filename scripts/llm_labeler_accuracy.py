#!/usr/bin/env python
"""Is an LLM good enough to LABEL borderline pairs for threshold calibration?

Context. FS accuracy work stalled on threshold selection: two unsupervised proxies
were built and refuted (score-histogram anti-mode; lambda-driven quantile). A small
labeled seed would fix it -- tens to low hundreds of labels is enough to locate a
1-D cut -- but goldenmatch's thesis is ZERO-CONFIG, so the labels must not come from
the user. The proposal is to let the LLM produce them (the auto-config
planning-effort spec stages this as "Phase 4: LLM-judge labeling", not yet built).

Before building any calibration loop, measure the LABELER. We have ground truth on
these datasets, so this is cheap and decisive.

THE METRIC THAT DECIDES IT is not raw accuracy -- it is ERROR CORRELATION. Human
labels help partly because their mistakes are uncorrelated with the model's. If the
LLM fails the same way FS fails (both over-merging namesakes: "John Smith b.1850"
vs a different John Smith b.1850), the labels RATIFY the bias instead of correcting
it, and calibration confidently finds the wrong cut. A labeler that is 85% accurate
with uncorrelated errors is more useful than one that is 90% accurate and correlated.

Diagnostic only: never fails the job, prints a verdict table to the step summary.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The cut FS actually uses on the linear path, and the band around it where the
# decision is genuinely uncertain (and where a calibrator would spend its labels).
_FS_CUT = 0.50
_BAND_LO, _BAND_HI = 0.40, 0.70
# Emit permissively so we see pairs on BOTH sides of the cut; the default 0.50
# threshold would hide every below-cut pair.
_EMIT_FLOOR = 0.30
_SEED = 20260724


def _emit(md: str) -> None:
    print(md)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")


def _score_pairs(df, dataset: str):
    """Run auto-config + dedupe with a permissive emit floor -> scored pairs."""
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    cfg = auto_configure_probabilistic_df(df)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) == "probabilistic":
            mk.link_threshold = _EMIT_FLOOR
    res = goldenmatch.dedupe_df(df, config=cfg)
    return cfg, list(res.scored_pairs)


def _sample(pairs, gt, n_band: int, n_ctrl: int, rng):
    """Stratified sample: the uncertain band + clear-positive/negative controls.

    The band is CLASS-BALANCED against ground truth. Measured on ncvr_synthetic the
    natural band is 116/120 true matches -- at that base rate a degenerate labeler
    answering "match" to everything scores 0.97 and looks excellent. Balancing (and
    reporting the natural base rate alongside) makes the accuracy number mean
    something and gives the over-merge direction real negatives to fail on.

    Returns ``(band, ctrl_hi, ctrl_lo, base_rate)`` where base_rate is the NATURAL
    (unbalanced) true-match fraction of the band.
    """
    def _is_true(p):
        return (min(p[0], p[1]), max(p[0], p[1])) in gt

    band_all = [p for p in pairs if _BAND_LO <= p[2] <= _BAND_HI and p[2] < 0.999]
    rng.shuffle(band_all)
    base_rate = (
        sum(1 for p in band_all if _is_true(p)) / len(band_all) if band_all else 0.0
    )
    pos = [p for p in band_all if _is_true(p)]
    neg = [p for p in band_all if not _is_true(p)]
    half = max(1, n_band // 2)
    band = pos[:half] + neg[:half]
    # Top up from whichever class has surplus if the other ran dry, so a dataset
    # with few band negatives still yields a full-size sample (its imbalance stays
    # visible via base_rate and balanced accuracy).
    if len(band) < n_band:
        rest = (pos[half:] + neg[half:])
        rng.shuffle(rest)
        band += rest[: n_band - len(band)]
    rng.shuffle(band)

    hi = [p for p in pairs if p[2] > 0.90 and p[2] < 0.999]
    lo = [p for p in pairs if p[2] < _BAND_LO]
    rng.shuffle(hi)
    rng.shuffle(lo)
    return band, hi[:n_ctrl], lo[:n_ctrl], base_rate


def _llm_verdicts(sampled, df, budget_usd: float):
    """Send every sampled pair to the LLM; return {(a,b): is_match}.

    Sets candidate_lo/hi to span everything and auto_threshold above 1.0 so NOTHING
    is auto-accepted -- every pair gets a real LLM decision. Approved pairs come
    back with score exactly 1.0; rejected keep their original score (never demoted),
    which is why pairs at >=0.999 are excluded from the sample upstream.
    """
    from goldenmatch.config.schemas import BudgetConfig, LLMScorerConfig
    from goldenmatch.core.llm_scorer import llm_score_pairs

    cfg = LLMScorerConfig(
        enabled=True, auto_threshold=1.01, candidate_lo=0.0, candidate_hi=1.01,
        budget=BudgetConfig(max_cost_usd=budget_usd),
    )
    out, summary = llm_score_pairs(
        sampled, df, config=cfg, return_budget=True,
    )
    verdicts = {(a, b): (s == 1.0) for a, b, s in out}
    return verdicts, summary


def _analyze(label, sampled, verdicts, gt, lines):
    """Accuracy + the decisive error-correlation breakdown."""
    n = tp = tn = fp = fn = 0
    fs_wrong = both_wrong_same_way = llm_saves = 0
    for a, b, score in sampled:
        key = (min(a, b), max(a, b))
        if key not in verdicts and (a, b) not in verdicts:
            continue
        llm = verdicts.get((a, b), verdicts.get(key))
        truth = key in gt
        fs = score >= _FS_CUT
        n += 1
        if llm and truth:
            tp += 1
        elif not llm and not truth:
            tn += 1
        elif llm and not truth:
            fp += 1
        else:
            fn += 1
        if fs != truth:                       # FS got this pair wrong
            fs_wrong += 1
            if llm == fs:                     # LLM repeats FS's mistake
                both_wrong_same_way += 1
            else:                             # LLM would correct FS
                llm_saves += 1
    if n == 0:
        lines.append(f"| {label} | - | - | - | - | - | - | _no pairs_ |")
        return None
    acc = (tp + tn) / n
    # Balanced accuracy = mean of per-class recall. Immune to the base-rate effect
    # that makes a "always match" labeler look strong on a match-heavy band.
    rec_pos = tp / (tp + fn) if (tp + fn) else None
    rec_neg = tn / (tn + fp) if (tn + fp) else None
    if rec_pos is not None and rec_neg is not None:
        bal = (rec_pos + rec_neg) / 2
        bal_s = f"{bal:.3f}"
    else:
        bal = None
        bal_s = "n/a"
    over = fp / n   # says match when it is not -> the over-merge failure direction
    under = fn / n
    corr = (both_wrong_same_way / fs_wrong) if fs_wrong else None
    corr_s = f"{corr:.2f}" if corr is not None else "n/a"
    lines.append(
        f"| {label} | {n} | {acc:.3f} | {bal_s} | {over:.3f} | {under:.3f} | "
        f"{fs_wrong} | {corr_s} |"
    )
    return {"n": n, "acc": acc, "bal": bal, "over": over, "under": under,
            "fs_wrong": fs_wrong, "corr": corr, "saves": llm_saves}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="ncvr_synthetic,historical_50k")
    ap.add_argument("--row-cap", type=int, default=20000)
    ap.add_argument("--n-band", type=int, default=120)
    ap.add_argument("--n-control", type=int, default=15)
    ap.add_argument("--budget-usd", type=float, default=2.0)
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        _emit("## LLM labeler accuracy\n\n_OPENAI_API_KEY not set; skipped._")
        return 0

    from scripts.autoconfig_quality.datasets import REGISTRY
    reg = {d.name: d for d in REGISTRY}
    rng = random.Random(_SEED)

    lines = [
        "## LLM labeler accuracy on BORDERLINE pairs",
        "",
        "`over` = says match when it is not (the over-merge direction FS also fails in).",
        "`corr` = of the pairs **FS gets wrong**, the fraction the **LLM gets wrong the "
        "same way**. This is the number that decides viability: low `corr` means the "
        "LLM's mistakes are uncorrelated with FS's, so its labels carry new information. "
        "High `corr` means the labels would ratify FS's bias and calibration would "
        "confidently pick the wrong cut.",
        "",
        "`bal` = balanced accuracy (mean of per-class recall). The natural band is "
        "heavily match-skewed (measured 116/120 on ncvr_synthetic), so raw accuracy "
        "flatters a labeler that just answers \"match\"; the band below is "
        "class-balanced and `bal` is the number to read.",
        "",
        "| dataset / stratum | n | accuracy | bal | over | under | FS wrong | corr |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    totals = []
    for name in [d for d in args.datasets.split(",") if d]:
        spec = reg.get(name)
        loaded = spec.loader() if spec else None
        if loaded is None:
            lines.append(f"| {name} | - | - | - | - | - | _absent_ |")
            continue
        df, gt = loaded
        cap = args.row_cap
        if cap and df.height > cap:
            df = df.head(cap)
            gt = {(a, b) for a, b in gt if a < cap and b < cap}
        try:
            _cfg, pairs = _score_pairs(df, name)
        except Exception as e:  # never fail the job
            lines.append(f"| {name} | - | - | - | - | - | _scoring failed: {type(e).__name__}_ |")
            continue
        band, hi, lo, base_rate = _sample(pairs, gt, args.n_band, args.n_control, rng)
        if not band:
            lines.append(f"| {name} | 0 | - | - | - | - | - | _no borderline pairs_ |")
            continue
        sampled = band + hi + lo
        try:
            verdicts, summary = _llm_verdicts(sampled, df, args.budget_usd)
        except Exception as e:
            lines.append(f"| {name} | - | - | - | - | - | _LLM failed: {type(e).__name__}: {str(e)[:60]}_ |")
            continue
        r = _analyze(
            f"**{name}** (band, balanced; natural base rate {base_rate:.2f})",
            band, verdicts, gt, lines,
        )
        _analyze(f"{name} (control: clear-positive)", hi, verdicts, gt, lines)
        _analyze(f"{name} (control: clear-negative)", lo, verdicts, gt, lines)
        if r:
            totals.append((name, r))
        if summary:
            lines.append(
                f"| _{name} cost_ | | | | | | ${summary.get('total_cost_usd', 0):.4f} "
                f"({summary.get('total_calls', 0)} calls) |"
            )

    lines.append("")
    lines.append("### Verdict")
    lines.append("")
    if not totals:
        lines.append("_No dataset produced a labeled band; nothing to conclude._")
    for name, r in totals:
        c = r["corr"]
        if r["acc"] >= 0.90 and (c is None or c <= 0.40):
            v = ("**VIABLE** - accurate AND its errors are largely uncorrelated with "
                 "FS's, so the labels add information.")
        elif c is not None and c >= 0.70:
            v = ("**NOT VIABLE** - the LLM repeats FS's mistakes on most pairs FS gets "
                 "wrong. Calibrating on these labels would ratify the existing bias.")
        elif r["acc"] < 0.75:
            v = "**NOT VIABLE** - too inaccurate on exactly the pairs that decide the cut."
        else:
            v = ("**MARGINAL** - worth a second look; check whether the corrections it "
                 "does make are the ones that matter.")
        lines.append(
            f"- `{name}`: accuracy {r['acc']:.3f}, over-merge {r['over']:.3f}, "
            f"error-correlation {f'{c:.2f}' if c is not None else 'n/a'} "
            f"(LLM would correct FS on {r['saves']} of {r['fs_wrong']} FS errors). {v}"
        )
    _emit("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
