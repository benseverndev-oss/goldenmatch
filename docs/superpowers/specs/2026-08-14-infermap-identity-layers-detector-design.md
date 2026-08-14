# Identity-layers detector — detect the entity roles present in a dataset

**Date:** 2026-08-14
**Status:** Design (draft, pre-approval). Tracks [#2574](https://github.com/benseverndev-oss/goldenmatch/issues/2574).
**Scope:** `packages/python/infermap/` + `packages/python/goldencheck-types/` (and their TS mirrors).
**Feeds:** [#2575](https://github.com/benseverndev-oss/goldenmatch/issues/2575) (per-block / per-partition config) — you cannot vary matching config per segment until you can detect the segments.

## The gap

A real dataset does not contain *one* kind of entity. A loan tape references a
**lender** (a bank), a **borrower** (a person), a **servicer**, and a **payor**.
A claims file references an **insurer**, a **provider**, a **patient**, and a
**subscriber**. A telemetry table references a **machine**, a **site**, and an
**operator**. Each of those is a distinct **identity layer** — its own entity
population, with its own natural key, its own error profile, and (this is the
point) its own right answer for how to match it.

GoldenMatch today cannot see any of that. The nearest capability, `infermap`'s
`detect_domain`, answers a **different and much coarser question**:

- `packages/python/infermap/infermap/detect.py` scores 16 **industry verticals**
  (finance, healthcare, insurance, …) by name-hint coverage and returns **one
  winner for the whole dataset**, refusing on ties (`detect.py:127-128`).
- The score is a **coverage fraction** — `hits / len(columns)` (`detect.py:118-119`)
  — so it measures "how finance-y is this table", not "who is in this table".
- Detection reads **column names only**. `df` is used solely for `df.columns`
  (`detect.py:72`); `value_signals` in the packs are consumed by the *mapping*
  scorers, never by detection.

The consequence is that a loan tape is labelled `finance` and nothing else. The
fact that `lender_name` and `borrower_name` are **two different populations that
must never be deduped against each other** is invisible to every downstream stage.

### Why the existing pieces do not already cover this

Three near-misses, each genuinely close and each structurally wrong for the job:

1. **Domain packs model field *types*, not *parties*.** `finance.yaml` declares
   `merchant: name_hints: [merchant, vendor, payee, counterparty]` — one flat
   canonical type collapsing four distinct roles. There is no taxonomy of entity
   kinds (person / organization / asset) crossing verticals.
2. **`FieldGroupSpec` groups fields *within* one entity, not *across* parties.**
   `goldencheck_types/types.py:50-63` exists to promote correlated columns
   (address parts, name parts) from one winning source record during
   survivorship. That is intra-entity cohesion; layers are inter-entity
   separation. Same shape, opposite purpose.
3. **The M×N engine is deliberately 1:1.** `engine.py:373` runs
   `linear_sum_assignment` for an optimal one-source-to-one-target mapping, an
   explicit non-goal to exceed (`2026-03-29-infermap-design.md`). A table hosting
   three parties, each with a `name` and an `id`, is *inherently* many-to-many
   against a canonical type list. **Routing role detection through the assignment
   engine would be a category error** — see "Why this is not a mapping problem".

## The reframe that makes this tractable

The naive reading of "detect entity roles" is *per-column classification*: label
each column with a role. That reading is what collides with the 1:1 engine, and
it throws away the signal that actually matters.

**An identity layer is a *group of columns that together describe one party*.**

```
lender_name, lender_id, lender_address   →  layer(role=lender,   kind=organization)
borrower_name, borrower_ssn, borrower_dob →  layer(role=borrower, kind=person)
```

Reframed this way the problem is **column clustering by party**, and three things
fall out immediately:

- It is a **labelling** pass, not a mapping pass — many-to-many by construction,
  so it never touches the 1:1 assignment.
- The output is **directly the segment definition #2575 needs**: a layer is a
  set of columns describing one population, which is exactly what you would
  block, score, and threshold independently.
- It is **mostly domain-free**. Real schemas mark parties with affixes
  (`lender_*`, `*_borrower`, `payor_*`) far more reliably than any vertical
  vocabulary. The cheap universal signal carries most of the weight, and the
  domain packs only have to supply the *names* of roles, not enumerate every
  schema shape.

## Design

### The model: kind × role

Two levels, deliberately separated because they have different lifetimes.

| | What it is | Cardinality | Where it lives |
|---|---|---|---|
| **`kind`** | *What the entity is* — `person`, `organization`, `asset`, `place`, `unknown` | Small **closed** set | Hard-coded constant |
| **`role`** | *The part it plays* — `lender`, `borrower`, `payor`, `merchant`, `operator` | **Open**, vertical-specific | Declared in domain packs |

`kind` is closed because it is the axis GoldenMatch actually acts on (you match
people differently from machines) and an open set there would make downstream
behaviour unpredictable. `role` is open because every vertical invents its own
party vocabulary and that is the packs' job.

A layer carries both: `role=lender, kind=organization`. When the role is
unrecognised but a party is clearly present, `role="unknown"` with a real `kind`
is still a useful, honest answer.

### Signal 1 — affix clustering (domain-free, primary)

Columns that share a token prefix or suffix which is **not itself a canonical
type token** are almost certainly one party. Reuse the existing tokenizer
(`detect.py:_tokens`, splits on `[_\-.\s]+`) so behaviour matches the rest of
infermap.

```
borrower_name, borrower_ssn, borrower_dob   → shared leading token "borrower"
name_of_lender, id_of_lender                → shared trailing token "lender"
```

Guards, each earning its place:

- **A qualifier must qualify something.** A shared token only opens a layer if
  the *remaining* tokens map to ≥2 distinct canonical types, or ≥2 columns. A
  single `borrower_name` alone is a weak singleton layer, not a confident one.
- **Type tokens are not qualifiers.** `account_number` / `account_id` share
  `account`, but `account` is a canonical type token in `finance.yaml` — so it
  must not open an "account party". The pack's own `name_hints` supply the
  stop-list, which means the guard gets better as packs improve rather than
  needing its own hand-maintained list.
- **Frequency floor.** A token appearing in every column (`col_`, `f_`) is a
  table-wide prefix, not a party.

### Signal 2 — role hints (pack-declared, corroborating)

Domain packs gain an optional `roles:` block. This is additive: absent it, a
pack behaves exactly as today.

```yaml
roles:
  lender:
    kind: organization
    name_hints: ["lender", "originator", "creditor", "bank"]
    typical_types: ["account_number", "routing_number"]
  borrower:
    kind: person
    name_hints: ["borrower", "debtor", "obligor"]
    typical_types: ["ssn", "person_name"]
```

`typical_types` is **corroboration, never a requirement** — it raises confidence
when the layer's columns map to the expected canonical types, and its absence
never vetoes a layer that affix clustering found. Making it a requirement would
make the detector fail exactly where it is most needed: unfamiliar schemas.

### Combining, and the honest-refusal contract

Score each candidate layer from affix strength (column count, qualifier
distinctiveness) and role-hint agreement. Then adopt `detect_domain`'s existing
discipline, which is already the right one: **when the evidence does not
separate, say so rather than guess.** Every layer carries a `reason` from the
same vocabulary shape as `DetectionResult.reason`:

`affix` · `role_hint` · `affix+role_hint` · `singleton` · `low_confidence`

An unrecognised party is reported as `role="unknown"` with its columns and its
evidence — not silently dropped, and not force-fitted to the nearest known role.
Columns belonging to no layer land in `unassigned`. **A dataset with one entity
population correctly yields exactly one layer** — the single-entity case is not a
degenerate path, it is the common case and must stay clean.

### Output shape

New dataclasses in `goldencheck-types` (the shared wire-format package), so
goldenpipe and goldenmatch can consume layers without importing infermap:

```python
@dataclass(frozen=True)
class IdentityLayer:
    role: str                      # "lender" | ... | "unknown"
    kind: str                      # person|organization|asset|place|unknown
    columns: list[str]             # the columns constituting this layer
    score: float
    reason: str                    # affix | role_hint | affix+role_hint | singleton | low_confidence
    evidence: dict[str, Any]       # infermap-internal; consumers must not depend on shape

@dataclass(frozen=True)
class LayerDetectionResult:
    layers: list[IdentityLayer]
    unassigned: list[str]
    domain: str | None             # from the EXISTING detect_domain, unchanged
    schema_version: int = SCHEMA_VERSION
```

`evidence` is explicitly opaque, matching the precedent already set for
`FieldMapping.evidence` (`types.py:89-90`).

### Public API

```python
detect_identity_layers(df, domain=None, min_score=0.3) -> LayerDetectionResult
```

Purely additive. **`detect_domain` / `detect_domain_detailed` keep byte-identical
behaviour** — `goldenpipe.stages.infer_schema` depends on them
(`infer_schema.py:106`), and this design must not perturb that path.

## Why this is not a mapping problem (the load-bearing boundary)

Worth stating explicitly because it is the single decision that keeps this
feature cheap. Role detection is **many-to-many labelling**: one role spans many
columns, and one column can corroborate a role while still mapping to its own
canonical type. Mapping is **1:1 assignment**: one source column to one target
field.

Forcing layers through `optimal_assign` would either break the 1:1 invariant the
engine's correctness rests on, or silently drop parties past the first. So layers
run as a **separate pass over column names + the pack's role block**, and the
mapping engine is untouched. Per decision 0047's *one authoritative semantic
owner* test: mapping keeps its owner, layers get a new one, neither becomes a
second source of truth for the other.

## Non-goals

- **Not changing `detect_domain`.** Vertical detection stays exactly as-is.
- **Not entity resolution.** This *labels* layers; goldenmatch decides what to do
  with them (#2575).
- **Not LLM-based.** The `LLMScorer` precedent (host-only, off by default) applies
  if it is ever wanted; Wave 1 is deterministic and free.
- **Not value-based in Wave 1.** See Wave 3.
- **Not cross-table / foreign-key inference.** Single-frame only.

## Waves

**Wave 1 — types + detector (Python), the load-bearing wave.**
`RoleSpec` + `IdentityLayer` + `LayerDetectionResult` in `goldencheck-types`;
**`SCHEMA_VERSION` 3 → 4**; optional `roles:` blocks for an initial 3–4 verticals
(finance, insurance, healthcare, manufacturing); `infermap/layers.py` implementing
both signals; `detect_identity_layers` public API; fixture corpus + tests.

> The cross-language parity contract (`goldencheck-types/CLAUDE.md`) requires the
> TS type mirror in the **same PR** as any `SCHEMA_VERSION` bump. That is
> non-negotiable and is scoped into Wave 1 — types only, not the detector.

**Wave 2 — TS detector + surfaces.** Port `layers.ts`, barrel-export it from
`core/index.ts` (`detectDomainDetailed` once shipped in `detect.ts` without a
barrel export and cross-package consumers could not reach it — the documented
lesson to not repeat),
cross-surface fixture locking Python == TS. Then MCP tool + CLI command **and**
the `parity/infermap.yaml` declaration — the `api_parity` gate requires the tool,
the command, and the manifest entry to move together, so surfaces land as one PR,
never piecemeal.

**Wave 3 — value signals.** Second stage that runs **only on ambiguity**, so the
common path stays name-only and free: a 9-digit numeric column named `aba`
→ `routing_number` → implies an organization layer even with no affix.

**Wave 4 — consumers.** `goldenpipe.stages.infer_schema` emits layers into
`InferredSchema`; goldenmatch consumes them as the segment labels for #2575.

Kernelization (`infermap-core::detect_identity_layers`) is **deferred on
purpose**, per decision 0047's *kernelize on measurement* test: this is
tokenizing a few hundred column names, not a bulk path. It earns a kernel when a
measurement says so, and not before.

## How we know it works

A committed fixture corpus of **hand-labelled multi-party schemas** — loan tape,
insurance claim, invoice/AP, IoT telemetry, plus **single-party controls** (a
plain customer table must yield exactly one layer) and **adversarial cases**
(`account_number`/`account_id` must NOT open an "account party"; a table-wide
`col_` prefix must not become a layer).

Metric: **layer-level precision / recall** against the labels, plus exact-set
column assignment per layer. Guardrail: a byte-identical `detect_domain`
regression test — the existing path may not move.

## Open decisions (for approval)

Three calls made here that materially shape the design. Each has a recommendation
and a stated default; flag any you want changed before implementation.

1. **`kind` is a closed set** (`person`/`organization`/`asset`/`place`/`unknown`).
   *Recommended*, because it is the axis matching behaviour keys off. The
   alternative — pack-extensible kinds — makes downstream behaviour open-ended.
2. **Affix clustering is the primary signal**, role hints corroborate.
   *Recommended*, because it works on unfamiliar schemas and degrades gracefully;
   hint-primary would only ever find parties we already enumerated.
3. **Wave 1 is name-only.** *Recommended* — it keeps detection free and matches
   `detect_domain`'s existing cost profile. Value signals (Wave 3) are the natural
   escalation and are designed to run only when names are ambiguous.

## Anchor files

`infermap/detect.py` · `infermap/domain_pack.py` · `infermap/engine.py` ·
`infermap/identity.py` · `goldencheck-types/goldencheck_types/types.py` ·
`goldencheck-types/goldencheck_types/_domains/*.yaml` ·
`goldenpipe/goldenpipe/stages/infer_schema.py` ·
`packages/typescript/infermap/` (mirror) · `parity/infermap.yaml` (Wave 2)
