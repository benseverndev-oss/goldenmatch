# 0049 — Metric-aware key certification: GoldenMatch as the identity layer under semantic layers

**Status:** accepted (2026-07-30, Ben) • **Shipped:** v1 (A) — `goldenmatch.semantic.certify_key_integrity` + MetricFlow reader + `key.integrity` analyzer + `goldenmatch_key_integrity` dbt test • **Planning:** [../planning/semantic-layer-interchange.md](../planning/semantic-layer-interchange.md) • **Frame:** [../architecture/one-product-two-engines.md](../architecture/one-product-two-engines.md) ([0047](0047-one-product-two-engines-architecture.md))

## Context
Every modern semantic layer (dbt Semantic Layer + MetricFlow, Cube, Open Semantic
Interchange) is a join graph, and every join runs on entity-key equality; measures
are defined *relative to* an entity. All three treat conformed / unique /
cross-source keys as an **assumed input they don't resolve** — so a metric can be
perfectly *defined* (governed, certified, OSI-exchanged, AI-queryable) yet
numerically *wrong* because the keys underneath were never resolved (duplicated
keys → `SUM`/`COUNT(DISTINCT)` fan-out; dirty keys → one entity fragmented across
keys → undercount; non-conformed keys → un-joinable). GoldenMatch already produces
the resolved-identity artifact these layers assume. This is the "wedge (A)" from
the planning doc: certify the keys a semantic model *already declares* — advisory,
never mutating a number.

## Decision
1. **Certify-only, advisory, never mutating (v1 scope).** `certify_key_integrity`
   emits a `KeyIntegrityCertificate` quantifying uniqueness-at-grain, per-measure
   fan-out, and (opt-in) entity fragmentation / undercount. It reports and
   quantifies; it never rewrites a metric or a key — this is *Advanced, never
   black-box* (commitment 5) by construction. No key emission (B), no OSI (C), no
   Cube parser in v1.
2. **Owner decomposition — no new source of truth.** The binding is an *adapter
   surface* (like the dbt package + the goldenmatch-kg shims), not a new engine.
   Structural uniqueness/fan-out is arrow-native compute; the undercount tier
   composes the **goldenmatch** ER pipeline (`dedupe_df`) + cluster comparison;
   the reporting projection is the **goldenanalysis** `key.integrity` analyzer
   (sibling to `match.rates` consuming a recall certificate). GoldenMatch does
   **not** author measures/metrics — MetricFlow/Cube keep that owner.
3. **Hold the identity ↔ metric-semantics boundary** (the 0007 DQ↔ER discipline,
   one level up). GM owns identity + keys; the semantic layer owns measures,
   metrics, and grain. The guard test: *am I adding a second source of truth for
   something MetricFlow/Cube already owns?*
4. **MetricFlow YAML first.** `parse_semantic_models` reads dbt/MetricFlow
   `semantic_models[].entities` (primary/natural) + `measures` + agg-time grain —
   the cleanest, most stable entity model, and dbt is already our warehouse-native
   surface. Cube/OSI readers are thin follow-on adapters.
5. **dbt test = structural SQL only.** `goldenmatch_key_integrity` is a pure-SQL
   threshold test (uniqueness + fan-out, no UDF, all adapters), sibling to
   `goldenmatch_match_quality`. The undercount/ER tier needs the engine and stays
   the Python/CLI capability in v1.
6. **Fail-open resolution.** The opt-in `resolve=True` tier is wrapped so any
   failure (offline model, controller refusal, tiny-frame degeneracy) leaves the
   resolution fields None with a note and never breaks the structural certificate.
   It pre-disables rerank/cross-encoder so certification never blocks on a download.

## Conformance to the two-engines frame (0047)
- **One authoritative owner per capability** ✅ — adapter surface composing
  existing owners (control-plane/compute ER + goldenanalysis reporting); no second
  source of truth for metric semantics.
- **Compute vs. control distinct** ✅ — structural is stateless arrow compute; the
  ER tier is a stateless `dedupe_df` read; semantic artifacts (YAML) are metadata.
- **Arrow at bulk boundaries** ✅ — the certifier is arrow-native (no polars); the
  semantic-model artifacts are small metadata, correctly not Arrow-marshalled.
- **Conformance defines correctness** ✅ — OSI (Apache Ossie) is an interchange
  spec; "GM emits/validates it" is a future conformance target. v1 anchors on the
  stable MetricFlow surface.
- **Parity gate** ✅ — the new `key.integrity` analyzer is declared `python_only`
  in `parity/goldenanalysis.yaml` (v1 is Python + dbt only; TS/WASM port is a
  follow-on), so the `analyzers` cross-language gate passes.

## Consequences / honest flags
- **Scope creep into "GM is a semantic layer" is the top risk.** Held by decisions
  1–3: GM never defines measures/metrics/grain.
- **OSI is young** (spec Sept-2025; Apache-incubating June-2026). v1 deliberately
  does not depend on it — MetricFlow-first, OSI as a later conformance play.
- **The undercount tier inherits ER's behavior** — on tiny/degenerate frames
  zero-config ER may not merge (documented toy-merge degeneracy); the tier is
  opt-in and fail-open, and the structural certificate stands alone.
- **No warehouse-side ER in dbt yet** — the dbt test is structural only; an ER
  materialization (undercount in-warehouse) is a follow-up.
- **Follow-ons (not v1):** key emission / codegen (B), OSI-native provider (C),
  Cube reader, TS/WASM analyzer port, a CLI/MCP front door (adds an `api_parity`
  MCP+CLI surface obligation).

---
**Classification:** decision/accepted • **Last updated:** 2026-07-30
