# 0060 — Feast (feature-store) provider: the metric-aware wedge, one layer over into ML

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `goldenmatch.semantic.feast` (`parse_feast_models` / `parse_feast_objects`, `feast_join_keys`, `certify_feast_feature_views`, `emit_feast_from_crosswalk`, `emit_feast_yaml`) + a `"feast"` dialect in `certify_semantic_model` • **Builds on:** [0049 (metric-aware key certification)](0049-metric-aware-key-certification.md), [0050 (resolved-crosswalk emit)](0050-resolved-crosswalk-emit.md), [0052 (Cube dialect)](0052-cube-dialect-and-osi-validation.md) • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md)

## Context
The semantic-layer wedge (0049) proved that a metric can be perfectly *defined* yet
numerically *wrong* when the entity keys its joins run on were never resolved. A
**feature store is the same join graph, one layer over into ML**: a Feast
`FeatureView` is keyed on an `Entity`, and the entity's `join_keys` are the identity
the features silently assume. The three break modes reappear and bite ML directly:

- **duplicated join key → fan-out** — an aggregated feature double-counts; a
  point-in-time join multiplies rows;
- **fragmented entity → undercount / training-serving skew** — one real entity split
  across keys trains the model on half a customer;
- **non-conformed keys across sources** — the feature view can't join to the entity.

Feast's `Entity(join_keys=[...])` maps one-to-one onto MetricFlow `entity (primary)`
and Cube `primary_key`, so the *exact* certifier applies. This surface fits the
"missing provider" filter (an established ecosystem that runs on entity keys and
assumes resolved identity as an input it never produces) that also selected the
metrics and ontology layers.

## Decision
A new dialect module `goldenmatch/semantic/feast.py`, structured exactly like the
Cube dialect (0052) — consume, certify (bridge A), emit (bridge B):

1. **Consume.** `parse_feast_models(source)` reads a declarative
   `{entities, feature_views}` doc (path / YAML / dict); `parse_feast_objects(
   entities, feature_views)` duck-types live Feast SDK objects (e.g.
   `store.list_entities()` / `store.list_feature_views()`) — **no `feast` import in
   goldenmatch**, so the package never depends on it. `feast_join_keys` resolves
   each feature view to the entity `join_keys` it rides on.
2. **Certify (bridge A).** `certify_feast_feature_views(repo, frames, resolve=…)`
   certifies each feature view's entity join key via `certify_key_integrity`, **with
   the view's features as the fan-out measures** — so the certificate quantifies
   exactly "how much does aggregating this feature over a not-truly-unique key
   inflate it." `resolve=True` adds the ER fragmentation/undercount tier, made
   metric-aware by `semantic_field_roles` (a feature value is never identity
   evidence — you don't merge two customers because they share a churn score).
3. **Emit (bridge B).** `emit_feast_from_crosswalk(crosswalk, …)` declares the
   GoldenMatch-resolved key as the entity `join_keys` and a feature view keyed on it,
   provenance (+ optional certificate verdict) in `tags.goldenmatch`.
   `parse_feast_models(emit_feast_yaml(...))` round-trips.
4. **Front-door wiring, no new surface.** `certify_semantic_model` auto-detects a
   top-level `feature_views:` as the `"feast"` dialect and dispatches to
   `certify_feast_feature_views`; `semantic_field_roles` learns the Feast role split
   (join_keys = identity, features = measures). The CLI (`certify-keys`), MCP
   (`certify_semantic_model`), and REST surfaces certify a feature repo with **no new
   MCP tool / CLI command / A2A skill** — same posture as Cube. Library-only,
   advisory, parity-free.

## Conformance to the two-engines frame (0047)
- **One authoritative owner** ✅ — an *adapter surface* over the existing
  `certify_key_integrity` (which is single-sourced through `key-integrity-core`,
  0059). GoldenMatch does **not** author features, feature transforms, or
  materialization — Feast keeps that. No second source of truth.
- **Conformance defines correctness** ✅ — Feast is another dialect target; the
  reader/emitter round-trips and the certifier is the same shared owner.
- **Compute vs. control stay distinct** ✅ — the Feast doc is small metadata (YAML /
  duck-typed objects), correctly not Arrow-marshaled; the certification rides the
  columnar key-integrity path.
- **Zero-config should embarrass the alternatives** ✅ — point it at a feature repo +
  the offline frames and get a per-feature fan-out / undercount report on the first
  run; Feast itself offers nothing here.

## Consequences / honest flags
- **Declaration only, no materialization.** Like every other dialect, this reads/
  writes the *declaration*; it does not run Feast's offline/online stores or
  point-in-time joins. `frames` are supplied by the caller (the offline source).
- **Feast discovery is deferred.** v1 is consume + certify + emit (bridges A + B),
  mirroring Cube's initial add. Generating a Feast repo from raw tables via
  `discover_semantic_model(dialect="feast")` is a follow-on (the discovery
  orchestrator is dialect-pluggable but out of scope here).
- **`parse_feast_objects` is duck-typed, not schema-locked.** It reads `.name` /
  `.join_keys` / `.features`-or-`.schema` / `.batch_source|.source` off whatever it's
  given; a future Feast rename of those attributes would need a follow-up (the
  declarative-doc path is unaffected).
- **No new parity surface** — verified: `feast` appears in zero gated Python surfaces
  (mcp_tools / cli_commands / a2a_skills / scorers / …), so `parity/goldenmatch.yaml`
  is unchanged, consistent with Cube.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
