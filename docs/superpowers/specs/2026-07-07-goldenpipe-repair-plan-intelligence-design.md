# GoldenPipe Repair-Plan Intelligence — Design

**Date:** 2026-07-07
**Status:** Approved (brainstorming), pending implementation plan
**Scope:** Phase 1 (advisory). Phase 2 (active config-injection) is designed-for but out of scope here.

## Goal

Turn GoldenCheck findings into specific, actionable GoldenFlow-transform
remediations. Today the brain's Check-to-Flow decision is generic ("there are
issues, route to Flow"). Repair-plan intelligence emits a per-column,
per-finding plan naming the exact transform(s) that would fix each issue — e.g.
a `future_dated` finding on a date column suggests `date_validate`; a
`pattern_consistency` finding on an IBAN column suggests `iban_validate`.

## Constraints

- Must live in the pyo3-free `goldenpipe-core` kernel so it is **byte-identical
  across Python / Rust / TS / WASM** (CI parity-gated), matching every other
  brain decision.
- Deterministic. No LLM in the hot path.
- **Phase 1 is advisory only**: it emits an artifact and reasoning text and
  changes **zero executed stages**. A pipeline's output is byte-identical
  whether or not repair-planning runs.

## Approach (chosen: A)

A static `(check, type_tag) -> [transforms]` table compiled into the kernel
(exactly how decision rules already live there). A new pure kernel function does
the lookups; the host resolves per-column types from machinery it already runs
and passes them in as plain data. The kernel never sees a DataFrame or a
classifier.

Rejected alternatives:
- **B — Python-only rule in `decisions.py`.** Fastest, but Python-only; TS/WASM
  would never get it. Violates the cross-surface constraint.
- **C — repairs co-located in domain-pack YAML.** Elegant co-location and the
  packs already sync cross-surface, but only covers columns that matched a
  domain type (coarse `date`/`email` on unmatched columns get nothing) and
  bloats the pack schema. Worth revisiting for fine types later.

## Architecture

### New kernel function

```
build_repair_plan(findings: [Finding], column_types: {col: TypeTag}) -> RepairPlan
```

Pure, deterministic, no I/O. Same functional shape as `apply_decision`.

### Data model (new serde structs in `goldenpipe-core/src/model.rs`, mirrored Python/TS)

```
RepairPlan  { repairs: [RepairItem] }        // empty when nothing maps
RepairItem  { column, check, type_tag,
              suggested_transforms: [str],    // ordered, e.g. ["iban_validate"]
              reason }                        // human string, from the finding message
```

`Finding` reuses the existing shape already in `artifacts["findings"]`:
`{column, check, message, severity}`.

`RepairPlan` is an **artifact**, never a `Decision`. It is attached to
`ctx.artifacts["repair_plan"]`; it does not skip, insert, or abort any stage.
The empty inserted-stage `config` seam in `router.rs` (an inserted `PlannedSpec`
gets `config: Default::default()`) is where Phase 2 would later write the
suggested transforms — Phase 1 leaves it untouched.

### Data flow

1. GoldenCheck scan -> `findings` (already in `artifacts["findings"]`).
2. Host resolves `column_types` (see Type resolution).
3. Host calls `build_repair_plan(findings, column_types)`.
4. Kernel does `(check, type_tag)` lookups; emits `RepairPlan`.
5. Host stores the artifact and appends one reasoning line per item.

## The mapping table

Static kernel data: a list of `(check, type_tag) -> [transforms]`. Lookup tries
the exact `(check, type)` first, then `(check, "*")` wildcard. Ordered,
deterministic, first-match wins.

### Starter coverage (v1)

| check | type_tag | suggested transforms |
|---|---|---|
| `encoding_detection` | `*` | `fix_mojibake`, `normalize_unicode` |
| `future_dated` | `date` | `date_validate` |
| `temporal_order` | `date` | `date_validate` |
| `stale_data` | `date` | `date_validate` |
| `format_detection` | `date` | `date_parse` |
| `format_detection` | `email` | `email_normalize` |
| `pattern_consistency` | `email` | `email_canonical` |
| `pattern_consistency` | `name` | `name_proper` |
| `format_detection` | `phone` | `phone_validate` |
| `pattern_consistency` | `phone` | `phone_national` |
| `format_detection` | `zip` | `zip_normalize` |
| `format_detection` | `iban` | `iban_validate` |
| `pattern_consistency` | `iban` | `iban_validate` |
| `format_detection` | `cusip` | `cusip_validate` |
| `pattern_consistency` | `cusip` | `cusip_validate` |
| `format_detection` | `isin` | `isin_validate` |
| `pattern_consistency` | `isin` | `isin_validate` |
| `format_detection` | `npi` | `npi_validate` |
| `pattern_consistency` | `npi` | `npi_validate` |
| `format_detection` | `imei` | `imei_validate` |
| `pattern_consistency` | `imei` | `imei_validate` |
| `format_detection` | `ean` | `ean_validate` |
| `pattern_consistency` | `ean` | `ean_validate` |
| `format_detection` | `isbn` | `isbn_validate` |
| `pattern_consistency` | `isbn` | `isbn_validate` |
| `format_detection` | `credit_card` | `luhn_validate` |
| `pattern_consistency` | `credit_card` | `luhn_validate` |
| `format_detection` | `aba_routing` | `aba_validate` |
| `pattern_consistency` | `aba_routing` | `aba_validate` |
| `format_detection` | `swift` | `swift_validate` |
| `pattern_consistency` | `swift` | `swift_validate` |
| `format_detection` | `vat` | `vat_validate` |
| `pattern_consistency` | `vat` | `vat_validate` |

