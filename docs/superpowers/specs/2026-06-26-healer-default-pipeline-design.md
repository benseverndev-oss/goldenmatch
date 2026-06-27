# Healer in the Default Pipeline: Surface-on-Default + Opt-in Heal Loop

**Date:** 2026-06-26
**Branch:** `feat/healer-default-pipeline` (worktree `.worktrees/healer-default`), stacked on `feat/suggest-verify-gate-proxy` (PR #1272) → `feat/suggest-gym` (#1271) → `feat/config-suggestion-kernel` (#1267).
**Status:** Design approved (brainstorm); spec under review.

## Problem

The **healer** — `review_config`, the config-suggestion loop — exists and is now trustworthy (the verify-gate default is the precision-sensitive `cohesion` proxy; suggester-gym live recovery 0.151 → 0.543, zero net-negatives on real pairs). But it is **opt-in and invisible**: reachable only via `from goldenmatch.core.suggest import review_config`, never surfaced from the default `dedupe_df` path, and absent from every other surface (CLI/MCP/A2A/TUI/web/REST). The [healing-loop thesis](../foundation/project-definition.md) — *zero-config → returned config → healer suggests tweaks → apply → improve → repeat* — is therefore real in the library but undiscoverable in the product.

Three honest blockers kept it opt-in: **(1) cost** — the self-verify gate simulates each candidate by re-running the pipeline; **(2) it is usually a no-op** — zero-config is near-ceiling on most data, so the healer correctly finds nothing on the common case; **(3) posture** — silently mutating a user's results cuts against "advanced, never black-box."

## Goal

Make the healer **discoverable from the default pipeline and reachable on every surface**, without making the common case pay and without silently changing results — turning the healing loop from a library capability into the product's default workflow.

This spec covers three of the four steps in the agreed progression:
1. a **cheap headroom trigger** off the controller's already-computed signals;
2. **surface-on-default** — a triggered `dedupe_df` attaches cheap raw candidate suggestions;
3. an **opt-in heal loop** — `dedupe_df(df, heal=True)`.

Default **auto-apply** (flipping `heal` on by default) is explicitly out of scope — a named follow-on, gated on broad-corpus evidence.

## Architecture

**Approach A — one shared core helper; every surface delegates.** All trigger / cost / graceful-degrade logic lives in one module, `goldenmatch/core/suggest/surface.py`. The default pipeline and all seven surfaces call into it; no surface re-implements the trigger or the loop. This mirrors the repo's established single-serializer / single-core-function pattern (`web/controller_telemetry.serialize_telemetry`).

Rejected alternatives: per-surface wiring (duplicates trigger+cost logic across seven surfaces → drift); a formal pipeline "suggest" stage (the suggestion is a post-hoc read of the result, not a frames-in/frames-out transform; it would muddy the stage model and the distributed path, and the heal *loop* lives outside a single pipeline run anyway).

### The two-stage cost model

A **free** controller signal gates whether the **cheap** kernel call happens; the **expensive** verified path is opt-in only.

- **Free:** `headroom_signal(result)` reads `result.postflight_report.controller_history` — committed health (RED/YELLOW) or a score-histogram dip on the committed `ComplexityProfile`. No kernel call, no re-run.
- **Cheap (default, when triggered):** an **artifacts-in** suggestion call that reuses the `scored_pairs` + `clusters` the just-finished `dedupe_df` already produced (on the `DedupeResult`) and does only a cheap column-signals profiling pass over `df` — then ONE native kernel `suggest()` call. **No pipeline re-run.** Produces *unverified candidate* suggestions.
- **Expensive (opt-in only):** the verify gate simulates each candidate (apply + re-run the pipeline, up to 8) and keeps only non-worsening ones (the no-net-negative guarantee). Reached via `suggest=True` / `heal=True`.

> **Critical correction (spec-review, 2026-06-26).** The existing `review_config(df, config, verify=...)` (`core/suggest/adapter.py`) **always** calls `engine._run_pipeline(df, config)` to (re)produce the artifacts it feeds the kernel — the `verify` flag only gates the *per-candidate* re-run loop. So calling `review_config` on the default path would run the full dedupe pipeline a SECOND time, roughly doubling wall on every triggered run — which would break the whole "the common case doesn't pay" premise. The cheap default path therefore must NOT call `review_config`; it must use a new **artifacts-in entry point** (below) that consumes the result's already-computed `scored_pairs`/`clusters`. `review_config` (which re-runs) is used only on the opt-in verified/heal paths, where the simulation cost is accepted by request.

## Components

All new code in `goldenmatch/core/suggest/surface.py` (the orchestration) plus thin per-surface call-sites.

### `surface.py`
- **`headroom_signal(result) -> HeadroomReason | None`** — pure, free. Returns a small reason object (e.g. `health=RED` / `dip`) when the committed run shows RED/YELLOW health **or** a score-histogram dip; `None` otherwise. Reads `result.postflight_report.controller_history`; it must re-derive the committed entry via `history.pick_committed(...)` (there is no stored "committed" pointer) and read `committed.profile.health()` plus the dip at `committed.profile.scoring.dip_statistic` / `bimodality_or_dip_score`. No native, no kernel. **Returns `None` when `controller_history` is `None`** — i.e. on the explicit-config path (`dedupe_df(df, config=...)`), where the controller never ran (see Posture). The default surface therefore targets the zero-config workflow.
- **`suggest_from_result(result, df, *, verify=False) -> list[Suggestion]`** — the **artifacts-in** entry point this spec adds (the cheap path's workhorse). Builds the kernel input from the result's already-computed `result.scored_pairs` + `result.clusters` + a column-signals profiling pass over `df` (reuse the existing `core/indicators.py` / autoconfig profiling helpers — a cheap O(N)-per-column pass, NOT a pipeline run), then calls the native `suggest()` kernel directly. When `verify=True`, runs the per-candidate simulation loop (which DOES re-run the pipeline) on top. Catches `SuggestionsNativeRequired` → `[]`. This is what lets the default path skip the redundant `_run_pipeline` that `review_config` would do.
- **`maybe_suggest(result, df, *, verify=False) -> list[Suggestion]`** — the default-path gate. Returns `[]` immediately unless `headroom_signal` fired and the kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE` is not `0`. When fired, delegates to `suggest_from_result(result, df, verify=verify)`. Graceful no-native → `[]`.
- **`heal(df, config, *, step_cap=5) -> HealOutcome`** — the bounded loop: re-run `dedupe_df(df, config=...)` → `suggest_from_result(result, df, verify=True)` → `apply_suggestion(top)` → repeat until no suggestions or `step_cap` (guarding against re-emitting the same patch id, like the existing convergence loops). Returns `(healed_config, applied_trail, healed_result)`. Reuses `suggest_from_result` / `apply_suggestion` — no new rule logic. (The per-step re-run is inherent to applying a config and is the accepted opt-in cost.)
- **`serialize_suggestions(suggestions) -> list[dict]`** — the single wire shape every non-Python surface emits: `{id, kind, target, rationale, verified, patch}`. One serializer, no per-surface drift.

### `DedupeResult` (`goldenmatch/_api.py`)
Two advisory fields, following the existing `lint_findings` / `native` / `throughput_posture` pattern (additive, default-empty):
- `suggestions: list` — raw candidates (default path) or verified (opt-in). Each carries a `verified: bool`.
- `heal_trail: list | None` — the ordered applied suggestions when `heal=True`; `None` otherwise.

`dedupe_df` / `match_df` gain `suggest: bool = False` and `heal: bool = False`.

## Data flow

### Default `dedupe_df(df)` — cheap, surface-only
```
run pipeline → DedupeResult (.config, .postflight_report)
maybe_suggest(result, df, verify=False):
    headroom_signal(result)?            # FREE
       GREEN & no dip  -> []            # common case: kernel never called, byte-identical no-op
       RED/YELLOW/dip  -> suggest_from_result(result, df, verify=False)   # reuse result artifacts; NO pipeline re-run
    native absent      -> []            # graceful, silent
result.suggestions = candidates (verified=False)   # empty when not triggered/no-native
CLI prints a one-line hint when result.suggestions is non-empty
```
(No separate `suggestions_available` field — surfaces key off `bool(result.suggestions)`.)
Default-on; suppress with `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`. Surfaced candidates are explicitly **unverified**; the no-net-negative guarantee is enforced at apply/heal time.

### `dedupe_df(df, suggest=True)` — verified, not applied
Runs `suggest_from_result(result, df, verify=True)`. Attaches verified `result.suggestions` (`verified=True`); applies nothing. As an explicit request it runs regardless of the trigger; when controller signals are present a no-headroom case still short-circuits for free (see Posture → "Explicit-config path & the trigger gate").

### `dedupe_df(df, heal=True)` — verified loop, applied
Runs `heal()`. Returns the healed `DedupeResult` (golden/clusters reflect the improved config; `result.config` is the improved config) plus `result.heal_trail` (auditable). `heal=True` implies verified; `heal` wins over `suggest` if both set. As an explicit request it runs regardless of the trigger; on a near-ceiling input it's a cheap no-op — the loop exits immediately when `suggest_from_result` returns nothing.

## Cross-surface wiring

Every surface reads `result.suggestions` / calls `dedupe_df(suggest=/heal=)` and renders via `serialize_suggestions`. No surface re-implements the trigger or loop.

| Surface | Exposure |
|---|---|
| **Python** | `result.suggestions`, `result.heal_trail`, `suggest=`/`heal=` (the core; all else delegates). |
| **CLI** | default `goldenmatch dedupe` prints the one-line hint when candidates surface; `--suggest` (verified table) and `--heal` (apply loop + print trail). |
| **MCP** | repoint the legacy shallow `suggest_config` tool at the real healer (`review_config`); add a `heal` tool. Update the server-card tool count + count-assertion tests. |
| **A2A** | a `suggest` / `heal` skill; bump the agent-card skill count + its test assertion. |
| **TUI** | a Suggestions panel over `result.suggestions` + an apply action (reuses the existing correction/apply write path). |
| **Web** | a suggestions section on the run view + an apply endpoint (mirrors the review-queue UI pattern). |
| **REST** | `GET` suggestions on a run + `POST /heal` (or `/suggest`), auth-gated like the rest. |

**TS port is out** — the suggest kernel has no WASM/TS binding yet (honest constraint; named follow-on).

**Count-assertion sites to update (so CI stays green):** repointing/adding the MCP tool touches the server-card count in `mcp/server.py` (~line 1002) AND the `len(TOOLS) == N` assertion in `tests/test_mcp_new_tools.py`; the A2A skill touches `_SKILLS` in `a2a/server.py` AND `test_agent_card_has_<N>_skills` in `tests/test_a2a.py`. The plan must bump all of these.

## Posture & graceful-degrade

- **Additive / advisory**, like `lint_findings` (warn-default): a triggered run gains a populated field + a CLI hint; nothing is applied, nothing blocks. Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`.
- **No native → invisible + safe:** `maybe_suggest` returns `[]`, no error, no hint. Fully real for `[native]` users; **bundling the kernel into the base wheel is a named follow-on**, not this spec (it would turn the base into a platform wheel — large blast radius on the pure-Python / edge-safe story).
- **Cost guarantee:** on a GREEN/no-dip run the kernel is never called (free trigger short-circuits). A triggered default run pays one column-signals profiling pass + one native `suggest()` call over the **reused** artifacts — NOT a second pipeline run (that is the whole point of `suggest_from_result`). Verified/heal simulation cost is opt-in only.
- **Explicit-config path & the trigger gate.** The free trigger is an optimization that skips the kernel call when there's no headroom. On the **default** (no-kwarg) path it gates the auto-surface: `controller_history` is populated only on the zero-config path, so `dedupe_df(df, config=...)` (explicit config, no controller) surfaces nothing by default — matching the thesis that the auto-loop is the zero-config workflow. On an **explicit** `suggest=True` / `heal=True` the user has asked, so it runs regardless: when controller signals ARE present it still short-circuits a no-headroom case for free (the approved "cheap no-op on near-ceiling"); when they are absent (explicit-config), it builds from the result's artifacts and runs.

## Testing

- **Unit (fast, no native):** `headroom_signal` (RED/YELLOW/dip → fire; GREEN+no-dip → none) over synthetic postflight; `maybe_suggest` gating (trigger-off → kernel **not** called, asserted; native-absent → `[]`; kill-switch → `[]`); `serialize_suggestions` shape; `heal()` (monkeypatched `review_config`) applies in order and terminates on the cycle guard / empty; **no-op parity** — a trigger-off default run is byte-identical to today (`suggestions == []`, no other field change).
- **Cross-surface:** match each surface's existing test pattern — MCP tool-count assertion, A2A skill-count assertion, CLI `--suggest`/`--heal`, REST endpoint, TUI panel, web router.
- **End-to-end (CI, native):** on `ncvr_synthetic`, a triggered default run surfaces candidates; `heal=True` improves F1 with the no-net-negative guarantee (reuses the suggester-gym machinery).

## Done criteria

- Default `dedupe_df` attaches raw candidates **only** when the free trigger fires **and** native is present; byte-identical no-op otherwise; kill-switch works.
- `suggest=True` (verified, not applied) and `heal=True` (verified loop, applied, `heal_trail`) work and run regardless of the trigger (only the no-kwarg default surface is trigger-gated); when controller signals are present a no-headroom case short-circuits for free.
- All seven surfaces expose the healer via the one core helper + serializer; surface count-assertions updated.
- Graceful no-native everywhere; the cost short-circuit (kernel not called on GREEN/no-dip) is proven by test.

## Out of scope (named follow-ons)

- Default **auto-apply** — `heal` stays opt-in; flipping it default-on is gated on broad-corpus net-positive evidence.
- **Bundling** the suggest kernel into the base wheel (the "reach every pip user" packaging change).
- The **TS / WASM** port of the suggest kernel.
- New suggestion **rules** (blocking-pass, field-weight) — rule coverage, a separate lever.
