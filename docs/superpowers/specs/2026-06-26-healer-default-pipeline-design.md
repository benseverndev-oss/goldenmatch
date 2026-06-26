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
- **Cheap (default, when triggered):** `review_config(df, config, verify=False)` — one native kernel call over already-collected run artifacts, **no pipeline re-runs**. Produces *unverified candidate* suggestions.
- **Expensive (opt-in only):** `review_config(df, config, verify=True)` — simulates each candidate (apply + re-run, up to 8) and keeps only non-worsening ones (the no-net-negative guarantee). Reached via `suggest=True` / `heal=True`.

## Components

All new code in `goldenmatch/core/suggest/surface.py` (the orchestration) plus thin per-surface call-sites.

### `surface.py`
- **`headroom_signal(result) -> HeadroomReason | None`** — pure, free. Returns a small reason object (e.g. `health=RED` / `dip`) when the committed run shows RED/YELLOW health **or** a score-histogram dip; `None` otherwise. Reads only `result.postflight_report.controller_history` (already computed). No native, no kernel.
- **`maybe_suggest(result, df, *, verify=False) -> list[Suggestion]`** — the default-path entry. Returns `[]` immediately unless `headroom_signal` fired. When fired, calls `review_config(df, result.config, verify=verify)`; catches `SuggestionsNativeRequired` → `[]` (graceful no-native). Honors the kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0` (→ `[]`).
- **`heal(df, config, *, step_cap=5) -> HealOutcome`** — the bounded loop: `review_config(verify=True)` → `apply_suggestion` → re-run `dedupe_df(df, config=...)` → repeat until no suggestions or `step_cap` (guarding against re-emitting the same patch id, like the existing convergence loops). Returns `(healed_config, applied_trail, healed_result)`. Reuses `review_config` / `apply_suggestion` — no new rule logic.
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
       RED/YELLOW/dip  -> review_config(df, config, verify=False)   # ONE cheap kernel call, no re-runs
    native absent      -> []            # graceful, silent
result.suggestions = candidates (verified=False) ; result.suggestions_available = bool(...)
CLI prints a one-line hint when non-empty
```
Default-on; suppress with `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`. Surfaced candidates are explicitly **unverified**; the no-net-negative guarantee is enforced at apply/heal time.

### `dedupe_df(df, suggest=True)` — verified, not applied
Same free-trigger gate, but `maybe_suggest(..., verify=True)`. Attaches verified `result.suggestions` (`verified=True`); applies nothing.

### `dedupe_df(df, heal=True)` — verified loop, applied
Runs `heal()`. Returns the healed `DedupeResult` (golden/clusters reflect the improved config; `result.config` is the improved config) plus `result.heal_trail` (auditable). `heal=True` implies verified; `heal` wins over `suggest` if both set. Gated by the same free trigger, so a near-ceiling input is a cheap no-op (the loop exits immediately).

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

## Posture & graceful-degrade

- **Additive / advisory**, like `lint_findings` (warn-default): a triggered run gains a populated field + a CLI hint; nothing is applied, nothing blocks. Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`.
- **No native → invisible + safe:** `maybe_suggest` returns `[]`, no error, no hint. Fully real for `[native]` users; **bundling the kernel into the base wheel is a named follow-on**, not this spec (it would turn the base into a platform wheel — large blast radius on the pure-Python / edge-safe story).
- **Cost guarantee:** on a GREEN/no-dip run the kernel is never called (free trigger short-circuits). Triggered default runs pay exactly one `verify=False` kernel call. Verified/heal cost is opt-in only.

## Testing

- **Unit (fast, no native):** `headroom_signal` (RED/YELLOW/dip → fire; GREEN+no-dip → none) over synthetic postflight; `maybe_suggest` gating (trigger-off → kernel **not** called, asserted; native-absent → `[]`; kill-switch → `[]`); `serialize_suggestions` shape; `heal()` (monkeypatched `review_config`) applies in order and terminates on the cycle guard / empty; **no-op parity** — a trigger-off default run is byte-identical to today (`suggestions == []`, no other field change).
- **Cross-surface:** match each surface's existing test pattern — MCP tool-count assertion, A2A skill-count assertion, CLI `--suggest`/`--heal`, REST endpoint, TUI panel, web router.
- **End-to-end (CI, native):** on `ncvr_synthetic`, a triggered default run surfaces candidates; `heal=True` improves F1 with the no-net-negative guarantee (reuses the suggester-gym machinery).

## Done criteria

- Default `dedupe_df` attaches raw candidates **only** when the free trigger fires **and** native is present; byte-identical no-op otherwise; kill-switch works.
- `suggest=True` (verified, not applied) and `heal=True` (verified loop, applied, `heal_trail`) work and are gated by the same free trigger.
- All seven surfaces expose the healer via the one core helper + serializer; surface count-assertions updated.
- Graceful no-native everywhere; the cost short-circuit (kernel not called on GREEN/no-dip) is proven by test.

## Out of scope (named follow-ons)

- Default **auto-apply** — `heal` stays opt-in; flipping it default-on is gated on broad-corpus net-positive evidence.
- **Bundling** the suggest kernel into the base wheel (the "reach every pip user" packaging change).
- The **TS / WASM** port of the suggest kernel.
- New suggestion **rules** (blocking-pass, field-weight) — rule coverage, a separate lever.
