# 0061 — Malloy (malloydata.dev) BI dialect for the semantic-layer certifier

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.malloy` (`parse_malloy_models`, `malloy_join_keys`, `certify_malloy_joins`, `emit_malloy_source`, `emit_malloy_from_crosswalk`) + a `"malloy"` dialect in `certify_semantic_model` • **Builds on:** [0049 (metric-aware key certification)](0049-metric-aware-key-certification.md), [0052 (Cube dialect)](0052-cube-dialect-and-osi-validation.md), [0060 (Feast provider)](0060-feast-feature-store-provider.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
The next missing provider surface from the semantic-layer roadmap filter (an
established ecosystem that runs on entity keys and assumes resolved identity) is the
BI-modeling lane. **Malloy** (Google's open semantic modeling language) is the
cleanest BI reader/emitter to add: its unit is a `source` whose identity is a
declared `primary_key`, and joins (`join_one` / `join_many` / `join_cross`) ride on
it — structurally almost identical to Cube's `primary_key` + `joins` block, so the
metric-aware certifier applies unchanged. (LookML and Power BI are the other BI
candidates; LookML's explore/join model is messier to parse, so Malloy is the
higher-leverage first cut — LookML is a follow-on.)

## Decision
A new dialect module `goldenmatch/semantic/malloy.py`, structured exactly like Cube
(0052) and Feast (0060) — consume, certify (bridge A), emit (bridge B):

1. **Consume.** `parse_malloy_models(source)` reads a structured `{sources: [...]}`
   projection (dict / YAML / path) OR raw Malloy `.malloy` DSL text via a **focused
   declaration parser** (brace-matched `source:` blocks → `primary_key`, `join_*`
   with `on` conditions, `measure:`, `dimension:`). `malloy_join_keys` resolves each
   join to the columns it rides on (parsed from the `on` condition).
2. **Certify (bridge A).** `certify_malloy_joins(model, frames, resolve=…)` certifies
   the **one-side** key of each join via `certify_key_integrity` — the joined (`to`)
   source for `join_one`, but the declaring (`from`) source for `join_many` (its key
   is what must be unique; certifying the many-side FK would spuriously flag a
   fan-out). `join_cross` has no key and is skipped. Falls back to the one-side
   source's declared `primary_key` when the `on` columns can't be parsed. Same
   direction logic as `certify_cube_joins`.
3. **Emit (bridge B).** `emit_malloy_from_crosswalk` emits a crosswalk source keyed
   on `source_pk` + a `join_one` from the source to it, returning
   `(malloy_text, provenance)` — Malloy has no metadata slot in this subset, so the
   GoldenMatch provenance (+ optional certificate verdict) is returned alongside
   rather than embedded (the one deliberate shape difference from the YAML dialects,
   whose `meta.goldenmatch` carries it inline). `emit_malloy_source` round-trips
   through the DSL parser.
4. **Front-door wiring, no new surface.** `certify_semantic_model` auto-detects a
   top-level `sources:` as the `"malloy"` dialect and dispatches to
   `certify_malloy_joins`; `semantic_field_roles` learns the Malloy role split
   (primary_key = identity, measures/dimensions). CLI / MCP / REST certify a Malloy
   model with **no new MCP tool / CLI command / A2A skill** — parity-free, like Cube
   and Feast.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — an *adapter surface* over the single-sourced
  `certify_key_integrity` (0059). GoldenMatch does not author Malloy measures,
  queries, or the model — Malloy keeps that. No second source of truth.
- **Conformance defines correctness** ✅ — Malloy is another dialect target;
  `parse_malloy_models(emit_malloy_source(...))` round-trips.
- **Compute vs. control stay distinct** ✅ — the model is small metadata (DSL text /
  dict), correctly not Arrow-marshaled; certification rides the columnar key path.

## Consequences / honest flags
- **The DSL parser is a focused subset, not full Malloy grammar.** It reads the
  declaration constructs (`source`/`primary_key`/`join_*`/`measure`/`dimension`) via
  brace-matching + regex — enough for the metric-aware wedge. Full-fidelity parsing
  (nested refinements, SQL blocks, pipelines) is out of scope; the exact,
  dependency-free path is the structured `{sources: [...]}` projection, which the
  front door detects. A Malloy source it can't parse simply yields fewer joins, not
  a wrong certificate.
- **Emit returns a tuple, not a string.** Unlike the YAML dialects, provenance is
  returned alongside the text (no metadata slot). Callers wanting it embedded can
  attach it in a `// ` comment or a sidecar — deferred.
- **Declaration only, no query engine.** Like every dialect, this reads/writes the
  declaration; it does not run Malloy's compiler or query the warehouse. `frames`
  are caller-supplied.
- **No new parity surface** — verified: `malloy` appears in zero gated Python
  surfaces, so `parity/goldenmatch.yaml` is unchanged, consistent with Cube/Feast.
- **LookML / Power BI are the remaining BI dialects**, and data contracts (ODCS) the
  remaining adjacent standard — follow-ons from the same filter.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
