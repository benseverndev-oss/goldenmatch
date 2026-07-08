# GoldenPipe Repair-Plan Intelligence — Phase 2 (Gated Active Application) — Design

**Date:** 2026-07-08
**Status:** Approved (brainstorming), pending implementation plan
**Depends on:** Phase 1 (PR #1577, branch `feat/goldenpipe-repair-plan`) — must be green/merged before Phase 2 implementation. Phase 2 builds on Phase 1's `repair_plan` artifact.

## Goal

When a gate (`apply_repairs`) is on, make the GoldenPipe brain actually apply the
Phase-1 repair suggestions: the `goldenflow.transform` adapter reads the advisory
`repair_plan` artifact and applies the suggested **fixer** transforms to the
flagged columns. When the gate is off, behavior is byte-identical to Phase 1.

## Constraints

- **No kernel change.** The Phase-1 `goldenpipe-core` kernel already produces the
  `repair_plan` artifact. Phase 2 is entirely host-side (Flow/Check adapters).
  This fits "planner is in the kernel; execution is a per-language host."
- **Byte-identical when the gate is off.**
- **Cross-surface** (Python + TS), consistent with Phase 1.

## Approach (chosen: Flow adapter reads the artifact)

The `goldenflow.transform` adapter, when `apply_repairs` is on, reads the
`repair_plan` artifact (already in `ctx.artifacts["repair_plan"]`), converts the
suggested transforms to a `GoldenFlowConfig` (`transforms: [{column, ops}]`), and
runs. Rejected: extending the kernel `Decision` model to carry a config payload
(the Phase-1 "inserted-stage config seam" sketch) — heavier (cross-surface kernel
change + parity gate) and unnecessary since the pipeline already contains the Flow
stage and the artifact already exists.

## Safety model — fixer-only application (the crux)

GoldenFlow transforms split into two kinds:
- **Fixers** — return the cleaned column value (in-place clean). Safe to auto-apply.
- **Assertions** — the `*_validate` transforms return a **boolean** series
  (`scalar_dtype="bool"`). Applied as a column op they **overwrite** the column
  with `True`/`False`, destroying the data.

Phase 1's mapping suggested validators (`iban_validate`, `date_validate`, …) as
advice for check-digit / format findings. As advice that is correct; as an
auto-applied column op it is destructive. So Phase 2 applies **only fixers** and
**skips assertions**, logging each skip.

Note: goldenflow's own `auto_apply` flag is **not** the discriminator — genuine
fixers (`fix_mojibake`, `date_parse`, `email_normalize`, `name_proper`) are all
`auto_apply=False`. The discriminator is the return type (fixer value vs bool).
Phase 2 uses a curated allowlist, not runtime registry introspection.

**FIXERS allowlist (host policy, identical Python/TS):**
```
fix_mojibake, normalize_unicode, date_parse, email_normalize,
email_canonical, name_proper, phone_national, zip_normalize
```
Everything else in a `suggested_transforms` list (all `*_validate`) is an
assertion → skipped + logged.

**Honest consequence:** the fine-identifier findings (IBAN/CUSIP/NPI/…
check-digit failures) contribute **nothing** to active application — a malformed
identifier has no safe automatic fix, so it stays flagged. Phase 2's value is
targeted auto-cleaning (mojibake, email/name/phone/zip/date normalization) **only
on columns the checker flagged** — more surgical than goldenflow's blanket
zero-config auto-detect.

## The gate

`apply_repairs: true` as a key in the `goldenflow.transform` stage's config. The
adapter **pops** it before building the `transform_df` call, so it never leaks
into `GoldenFlowConfig`. Declarative, per-pipeline, local to the acting stage.
Absent/false → the existing code path runs untouched.

## Merge / auto-detect semantics — surgical (replace)

`transform_df` is either/or: non-empty `config.transforms` → **explicit** mode;
empty → **auto-detect** mode (there is no clean "auto-detect plan + merge" API).

When `apply_repairs` is on:
- Build repair specs from the artifact (fixers only, grouped by column, dedup
  preserving order).
- **If there are ≥1 fixer specs or the stage already had user-declared
  transforms:** run explicit mode.
  - No user transforms → `config.transforms = [repair fixer specs]` (auto-detect
    is bypassed for that run — the documented consequence).
  - User transforms present → per column: `user ops ++ repair ops`, deduping
    exact duplicates (user-first). Columns only in repairs get their own spec.
- **If there are zero fixer specs** (all-assertion plan) **and no user
  transforms:** inject nothing — the stage runs auto-detect exactly as before (do
  NOT flip to explicit-empty).

## Data flow

1. Check stage produces `findings` + `column_contexts` and (Phase-1 producer)
   attaches `repair_plan` to `ctx.artifacts` (Python already; TS added here).
2. Flow adapter `run()`: `apply = stage_config.pop("apply_repairs", False)`.
3. If `apply` and `repair_plan` present: `specs, skipped = repair_transform_specs(repair_plan)`.
4. Build the merged `GoldenFlowConfig` per the surgical rules; log `skipped`
   (assertion ops) into the manifest/reasoning.
5. `transform_df(df, config=merged)` (or the existing path when not applying).

## Components

### Python (box-testable)
- `goldenpipe/repair_host.py` — add:
  - `FIXERS: frozenset[str]` (the allowlist above).
  - `repair_transform_specs(repair_plan: dict) -> tuple[list[dict], list[dict]]`:
    returns `(specs, skipped)` where `specs = [{"column", "ops"}]` (fixers only,
    grouped, deduped) and `skipped = [{"column", "op"}]` for logging.
- `goldenpipe/adapters/flow.py` — pop the gate; when applying, build the merged
  config (surgical) and call `transform_df(df, config=...)`; record skipped
  assertions. The existing non-applying path is unchanged.

### TS (CI-only)
- `src/core/repairHost.ts` — **new**: mirror `repair_host.py`'s producer glue
  (`sampleColumn`, `buildColumnInputs`, `attachRepairPlan`) so the TS pipeline
  produces the artifact (Phase-1 gap), plus the identical `FIXERS` +
  `repairTransformSpecs`.
- `src/core/adapters/check.ts` — wire `attachRepairPlan` (mirror `check.py`).
- `src/core/adapters/flow.ts` — the consumer (gate + merge + apply), mirror of
  `flow.py`.

The `FIXERS` set is host policy (not the parity-gated kernel), defined
identically in both hosts; a code comment cross-references, and this spec is the
single source of the list.

## Error handling

- Gate off / no artifact → existing path, byte-identical.
- All-assertion repair plan + no user transforms → inject nothing (auto-detect
  keeps running; never flip to explicit-empty).
- `transform_df` errors on an injected fixer → propagate (the user opted into
  applying; failures must be visible). The spec conversion/merge never raises.

## Testing

### Python (box)
- `repair_transform_specs`: mixed fixer+assertion item → specs contain only
  fixers, grouped/deduped; assertion-only item → empty specs + skipped entry.
- flow.py gate **off** → identical to today (no config injected; auto-detect
  path unchanged).
- flow.py gate **on** + fixer repair (e.g. `email_normalize`) → column
  transformed, manifest shows it.
- flow.py gate **on** + assertion-only (`iban_validate`) → no injection, column
  **not** bool-replaced, auto-detect still runs.
- flow.py gate **on** + user transforms + repair → merged user-first, deduped.

### TS (CI-only)
- `repairHost.ts` produce + `check.ts` attach integration (artifact appears).
- `flow.ts` gate-off identity + gate-on fixer apply.

## Rollout

Opt-in via `apply_repairs` in the Flow stage config. Default off → byte-identical.
No new kernel symbols, no parity-gate changes.

## Out of scope

- Kernel `Decision`-carried config injection (rejected approach).
- Applying assertion transforms to a derived `<col>_is_valid` column (the
  `{column, ops}` schema has no per-op output-column routing).
- Auto-detect + repairs augmentation (two-pass) — rejected in favor of surgical.
