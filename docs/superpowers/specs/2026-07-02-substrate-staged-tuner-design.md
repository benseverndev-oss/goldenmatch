# Substrate Staged Tuner — Design (SP-B2)

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Sub-project:** SP-B2 of the substrate-builder config-surface program (SP-A metric split #1371 merged; SP-B1 config surface #1373 armed; SP-C MCP/LLM loop follows this).

## Scope decisions (flagged for review)

1. **A working deterministic optimizer, not just a reporter.** SP-B2 ships a staged loop with a *deterministic escalation policy* — the honest baseline SP-C's LLM proposer must later beat. No LLM in SP-B2.
2. **Slice-gated loop.** Iterate on a cheap gold *slice* (build + score), and only promote a config that passes the slice gate to the expensive full build. This is the "don't pay a full build per candidate" idea. The unsupervised cheap-proxy *sample-extract* stage (parse-fail/density on unlabeled text) is **deferred** — it's the no-gold/production follow-on and needs proxy-validation first.
3. **Pure harness + injected `build_and_score`.** The control flow (staging, gates, ejection routing, escalation) is pure and box-TDD'd against a *fake* scorer. A thin real adapter wires the actual `ingest_corpus` + `substrate_scorecard` build; its end-to-end Modal run is a follow-up smoke, not part of the TDD.
4. **Lives in `erkgbench`** (`erkgbench/substrate_tuner.py`) — the bench/tuning layer that already owns `LEVER_AXIS_MAP` + `substrate_scorecard` (SP-A) and imports goldengraph. Consumes `SubstrateConfig`/`for_profile` (SP-B1).

## Problem

SP-B1 gives a config object + a static rule-table pick (`for_profile`). But which config is actually best for a corpus is an empirical question — the rule table is a hypothesis. SP-B2 closes the loop: build a slice under a config, score it with the SP-A three-axis scorecard, and if a gate axis is lackluster, **eject** with a diagnostic and **escalate** the config along that axis, iterating cheaply on the slice before paying for the full build. This is the deterministic core of Ben's staged-ejection optimizer.

## Non-goals

- No LLM / MCP (SP-C).
- No unsupervised (no-gold) gates — gold slice required (bench/dev setting; the production proxy path is a documented follow-on).
- No change to `ingest_corpus` or the scorers — SP-B2 orchestrates existing pieces.
- No new levers — escalation only toggles levers already in `SubstrateConfig`/`LEVER_AXIS_MAP`.

## Architecture

```
run_staged(docs, gold, qid_aliases, *, build_and_score, thresholds, budget, ...)
  1. config = for_profile(profile_corpus(docs), ...)          # SP-B1 initial pick
  2. slice = a fixed gold-bearing subset (docs+gold+aliases)
  3. for round in range(budget):
       sc = build_and_score(config, slice)                    # INJECTED: real=Modal, test=fake
       gate = evaluate_gate(sc, thresholds)
       if gate.passed: break
       tried.add(...); config2 = escalate(config, gate.failing_axis, tried)
       if config2 is None: break                              # escalation exhausted -> eject terminal
       config = config2
  4. full_sc = build_and_score(best_config, full)             # promote winner to full build
  5. return TunerResult(config, slice_scorecard, full_scorecard, trace=[RoundReport...])
```

`build_and_score(config, dataset) -> scorecard_dict` is the single injection seam. The pure harness never imports Modal or runs a real build; it calls this callable and reads the SP-A scorecard shape (`{"presence": {...}|None, "relational": {...}, "connectivity": {...}, "coherence": {...}}`).

## Components (in `erkgbench/substrate_tuner.py`)

### 1. `GateThresholds` (frozen dataclass)
```
presence_min: float = 0.90       # presence.coverage floor (wiki-path; None-presence skips this axis)
relational_f1_min: float = 0.50  # relational.f1 floor
```
Env-overridable defaults. Thresholds are *hypotheses* (like SP-B1's `CHUNK_MIN_SENTENCES`) — documented, tuned by running the harness.

### 2. `GateResult` + `evaluate_gate(scorecard, thresholds) -> GateResult`
`GateResult{passed: bool, failing_axis: str | None, scorecard: dict}`. Axis-precedence when both fail: **presence before relational** (you can't fix relational quality on entities that aren't in the KB — fix presence first). If `presence is None` (engineered/no-alias path), the presence check is skipped and only relational is gated.

### 3. `escalate(config, failing_axis, tried, *, relation_vocab=(), allow_refuted=False) -> SubstrateConfig | None`
Deterministic policy. Walks `LEVER_AXIS_MAP[failing_axis]` in order; for the first lever that is (a) not refuted [unless `allow_refuted`], (b) not already tried, and (c) has an applicable transform that *changes* the config, returns a new config with that transform applied and records it in `tried`. Returns `None` when the axis is exhausted (→ terminal eject).

Per-lever transforms (the escalation "moves"):
| lever | transform | notes |
|---|---|---|
| `chunk_extract` | `False→True` (6,2) | presence lever, the measured win |
| `extractor` | `"api"→"gliner"` | presence lever, a swap |
| `xdoc_key` | `""→"name_ci"→"name_ci_type"` (+`entity_type_canon=True` on the last) | relational lever |
| `entity_type_canon` | `False→True` | relational (usually rides xdoc_key) |
| `schema_canon` | `False→True` **only if `relation_vocab` non-empty** | relational; needs a vocab |
| `relation_reprompt`, `rebel_fuse`, `extract_recall` | skipped unless `allow_refuted` | REFUTED levers |
| `relation_vocab` | not auto-toggled (needs an external vocab) | skipped in auto-escalation |

Refuted levers are gated behind `allow_refuted=False` (default) so the deterministic policy never re-arms a dead path; SP-C can flip it to let the LLM *propose* a measurement-gated re-test.

### 4. `RoundReport` + `TunerResult`
- `RoundReport{round: int, config: SubstrateConfig, scorecard: dict, gate: GateResult, escalated_to: str | None}` — the ejection diagnostic (`escalated_to` = the lever toggled, or None if passed/exhausted). This is the structured hand-off SP-C's LLM will consume in place of `escalate`.
- `TunerResult{config, slice_scorecard, full_scorecard, trace: list[RoundReport], stopped_reason: str}` (`"passed" | "budget" | "exhausted"`).

### 5. Real adapter `build_and_score_real(config, dataset)` (thin, erkgbench)
Under `config.apply()`: run `ingest_corpus` over the dataset's docs → graph → `substrate_scorecard(graph, gold, qid_aliases)`. Reuses `run_substrate_eval._wiki_build`-style plumbing. NOT box-testable (needs native store + LLM) → validated by a Modal smoke, not TDD. The pure harness is validated with a fake.

## Testing (TDD, box-safe — pure harness with a FAKE `build_and_score`)

`erkgbench/tests/test_substrate_tuner.py`. A fake `build_and_score` returns scripted scorecards keyed by config, so control flow is deterministic:
- `gate_passes_when_both_axes_clear` — scorecard above both floors → `passed`, no escalation.
- `gate_routes_presence_before_relational` — both below floor → `failing_axis == "presence"`.
- `gate_skips_presence_when_none` — engineered `presence=None` → only relational gated.
- `escalate_presence_enables_chunking_first` — presence fail on a default config → `chunk_extract` turned on.
- `escalate_relational_bumps_xdoc_key` — relational fail with `xdoc_key=""` → `"name_ci"`; again → `"name_ci_type"` + `entity_type_canon`.
- `escalate_skips_refuted` — no escalation path selects reprompt/rebel/extract_recall (allow_refuted=False).
- `escalate_returns_none_when_exhausted` — all axis levers tried → `None`.
- `run_staged_passes_first_round` — fake returns a passing scorecard immediately → 1 round, `stopped_reason="passed"`, full build invoked once.
- `run_staged_escalates_then_passes` — fake returns fail then pass → 2 rounds, config escalated once, trace records the ejection, then the full build runs on the winner.
- `run_staged_budget_exhausted` — fake always fails → stops at `budget`, `stopped_reason="budget"`, full build still runs on best-so-far.
- `run_staged_terminal_eject` — fail on an axis with no eligible lever → `stopped_reason="exhausted"`.
- `tuner_result_trace_is_serializable` — `trace` RoundReports carry the scorecard + config + escalated_to (the SP-C hand-off shape).

## Design choices flagged for review

- **Gate thresholds + escalation ORDER are hypotheses.** They encode the arc's findings (presence→chunking, relational→name_ci→name_ci_type) but the *right* floors are unknown until the harness runs on real corpora. Shipped as documented, env-overridable defaults; the harness is the instrument that tunes them.
- **Slice choice.** For the wiki corpus the "slice" is a fixed doc subset with its gold; the caller passes `(docs, gold, qid_aliases)` and a `slice_size`. Escalating on a slice that's too small risks over-fitting the tune to noise — documented; `slice_size` defaults to the whole corpus for the small (19-doc) wiki case, i.e. slice==full until a corpus is large enough to warrant a true subset.
- **`build_and_score` is injected**, so the pure harness has ZERO Modal/LLM/native dependency and is fully box-testable; the real adapter is the only Modal-touching piece.
- **Refuted levers gated** behind `allow_refuted` — off for the deterministic baseline, the flag SP-C flips to let an LLM propose a gated re-test.

## Dependency / branch note

- Needs `SubstrateConfig`/`for_profile` (SP-B1 #1373) and `LEVER_AXIS_MAP`/`substrate_scorecard` (SP-A #1371, already on main). This branch (`feat/substrate-tuner`) is off main **before #1373 merged** → `goldengraph.config` is absent here. Implementation: **rebase onto `origin/main` after #1373 merges** before the final PR (same pattern SP-B1 used for SP-A). The pure harness imports `SubstrateConfig`/`for_profile` from `goldengraph.config` and `LEVER_AXIS_MAP` from `erkgbench.substrate_eval`.

## Follow-ons

- **Modal smoke** of `build_and_score_real` on the wiki corpus — confirm the loop actually improves the scorecard end-to-end (the deterministic baseline number SP-C must beat).
- **SP-C:** `suggest_substrate_config` MCP tool that swaps the deterministic `escalate` for a bounded LLM proposer over the `RoundReport` diagnostic; final config must beat this SP-B2 deterministic baseline on the scorecard (`review_config` self-verify).
- **No-gold production gates:** validate unsupervised proxies (fragmentation/density) against the gold axes on the bench, then reuse them as the sample-extract stage for arbitrary corpora.
