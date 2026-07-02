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
run_staged(docs, gold, qid_aliases, *, build_and_score, thresholds, budget, relation_vocab=(), ...)
  1. config = for_profile(profile_corpus(docs), ...)          # SP-B1 initial pick
  2. slice = a fixed gold-bearing subset (docs+gold+aliases)
  # precondition: budget >= 1 (else there is no round to score and best_config is undefined)
  3. tried = set()                                            # (lever, next_value) pairs, mutated ONLY by escalate
     rounds = []
     for r in range(budget):
       sc = build_and_score(config, slice)                    # INJECTED: real=Modal, test=fake
       gate = evaluate_gate(sc, thresholds)
       step = None if gate.passed else escalate(config, gate.failing_axis, tried,
                                                 relation_vocab=relation_vocab)   # (lever, new_config) | None
       escalated_to, config2 = (None, None) if step is None else step
       rounds.append(RoundReport(r, config, sc, gate, escalated_to=escalated_to))
       if gate.passed: stopped = "passed"; break
       if step is None: stopped = "exhausted"; break          # no eligible lever left on the failing axis
       config = config2
     else:
       stopped = "budget"
  4. best_config = argmax over rounds by _score(round.scorecard)   # escalation is NOT monotonic -> pick best
     full_sc = build_and_score(best_config, full)             # ALWAYS promote best-so-far to the full build
  5. return TunerResult(best_config, slice_scorecard=best_round.scorecard, full_scorecard=full_sc,
                        trace=rounds, stopped_reason=stopped)
```

`build_and_score(config, dataset) -> scorecard_dict` is the single injection seam. The pure harness never imports Modal or runs a real build; it calls this callable and reads the SP-A scorecard shape (`{"presence": {...}|None, "relational": {...}, "connectivity": {...}, "coherence": {...}}`).

`_score(scorecard) -> float` is the round-ranking scalar used to pick `best_config`: `relational.f1 + presence.coverage` (or just `relational.f1` when `presence is None`). Higher wins; ties → earliest round. This is what makes "promote the best-so-far" well-defined even though escalation can regress (e.g. an `extractor` swap).

## Components (in `erkgbench/substrate_tuner.py`)

### 1. `GateThresholds` (frozen dataclass)
```
presence_min: float = 0.90       # presence.coverage floor (wiki-path; None-presence skips this axis)
relational_f1_min: float = 0.50  # relational.f1 floor
```
Env-overridable defaults. Thresholds are *hypotheses* (like SP-B1's `CHUNK_MIN_SENTENCES`) — documented, tuned by running the harness.

### 2. `GateResult` + `evaluate_gate(scorecard, thresholds) -> GateResult`
`GateResult{passed: bool, failing_axis: str | None, scorecard: dict}`. Axis-precedence when both fail: **presence before relational** (you can't fix relational quality on entities that aren't in the KB — fix presence first). If `presence is None` (engineered/no-alias path), the presence check is skipped and only relational is gated.

**Connectivity is intentionally ungated in v1.** `GateThresholds` has only `presence_min` + `relational_f1_min`, so `evaluate_gate` only ever routes to `{presence, relational}`. This is deliberate: `LEVER_AXIS_MAP["connectivity"]` is `[relation_reprompt, rebel_fuse, relation_vocab]` — all refuted or not-auto-toggled — so a connectivity failure would have no eligible escalation and immediately exhaust. Connectivity is reported in the scorecard (visible in the trace) but is not a gate axis; that is why the escalate transform table has no connectivity-only rows. Adding a connectivity gate waits until there's a non-refuted lever that moves it.

### 3. `escalate(config, failing_axis, tried, *, relation_vocab=(), allow_refuted=False) -> tuple[str, SubstrateConfig] | None`
Deterministic policy, built on **next-state transitions** (NOT a "tried lever name" set — that breaks the `xdoc_key` ladder). Each lever has an `_advance(config) -> (next_value_repr, new_config) | None` that moves it ONE step from its current state (returns `None` when already at the end of its ladder). `escalate` walks `LEVER_AXIS_MAP[failing_axis]` in order and selects the **first** lever that is:
  (a) not refuted [unless `allow_refuted`], and
  (b) `_advance` yields a step (`next != current`), and
  (c) the `(lever, next_value_repr)` pair is not already in `tried`.
It records `(lever, next_value_repr)` in `tried` (escalate is the **sole** mutator of `tried`) and returns **`(lever_name, new_config)`** — the new frozen config via `dataclasses.replace`, PAIRED WITH the advanced lever's name so the caller records `escalated_to` directly instead of diffing (a diff is ambiguous: the `xdoc_key→name_ci_type` and `schema_canon` steps each change two fields). Returns `None` when every axis lever is exhausted → terminal eject (`escalated_to` is then `None`, as it is for a passed round).

**Termination:** every lever's ladder is finite (`chunk_extract`: F→T; `extractor`: api→gliner; `xdoc_key`: ""→name_ci→name_ci_type; `entity_type_canon`: F→T; `schema_canon`: F→T), each `(lever, next)` pair is recorded once and never re-selected, so the total number of escalations across all rounds is bounded by the sum of ladder lengths → the loop always terminates (independent of `budget`).

Per-lever `_advance` transitions (the escalation "moves"):
| lever | advance (current → next) | notes |
|---|---|---|
| `chunk_extract` | `False → True` (with `chunk_sentences=6, chunk_overlap=2`) | presence lever, the measured win |
| `extractor` | `"api" → "gliner"` | presence lever, a swap |
| `xdoc_key` | `"" → "name_ci" → "name_ci_type"` (sets `entity_type_canon=True` on the `→name_ci_type` step) | relational ladder (2 steps) |
| `entity_type_canon` | `False → True` | relational (usually already set by the xdoc_key ladder) |
| `schema_canon` | `False → True` **AND set `relation_vocab=<escalate's relation_vocab param>`** — eligible ONLY if that param is non-empty and `config.schema_canon` is False | relational; enabling canon WITHOUT a vocab is engine-defeated (SP-B1 §2), so the two are set together |
| `relation_reprompt`, `rebel_fuse`, `extract_recall` | skipped unless `allow_refuted` | REFUTED levers |
| `relation_vocab` | not auto-toggled on its own (has no ladder; it rides `schema_canon`) | — |

