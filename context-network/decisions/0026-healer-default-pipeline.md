# 0026 — Healer wired into the default pipeline (advisory, every surface)

**Status:** Accepted • **Shipped:** 2026-06-26 (branch `feat/healer-default-pipeline`, stacked on #1267/#1271/#1272)

## Context

The **healer** (`review_config`, the config-suggestion loop) is the engine of the
[healing-loop thesis](../foundation/project-definition.md): zero-config returns a
defensible config, the healer suggests self-verified tweaks, you apply them, results
improve, repeat. As of [ADR 0025](0025-healer-verify-gate-proxy.md) the self-verify
gate keeps only non-worsening suggestions (the "never makes results worse" guarantee).

But the healer was **opt-in** — a user only saw a suggestion if they knew to call
`review_config` by hand. The thesis says the loop is GoldenMatch's core workflow, yet
the default `dedupe_df` run never surfaced it. Closing that gap is the difference between
"a feature you can find in the docs" and "the product's default behavior."

The constraint: `review_config` re-runs the whole pipeline internally, and the gate's
`verify=True` simulates *each* candidate (apply → re-run) — far too expensive to pay on
every `dedupe_df`. A naive "always suggest" would multiply every run's cost.

## Decision

Wire the healer into the default pipeline as a **two-stage, cost-bounded advisory surface**:

1. **Free trigger.** After a normal run, read a *free* controller signal off the result
   already in hand (`postflight_report.controller_history` → RED/YELLOW health, or a
   scoring `dip_statistic ≥ 0.05`). No kernel call, no second pipeline. On a healthy
   result the surface short-circuits here — byte-identical timing.
2. **Cheap attach (default).** Only when the trigger fires, call a new **artifacts-in**
   `suggest_from_result(result, df)` that reuses the run's `scored_pairs`/`clusters`
   (no re-run) to attach *raw, unverified* candidate suggestions to `result.suggestions`.
3. **Opt-in deeper paths.** `dedupe_df(df, suggest=True)` pays the expensive verified
   gate; `dedupe_df(df, heal=True)` runs the full apply-and-re-run loop, recording
   `result.heal_trail` and returning the healed `result.config`.

The surface is present on **every surface** (the every-capability-on-every-surface
commitment): Python (`dedupe_df` `suggest=`/`heal=`), CLI (`--suggest`/`--heal` + a
free default-run hint), MCP (`review_config` tool), A2A (`review_config` skill), REST
(`GET /suggest`), web (`GET /api/v1/suggest`), and the TUI (Suggestions tab).

The healer requires `goldenmatch[native]` (the suggestion kernel is compiled). Every
surface **degrades gracefully** without the wheel: it attaches nothing and never raises.
Master kill-switch: `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`.

## Consequence

- The default `dedupe_df` run now advertises the loop without the user knowing it
  exists, while a healthy result pays **zero** extra cost (the free trigger guarantees
  no-op parity). This is the thesis made default behavior.
- The two-stage cost model keeps the default cheap (raw candidates) and reserves the
  expensive verified/heal work for explicit opt-in — no surprise cost multiplier.
- Native is now a *soft* dependency of a default-on surface: graceful-degrade keeps
  pure-Python installs working unchanged. Bundling the wheel into the default install
  is deferred (see the spec's "graceful-degrade now, bundle later").
- The advisory default is **not** auto-apply: the pipeline never silently mutates a
  user's config. `heal=True` is the only path that applies, and only on explicit request.
