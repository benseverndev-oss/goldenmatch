# SP-C Beta-Frontier Report — Design

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Follows:** SP-C suggester (#1384) + precision-aware F-beta `_score` (#1388). The auto-beta follow-on, resolved to the *report-the-frontier* option (make the precision/recall tradeoff visible; the user picks beta).

## Problem

The F-beta `_score` makes the accept metric precision-tunable, but the caller has no guidance on *which* beta to pick — the friction the auto-beta follow-on was meant to remove. Auto-*setting* beta on the LLM's homograph perception was rejected (it couples the metric to a fallible perception and hardcodes a corpus-dependent beta — the "assume, don't measure" anti-pattern). Instead: **report the accept decision across a small beta sweep so the caller sees the frontier and decides.**

## Key property (why it's free)

`beta` only changes the accept *comparison* of two **already-computed** scorecards. `suggest_substrate_config` already builds baseline + proposed once each and holds `base_sc` + `prop_sc` (each with `relational.precision`/`recall`/`f1`). So the frontier is a **pure recompute** — zero extra builds, zero Modal cost.

## Non-goals

- NOT auto-setting beta (rejected).
- NOT changing the run's actual decision: `SuggestResult.accepted`/`.config` stay governed by the *active* beta (`GOLDENGRAPH_SUBSTRATE_SCORE_BETA`, default 1.0). The frontier is purely informational.
- NOT touching `_score`, `for_profile`, `build_and_score_real`, or the self-verify guardrail.
- No frontier on the no-gold MCP surface (`suggest_substrate_config_unverified`) — it has no scorecards to compare.

## Design

### 1. `_accept_frontier(base_sc, prop_sc, betas=(1.0, 0.5, 0.25)) -> dict[float, bool]`
Pure helper in `substrate_suggest.py`:
```python
def _accept_frontier(base_sc, prop_sc, betas=(1.0, 0.5, 0.25)):
    """Accept decision (proposed beats baseline) at each beta, recomputed from the two scorecards
    already in hand. beta<1 favors precision -> shows where a precision-improving-but-F1-losing config
    flips to accepted. Pure; no rebuild."""
    return {b: _score(prop_sc, beta=b) > _score(base_sc, beta=b) for b in betas}
```

### 2. `SuggestResult` gains `accept_frontier: dict[float, bool]`
Added as a field (frozen dataclass). `suggest_substrate_config` computes it from `base_sc`/`prop_sc` right after those are built:
```python
    frontier = _accept_frontier(base_sc, prop_sc)
    ...
    return SuggestResult(winner, flags, accepted, base_sc, prop_sc, accept_frontier=frontier)
```
`accepted` (the active-beta decision) and `config` (the winner at the active beta) are unchanged. On the homograph smoke's scorecards (baseline P=0.815/R=0.672, proposed P=0.932/R=0.545) the frontier is `{1.0: False, 0.5: True, 0.25: True}` — i.e. F1 rejects, beta≤0.5 accepts.

### 3. Runner prints the frontier + the P/R deltas
`run_substrate_suggest.py` adds a line: the `accept_frontier` dict + baseline/proposed `relational.precision`/`recall`, so a run reads (in effect): *"F1 (beta=1.0) rejects this; at beta≤0.5 it wins on precision 0.815→0.932. Set `GOLDENGRAPH_SUBSTRATE_SCORE_BETA` accordingly."* The markdown report gets the same.

## Testing (TDD, box-safe, no Modal)

`tests/test_substrate_suggest.py`:
- `accept_frontier_flips_at_beta` — with the smoke scorecards (`_rel(0.8153, 0.672, 0.7368)` baseline, `_rel(0.9323, 0.545, 0.6885)` proposed via a small local scorecard helper), `_accept_frontier(base, prop)` returns `{1.0: False, 0.5: True, 0.25: True}`. The measured flip, unit-pinned.
- `accept_frontier_all_true_when_proposed_dominates` — proposed better on BOTH P and R → every beta True.
- `accept_frontier_all_false_when_baseline_dominates` — baseline better on both → every beta False.
- `suggest_result_carries_frontier` — `suggest_substrate_config` (fake chat + fake build) returns a `SuggestResult` whose `accept_frontier` matches `_accept_frontier(base_sc, prop_sc)`, and whose `accepted`/`config` are UNCHANGED from before (the active-beta decision — regression guard that the frontier is purely additive).

Existing 13 SP-C tests + the F-beta tests stay green (additive change).

## Design choices flagged for review

- **Default betas `(1.0, 0.5, 0.25)`** — includes the F1 default (1.0) so the frontier always shows the status-quo decision, plus two precision-favoring points that bracket the homograph flip (0.5 marginal, 0.25 clear). Env-overridable is YAGNI for now (the caller can call `_accept_frontier` with custom betas if needed).
- **`accepted`/`config` unchanged** — the frontier informs, it does not decide. The run still returns the config for the *active* beta, so nothing about the shipped accept behavior changes.
- **No new Modal run** — the beta=1.0 (`accepted=False`) and beta=0.5 (`accepted=True`) SP-C runs already *are* the frontier `{1.0: False, 0.5: True}`, measured on real data; the box tests pin the recompute to those P/R numbers.

## Follow-ons

- Surface the frontier in the no-gold MCP path via an *unsupervised* precision proxy (needs the deferred no-gold-verify work) — not now.