Every transform above is verified to exist in the GoldenFlow registry.

### No-map policy (explicit, not an error)

Structural / match / drift checks have no single-column transform remedy and
produce **no** `RepairItem`: `unique`, `key_uniqueness_loss`, `duplicate_rows`,
`near_duplicate_rows`, `fuzzy_duplicate_values`, `referential_integrity`,
`functional_dependency`, `fd_violation`, `cross_column`,
`cross_column_validation`, `composite_key`, `correlation_break`, `cardinality`,
`benford_drift`, `distribution_drift`, `entropy_drift`, `type_drift`,
`pattern_drift`, `new_correlation`, `new_pattern`, `null_correlation`,
`nullability`, `required`, `range`, `range_distribution`, `bound_violation`,
`sequence_detection`, `unmapped_column`, `identity_safe_pk`.

`nullability`/`required` are deliberately no-map: filling nulls is a judgment
call, not a mechanical repair. The table is pure data, so promoting any no-map
check later is a one-line addition plus a golden vector.

## Type resolution (host side)

A small per-language helper `resolve_type_tags(contexts, detect_result) ->
{col: TypeTag}`. This is the only piece outside the kernel, because it reuses
per-language machinery. Priority:

1. **Domain-pack fine type** when InferMap detect ran and the column matched a
   pack type (`iban`, `npi`, `vin`, ...).
2. else **coarse `ColumnContext.inferred_type`** (`email`, `name`, `date`,
   `phone`, `zip`, ...).
3. else **omit** the column — no tag means it can only match `*` wildcard checks
   such as `encoding_detection`.

The kernel receives only the resolved `{col: tag}` dict, so parity is preserved.

## Error handling

- No findings, unknown checks/types, or all-no-map -> `RepairPlan{repairs: []}`.
- Never raises, never blocks the pipeline.
- Unknown `check` or `type_tag` is a silent no-match (forward-compatible with new
  checks and new domain packs), not an error.
- `reason` is built from the finding's own `message`, truncated to the existing
  80-char finding-message convention.

## Surfacing (Phase 1, minimal)

- `ctx.artifacts["repair_plan"]` on the pipeline result object.
- One reasoning line per item on the existing reasoning-transparency channel,
  e.g. `repair: signup_date (future_dated) -> date_validate [12 rows dated after today]`.
- **No** new MCP tool or CLI flag in Phase 1 (YAGNI). Deferred to Phase 2.

## Testing

- **Rust golden vectors** (`goldenpipe-core/tests/golden_vectors.rs`): canonical
  `(findings, column_types)` inputs -> exact `RepairPlan` JSON. Cross-surface
  source of truth.
- **Python + TS parity**: the same fixtures through each surface, asserted
  byte-identical against the golden JSON, wired into the existing goldenpipe-core
  parity gate.
- **Box-runnable Python** unit tests for `resolve_type_tags` (coarse fallback,
  fine-type priority, omit-on-unknown) and `build_repair_plan` via the pure
  Python path — no Rust build needed on the box.
- **Empty-plan-when-unused** test proves the byte-identical-when-inactive
  guarantee (a pipeline with no findings, or only no-map findings, emits an empty
  plan and unchanged executed stages).

## Rollout

Pure-additive. The planner runs and attaches the artifact unconditionally (it is
advisory and cheap) but changes zero executed stages, so every existing
pipeline's output is unchanged. No feature flag in Phase 1.

## Phase 2 (out of scope, designed-for)

Gated (`apply_repairs=True`) config-injection: the brain writes the suggested
transforms into the `goldenflow.transform` stage's currently-empty inserted-stage
`config`, so Flow applies exactly the brain's picks. Phase 1 proves the mapping
is correct as advice before letting it drive execution.
