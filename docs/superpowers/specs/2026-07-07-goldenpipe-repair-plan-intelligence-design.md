# GoldenPipe Repair-Plan Intelligence — Design

**Date:** 2026-07-07
**Status:** Approved (brainstorming), pending implementation plan
**Scope:** Phase 1 (advisory, with a value-level fine-typer). Phase 2 (active
config-injection) is designed-for but out of scope here.

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

## Approach (chosen: A, with an in-kernel fine-typer)

A static `(check, type_tag) -> [transforms]` table compiled into the kernel
(exactly how decision rules already live there). A new pure kernel function
resolves each column's `type_tag` and does the lookups.

Resolving `type_tag` needs a value-level signal: the domain packs and the coarse
`ColumnContext` classifier cannot tell an IBAN from a routing number from a
SWIFT code (the goldencheck-types finance pack groups all of these under coarse
type keys like `routing_number`, and `iban` is not even a hint). Distinguishing
them requires looking at the column's values. Because column values are just
strings, the fine-typer runs **inside the kernel** (regex + structural checks
are byte-identical across languages); the host's only new job is to **sample**
values from the DataFrame it already holds and pass them in as JSON strings.

Rejected alternatives:
- **B — Python-only rule in `decisions.py`.** Fastest, but Python-only; TS/WASM
  would never get it. Violates the cross-surface constraint.
- **C — repairs co-located in domain-pack YAML.** Elegant co-location and the
  packs already sync cross-surface, but only covers columns that matched a
  domain type, the pack type keys are too coarse to pick a specific validator
  (`routing_number` covers routing/aba/swift/bic), and it bloats the pack
  schema.
- **Host-side fine-typer.** Would duplicate the regex/check-digit logic per
  language and require its own parity gate. Keeping the fine-typer in the kernel
  gives one implementation and one parity gate.

## Architecture

### New kernel function

```
build_repair_plan(
    findings: [Finding],
    columns:  [ColumnInput],
) -> RepairPlan
```

Pure, deterministic, no I/O. Same functional shape as `apply_decision`.

```
ColumnInput { name: str, coarse_type: str, samples: [str] }   // samples: <=20 non-null, deterministic first-N
Finding     { column, check, message, severity }              // existing shape from artifacts["findings"]
```

Per column the kernel: runs the fine-typer over `samples` -> a fine `type_tag`
if a detector fires, else falls back to `coarse_type`, else omits the column (no
tag -> only `*` wildcard checks can match). Then it does the `(check, type_tag)`
lookups against the static table. All classification and mapping live in the
kernel — one call, one parity gate.

### Data model (new serde structs in `goldenpipe-core/src/model.rs`, mirrored Python/TS)

```
RepairPlan  { repairs: [RepairItem] }        // empty when nothing maps
RepairItem  { column, check, type_tag,
              suggested_transforms: [str],    // ordered, e.g. ["iban_validate"]
              reason }                        // human string, from the finding message
```

`RepairPlan` is an **artifact**, never a `Decision`. It is attached to
`ctx.artifacts["repair_plan"]`; it does not skip, insert, or abort any stage.
The empty inserted-stage `config` seam in `router.rs` (an inserted `PlannedSpec`
gets `config: Default::default()`) is where Phase 2 would later write the
suggested transforms — Phase 1 leaves it untouched.

### Data flow

1. GoldenCheck scan -> `findings` (already in `artifacts["findings"]`).
2. Host builds `ColumnInput`s: `name` and `coarse_type` from the existing
   `ColumnContext`, `samples` = up to 20 deterministic first-N non-null values
   from the DataFrame (cast to string).
3. Host calls `build_repair_plan(findings, columns)`.
4. Kernel fine-types each column, then does `(check, type_tag)` lookups; emits
   `RepairPlan`.
5. Host stores the artifact and appends one reasoning line per item.

## The fine-typer (in-kernel)

Per fine tag, a detector of the form `(name-hint, value-regex, optional
check-digit)`. Value structure is the disambiguator; the name-hint gates the
tags whose value shape is ambiguous with other fixed-length numeric ids.
Detection stays structural (lean regex) — full check-digit validation is the
GoldenFlow *validate* transform's job — **except** `credit_card`, where a Luhn
pass is the disambiguator against arbitrary 13-19 digit numbers.

A detector fires only if the majority of non-null samples match its value
pattern (guards against a single coincidental match). Detectors are tried in a
fixed order; first firing tag wins.

