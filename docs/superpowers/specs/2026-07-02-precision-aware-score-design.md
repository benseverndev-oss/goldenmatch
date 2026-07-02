# Precision-Aware Accept Metric (F-beta `_score`) — Design

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Follows:** the substrate config-surface program (SP-A #1371 · SP-B1 #1373 · SP-B2 #1375 · SP-C #1384). The SP-C smoke (`docs/superpowers/reports/2026-07-02-suggester-smoke-verdict.md`) surfaced this as the top lever.

## Problem

`substrate_tuner._score(scorecard) = relational.f1 (+ presence.coverage)` is the shared accept/rank scalar for BOTH SP-B2 `run_staged` (best-so-far `argmax`) and SP-C `suggest_substrate_config` (`_score(prop) > _score(base)`). On the homograph corpus the proposed homograph-safe config **raised precision 0.815 → 0.892** (exactly its job — stop over-merging same-named distinct entities) but **F1 fell 0.737 → 0.672** (recall cost), so the F1-based accept **rejected a genuine precision win**. On a corpus where the objective is precision, F1 is the wrong metric — it charges the recall cost and hides the win.

## Goal / non-goals

- **Goal:** make the accept/rank scalar precision-tunable so a config that improves precision at acceptable recall can win — without regressing the validated F1-default behavior.
- **Non-goals:** NOT changing the default behavior (F1 stays the default). NOT auto-choosing beta from the corpus (that couples the metric to corpus perception — a follow-on). NOT a two-parameter precision-floor gate (the tuner's `argmax` needs a single rankable scalar).

## Design

`_score` becomes an **F-beta** on the relational axis, `beta` from an env var (default 1.0):

```python
def _score(scorecard: dict, *, beta: float | None = None) -> float:
    """Round-ranking scalar: F-beta of relational P/R (+ presence.coverage when present). beta<1 favors
    PRECISION, >1 recall, ==1 is F1 (the default -- byte-identical to the stored relational.f1)."""
    if beta is None:
        beta = float(os.environ.get("GOLDENGRAPH_SUBSTRATE_SCORE_BETA", "1.0") or "1.0")
    rel = scorecard["relational"]
    if beta == 1.0:
        f = rel["f1"]                                  # exact backward-compat, no float drift
    else:
        p, r, b2 = rel["precision"], rel["recall"], beta * beta
        denom = b2 * p + r
        f = (1.0 + b2) * p * r / denom if denom > 0 else 0.0
    s = f
    presence = scorecard.get("presence")
    if presence is not None:
        s += presence["coverage"]
    return s
```

Key properties:
- **Implementation note (review catch):** `substrate_tuner.py` currently imports `os` ONLY function-locally (inside `_reset_llm_state`) — there is no module-level `import os`. The plan MUST add `import os` at the module top, else `_score`'s env read raises `NameError` on any `beta != default` path. (The `beta == 1.0` default path never reads env only if the env var is unset AND no param — actually the env read runs whenever `beta is None`, i.e. every default call, so `import os` is required for the default path too.)
- **No signature change at the call sites.** `beta` is a keyword-only param defaulting to the env read, so `_score(scorecard)` (both existing call sites) is unchanged and picks up the env beta automatically. The optional param exists for tests/callers that want to pin beta explicitly.
- **beta == 1.0 uses the STORED `relational.f1`** (not recomputed) → byte-identical to today; every existing SP-B2/SP-C test and the validated wiki/`name_ci` result are unchanged at the default.
- **Verified from the smoke numbers** (baseline P=0.815/R=0.672 vs proposed P=0.892/R=0.540): beta=0.5 → proposed 0.789 > baseline 0.782 (flips to accept); beta=0.25 → 0.859 > 0.805 (clear). So beta<1 accepts the real precision win.
- **presence.coverage stays additive, un-beta'd** — it's a separate "is it in the KB" axis, not a P/R tradeoff.

## Consumers (automatic, no change)

- SP-B2 `run_staged`: `max(rounds, key=lambda rr: _score(rr.scorecard))` — picks up the env beta.
- SP-C `suggest_substrate_config`: `_score(prop_sc) > _score(base_sc)` — picks up the env beta.
- The SP-C homograph runner (`run_substrate_suggest.py`) sets `GOLDENGRAPH_SUBSTRATE_SCORE_BETA` so the smoke can be run precision-favoring.

## Testing (TDD, box-safe)

`tests/test_substrate_tuner.py` (extend — `_score` lives there):
- `score_beta1_equals_stored_f1` — with a scorecard whose `relational.f1` differs slightly from `2PR/(P+R)` (set f1 to a sentinel), `_score(sc, beta=1.0)` returns the STORED f1 (+presence), proving beta=1 uses the stored value, not a recompute.
- `score_beta_half_favors_precision` — the smoke numbers: `_score(base, beta=0.5) < _score(prop, beta=0.5)` where base=(P.815,R.672,f1.737), prop=(P.892,R.540,f1.672). Asserts the flip.
- `score_beta_reads_env` — set `GOLDENGRAPH_SUBSTRATE_SCORE_BETA=0.25`, `_score(sc)` (no param) computes F_0.25 (not F1). Restore env after (try/finally).
- `score_beta_zero_denom_safe` — a scorecard with P=0,R=0 → `_score` returns 0.0 (+presence), no ZeroDivisionError.
- `score_presence_still_additive` — beta≠1 with presence not None → still adds presence.coverage.
- `score_existing_callers_unchanged` — a fixture used by the existing `run_staged` tests still scores identically at the default (regression guard; the existing tuner tests already cover this implicitly, but assert one explicitly).

Existing SP-B2 (`test_substrate_tuner.py`) and SP-C (`test_substrate_suggest.py`) tests must stay green unchanged (beta defaults to 1.0).

## Verification (Modal, closes the SP-C loop)

Re-run the SP-C homograph smoke with `GOLDENGRAPH_SUBSTRATE_SCORE_BETA=0.5` (via `--opts`). **Expected:** `accepted=True`, proposed's higher F_0.5 beats baseline — i.e. the metric now rewards the precision win (P 0.815→0.892) the F1 gate hid. (The proposed winner is `name_ci_type` + `entity_type_canon` directly from `for_profile(expect_homographs=True)` — SP-C's suggest builds the config from the LLM flags, NOT via the SP-B2 escalate ladder, so `name_ci_type` is reached unconditionally when the LLM flags homographs.) Record in a short verdict addendum.

## Design choices flagged for review

- **Default beta = 1.0 (opt-in precision).** No regression; the homograph/ambiguous case opts in via env. Auto-beta-by-corpus deferred (couples metric to perception).
- **F-beta over precision-floor.** Single rankable scalar for the tuner's argmax; one principled knob for the P/R preference.
- **beta==1.0 short-circuits to the stored f1** so the default is bit-for-bit unchanged (avoids any harmonic-mean float drift vs the scorer's own f1).

## Follow-ons

- **Auto-beta:** when SP-C/the profile perceives homographs/high ambiguity, default beta<1 for that run (measured, not assumed).
- **Report both axes:** surface P and R (not just the scalar) in the tuner/suggester output so a precision win is visible even when the scalar is F1.