The `relation_vocab` param carries EXTERNAL knowledge (the caller's known schema, same as SP-B1's `for_profile(relation_vocab=)`); it is read from the param, not `config.relation_vocab`, and written onto the config together with `schema_canon`. Refuted levers are gated behind `allow_refuted=False` (default) so the deterministic policy never re-arms a dead path; SP-C can flip it to let the LLM *propose* a measurement-gated re-test.

### 4. `RoundReport` + `TunerResult`
- `RoundReport{round: int, config: SubstrateConfig, scorecard: dict, gate: GateResult, escalated_to: str | None}` — the ejection diagnostic (`escalated_to` = the lever advanced this round, or None if passed/exhausted). This is the structured hand-off SP-C's LLM will consume in place of `escalate`.
- `TunerResult{config, slice_scorecard, full_scorecard, trace: list[RoundReport], stopped_reason: str}` (`"passed" | "budget" | "exhausted"`). `config` = the **best-so-far** config (argmax of `_score` over `trace`, not necessarily the last one tried), and `full_scorecard` is that best config re-scored on the full corpus. The full build ALWAYS runs on the best-so-far, including on the `budget` and `exhausted` exits (escalate returning `None` means "no further improvement available," so the best already-seen config is the answer).

### 5. Real adapter `build_and_score_real(config, dataset)` (thin, erkgbench)
Under `config.apply()`: run `ingest_corpus` over the dataset's docs → graph → `substrate_scorecard(graph, gold, qid_aliases)`. Reuses `run_substrate_eval._wiki_build`-style plumbing. NOT box-testable (needs native store + LLM) → validated by a Modal smoke, not TDD. The pure harness is validated with a fake.

## Testing (TDD, box-safe — pure harness with a FAKE `build_and_score`)

`erkgbench/tests/test_substrate_tuner.py`. A fake `build_and_score` returns scripted scorecards keyed by config, so control flow is deterministic:
- `gate_passes_when_both_axes_clear` — scorecard above both floors → `passed`, no escalation.
- `gate_routes_presence_before_relational` — both below floor → `failing_axis == "presence"`.
- `gate_skips_presence_when_none` — engineered `presence=None` → only relational gated.
- `escalate_presence_enables_chunking_first` — presence fail on a default config → `chunk_extract` turned on.
- `escalate_relational_ladder_bumps_xdoc_key_twice` — relational fail with `xdoc_key=""` → `"name_ci"` (records `(xdoc_key, name_ci)`); escalate the RESULT again → `"name_ci_type"` + `entity_type_canon` (records `(xdoc_key, name_ci_type)`). Proves the ladder advances across calls (the Critical: `tried` keyed by `(lever, next_value)`, not lever name).
- `escalate_schema_canon_sets_vocab` — relational fail, `relation_vocab=("acquired",)` passed, xdoc_key ladder exhausted → `schema_canon=True` AND `relation_vocab==("acquired",)` on the new config (never one without the other).
- `escalate_schema_canon_skipped_without_vocab` — same but `relation_vocab=()` → `schema_canon` NOT selected (would be engine-defeated).
- `escalate_skips_refuted` — no escalation path selects reprompt/rebel/extract_recall (allow_refuted=False).
- `escalate_returns_none_when_exhausted` — all axis levers advanced → `None`.
- `run_staged_passes_first_round` — fake returns a passing scorecard immediately → 1 round, `stopped_reason="passed"`, full build invoked once.
- `run_staged_escalates_then_passes` — fake returns fail then pass → 2 rounds, config escalated once, trace records the ejection, then the full build runs on the winner.
- `run_staged_budget_exhausted` — fake always fails → stops at `budget`, `stopped_reason="budget"`, full build still runs on best-so-far.
- `run_staged_promotes_argmax_not_last` — fake returns a HIGH score on round 1 then LOWER on later rounds (a regressing escalation) → `TunerResult.config` is round-1's config (argmax), and the full build is invoked with it (not the last-tried config).
- `run_staged_terminal_eject` — fail on an axis with no eligible lever → `stopped_reason="exhausted"`, full build still runs on best-so-far.
- `run_staged_rejects_budget_below_one` — `run_staged(..., budget=0)` raises `ValueError` (no round → `best_config` undefined; guard the precondition rather than degenerate the argmax over an empty trace).
- `escalate_returns_lever_and_config` — a successful escalate returns a `(lever_name, SubstrateConfig)` tuple whose lever_name is the advanced lever (e.g. `"chunk_extract"`), so `RoundReport.escalated_to` is set without diffing.
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