**Cross-engine byte-identity:** every detector regex uses explicit ASCII
character classes — `[0-9]`, never `\d`; `[0-9Xx]`, never `[\dX]`. `\d` is
Unicode-digit by default in Python `re` and Rust `regex` but ASCII in JS, so a
sample containing a non-ASCII digit would classify differently per surface and
break the parity gate. Explicit classes are identical across all three engines.

### Fine vocabulary (v1)

Value-distinctive (name-hint optional):

| tag | value structure (detection, not full validation) |
|---|---|
| `iban` | `^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$`, length 15-34 |
| `isin` | `^[A-Z]{2}[0-9A-Z]{9}[0-9]$` (12 chars) |
| `swift` | `^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$` (8 or 11) |
| `credit_card` | `^[0-9]{13,19}$` (strip spaces/dashes) **and** Luhn passes |

Name-hint required (value shape alone is ambiguous):

| tag | name-hint (any of) | value structure |
|---|---|---|
| `cusip` | cusip | `^[0-9A-Z]{9}$` |
| `npi` | npi | `^[0-9]{10}$` |
| `imei` | imei, imsi | `^[0-9]{15}$` |
| `ean` | ean, gtin, barcode | `^[0-9]{8}$` or `^[0-9]{13}$` |
| `isbn` | isbn | `^[0-9]{9}[0-9Xx]$` or `^[0-9]{13}$` |
| `aba_routing` | routing, aba | `^[0-9]{9}$` |

`vat` is dropped from v1 — country-specific formats are too fuzzy for a lean
detector.

## The mapping table

Static kernel data: a list of `(check, type_tag) -> [transforms]`. Lookup tries
the exact `(check, type)` first, then `(check, "*")` wildcard. Ordered,
deterministic, first-match wins.

### Coverage (v1)

Coarse rows (tag from `ColumnContext.coarse_type`):

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

Fine rows (tag from the in-kernel fine-typer); both `format_detection` and
`pattern_consistency` map to the type's validator:

| type_tag | suggested transform |
|---|---|
| `iban` | `iban_validate` |
| `isin` | `isin_validate` |
| `swift` | `swift_validate` |
| `cusip` | `cusip_validate` |
| `npi` | `npi_validate` |
| `imei` | `imei_validate` |
| `ean` | `ean_validate` |
| `isbn` | `isbn_validate` |
| `credit_card` | `luhn_validate` |
| `aba_routing` | `aba_validate` |

Every transform named above is verified to exist in the GoldenFlow registry.

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

## Error handling

- No findings, unknown checks/types, all-no-map, or empty samples ->
  `RepairPlan{repairs: []}`.
- Never raises, never blocks the pipeline.
- Unknown `check` or `coarse_type` is a silent no-match (forward-compatible with
  new checks and new domain packs), not an error.
- A column whose samples fire no fine detector falls back to its coarse tag; a
  column with no coarse tag and no fine match is omitted.
- `reason` is built from the finding's own `message`, truncated to the existing
  80-char finding-message convention.

## Privacy / cost of value sampling

Sampled values (possibly PII, e.g. card numbers) cross the planner boundary.
Mitigations:
- It is an **in-process** call — the data is already in host memory; nothing is
  serialized to disk or network.
- Samples are bounded (<=20 non-null values per column, deterministic first-N).
- The fine-typer never logs raw values — reasoning lines carry only the derived
  `type_tag` and the finding's own message.

## Surfacing (Phase 1, minimal)

- `ctx.artifacts["repair_plan"]` on the pipeline result object.
- One reasoning line per item on the existing reasoning-transparency channel,
  e.g. `repair: signup_date (future_dated) -> date_validate [12 rows dated after today]`.
- **No** new MCP tool or CLI flag in Phase 1 (YAGNI). Deferred to Phase 2.

## Testing

- **Rust golden vectors** (`goldenpipe-core/tests/golden_vectors.rs`): canonical
  `(findings, columns)` inputs -> exact `RepairPlan` JSON. Cross-surface source
  of truth.
- **Fine-typer matrix** (positive + negative per fine tag): an IBAN sample
  classifies as `iban`; a 9-digit routing number does **not** classify as
  `iban`; a bare unnamed 9-digit column stays coarse (no fine tag); a 16-digit
  number that fails Luhn is **not** `credit_card`. Parity-gated.
- **Python + TS parity**: the same fixtures through each surface, asserted
  byte-identical against the golden JSON, wired into the existing goldenpipe-core
  parity gate.
- **Box-runnable Python** unit tests for the sampling helper (bounded, first-N,
  null-skipping) and `build_repair_plan` via the pure Python path — no Rust
  build needed on the box.
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
