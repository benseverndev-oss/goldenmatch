# Semantic-model discovery ("GoldenModel") — design

Status: **PROPOSED (design only, not implemented)** — a scoped feature that sits on
top of the existing engines. This spec decomposes it into phases, names the exact
existing functions each phase reuses vs. the new glue, and gives a phased PR plan.
No code lands until this design is approved.

Program: Semantic-layer wedge (follows the certify/write-back arc:
`certify_key_integrity` → catalog write-back D4 → MCP/REST/CLI gate E). This is the
*generative* half — discovery — where the certify arc was the *validating* half.

## Thesis

Point GoldenMatch at a set of source tables (warehouse, files, connectors) and it
**proposes** a semantic model — entity types, keys, joins, measures, dimensions —
where **every declared key already carries its trust verdict**. A human reviews and
approves in their catalog; nothing auto-ships.

The differentiator is NOT better guessing. Every generic auto-semantic-model tool
(LLM-based especially) emits confident guesses. GoldenMatch's edge is that it
**already owns the falsification test**: `certify_key_integrity` /
`certify_cube_joins` prove each structural claim (this IS the key, this join is
many-to-one, this measure won't double-count) against the actual data. So discovery
is hypothesis generation and certification is the proof — the proposed model comes
**pre-graded**.

This is deliberately the zero-config / approach-the-expert / never-black-box
posture (`context-network/foundation/project-definition.md` decision tests): propose
+ certify + hand to the expert, never a silent oracle.

## Architecture-frame fit (`one-product-two-engines.md`)

- **One authoritative semantic owner per capability.** The certifier stays the
  single source of "is this structurally valid." Discovery REUSES it; it never
  forks a second validator. Same for ER (identity graph is the entity owner) and
  classification (auto-config classifier / InferMap).
- **Conformance defines correctness.** Every proposed key / join / measure is
  VALIDATED against the actual data before it reaches the draft — not asserted.
- **Compute vs. control stay distinct.** Profiling / classification / ER /
  certification = the Arrow-native batch **compute** engine (existing). The
  discovered model + its provenance + versioning = a **control-plane** artifact
  (new, but a document, not a kernel).
- **Kernelize on measurement.** The heavy math (FD discovery, key certification)
  already has kernels. Discovery is orchestration + light inference heuristics —
  NOT a new kernel. Do not kernelize the proposer loops.

## Raw material — what already exists (verified 2026-08-03)

| Capability | Existing entry point |
|---|---|
| Column semantic-type classification + profile | `core/autoconfig.py::profile_columns`, `auto_configure_df`; InferMap (GoldenSchema) domain/concept mapping |
| Functional-dependency discovery (the key backbone) | `core/quality.py::fd_identity_scores` → `goldencheck.functional_dependencies` |
| Cross-table column alignment | `core/schema_match.py::auto_map_columns` |
| Entity resolution + durable entities | `dedupe_df`, Identity Graph (`goldenmatch/identity/`), `resolve_clusters` |
| **Key certification (the falsification test)** | `semantic/key_integrity.py::certify_key_integrity` |
| **Join-cardinality certification** | `semantic/cube.py::certify_cube_joins`, `semantic/osi.py::certify_osi_relationships`, `semantic/serving.py::certify_serving_joins` |
| Whole-model certification | `semantic/certify.py::certify_semantic_model`, `certification_report_dict` |
| Catalog emitters (write the proposed model out) | `semantic/{metricflow,cube,osi}.py::emit_*`, `certify=` write-back (D4), `crosswalk.py::build_resolved_crosswalk` |

So the certifier, the entity engine, the FD/key math, the classifier, and the
emitters are all in place. The discovery layer is largely **wiring + a few new
inference heuristics**, with the certifier as the backbone.

## The pipeline

```
tables ─▶ [0 profile/classify] ─▶ [1 FD + key-CERTIFY] ─▶ [2 ER: entity types]
                                                                   │
                                        ┌──────────────────────────┘
                                        ▼
                             [3 FK + join-CERTIFY] ─▶ [4 measure/dim propose]
                                        │                       │
                                        └───────────┬───────────┘
                                                    ▼
                       [5 emit model + certify_semantic_model] ─▶ pre-graded draft ─▶ human approves
```

### Phase 0 — Profile & classify (REUSE)
`profile_columns` over each table: per column semantic type (`email`/`identifier`/
`date`/`geo`/`numeric`/`description`/…), cardinality ratio, null rate, parse rates,
samples. This is the vocabulary the rest reasons over. No new code.

### Phase 1 — Key discovery (NEW ranker; certifier proves)
Candidate keys per table from three signals: near-unique cardinality (`ratio ≈
1.0`), `col_type == identifier`, and — the strong one — **FD discovery**
(`fd_identity_scores`: a column-set that functionally determines the rest of the row
is the key). Then the differentiator: **certify each candidate with
`certify_key_integrity`** — is it unique at grain, what is the fan-out. Output: a
ranked list of *certified* keys per table + a loud flag on tables with NO clean key
(the ones that will silently double-count). NEW: `KeyCandidate` ranker
(`semantic/discovery/keys.py`).

### Phase 2 — Entity discovery (REUSE ER; NEW clustering)
Which tables describe the same real-world thing. Resolve across tables (or read the
Identity Graph): if `customers`, `app_users`, `crm_contacts` resolve to overlapping
entities they are the `Person` entity type. `auto_map_columns` aligns their columns.
NEW: cross-table **entity-type clustering** (`semantic/discovery/entities.py`) over
(resolved-entity overlap + column-semantic signature). Output: entity types +
tables/keys that realize each + the conformed `resolved_entity_id`.

### Phase 3 — Join / relationship discovery (NEW inference; certifier proves cardinality)
FK inference: a column in table B whose values are a subset of table A's certified
key, with matching semantic type → candidate join. Then validate the **cardinality**
with the SAME machinery — the "one" side of a `many_to_one` must be unique at grain,
which is exactly what `certify_cube_joins` / `certify_serving_joins` already check.
Output: a certified join graph with directions, no spurious fan-outs. NEW: FK
inference (`semantic/discovery/joins.py`); certification is reused.

### Phase 4 — Measure & dimension proposal (NEW proposer, certifier-gated)
- **Measures:** numeric columns on fact-shaped tables (high row count, FK-heavy) →
  propose `SUM`/`COUNT`/`AVG`. A measure is proposed safe-to-`SUM` ONLY if its grain
  key certified clean — the Phase-1 fan-out directly gates which measures are
  trustworthy.
- **Dimensions:** low-cardinality categorical/date/geo columns + resolved entity
  attributes.
- **Grain:** the certified key from Phase 1.

NEW: `semantic/discovery/measures.py`.

### Phase 5 — Emit, certify, hand off (REUSE)
Assemble a draft MetricFlow / Cube / OSI model via the existing emitters, run
`certify_semantic_model` over the whole thing, attach the `key_integrity` verdict
block to every key (D4 write-back). Deliverable: a **draft model where every key is
already graded** — the human reviews/approves in their catalog. Nothing auto-ships.
Public entry point: `discover_semantic_model(tables, *, dialect="metricflow") ->
ProposedModel`.

## New surface (the glue) vs. reused

| REUSE (exists) | BUILD (new) |
|---|---|
| classifier, profiling | key-candidate **ranker** (Phase 1) |
| `fd_identity_scores` | cross-table **entity-type clustering** (Phase 2) |
| ER + Identity Graph | **FK / join inference** (Phase 3) |
| `auto_map_columns` | **measure/dimension proposer** (Phase 4) |
| `certify_key_integrity` / `certify_cube_joins` | the **orchestrator** `discover_semantic_model` + `ProposedModel` dataclass |
| emitters + `certify_semantic_model` | *(optional, advisory)* LLM **namer** |

## Honest boundaries (what it will NOT do)

- **Structure, not intent.** It discovers that `status` determines the row and is
  low-cardinality (→ a dimension). It does NOT know `status='C'` means "churned."
  Business naming/glossary needs a human or an *advisory* LLM namer — bolted on the
  SAME way the config-suggestion healer is (`context-network/decisions/0027-healer-wasm-ts.md`):
  opt-in, self-verified, never authoritative. Default off; the structural discovery
  is byte-deterministic without it.
- **A draft, not an oracle.** Gets the expert to ~80% and marks its own uncertainty
  (every key carries its verdict + confidence). Matches the North Star decision
  tests.
- **Certification is the honest core; discovery is the accelerator.** The reason to
  trust GM's proposal over a pure-LLM one is that each guess is PROVEN against the
  data before you see it — not that the guesses are cleverer.

## Cross-surface posture

- **Python-first.** `discover_semantic_model` + `ProposedModel` in
  `goldenmatch/semantic/discovery/`. Emits via the existing dialect emitters, so the
  output is a normal MetricFlow/Cube/OSI file.
- **CLI:** `goldenmatch discover-model <tables...> --dialect metricflow -o model.yml`
  (proposes + certifies + writes; prints the per-key verdict table). Mirrors
  `certify-keys`.
- **MCP / REST:** a `discover_semantic_model` tool + `POST /semantic/discover`,
  returning the proposed model + `certification_report_dict` (same shared serializer
  as the certify surface — one contract).
- **TS parity:** the *classifier/certifier* halves are already TS-ported; the
  discovery orchestrator is a Python-only follow-up (it leans on ER + FD discovery,
  which are Python-side — the "distributed/ER is Python-only by design" boundary in
  the TS CLAUDE.md). Declare it Python-only up front, not a silent gap.

## Phased PR plan (each additive, no-op when unused)

1. **PR-1 `ProposedModel` + Phase 1 key discovery.** `semantic/discovery/keys.py`:
   `discover_keys(table) -> list[KeyCandidate]` (FD + cardinality + `col_type`,
   certified via `certify_key_integrity`). Pure, testable on fixtures. No orchestrator
   yet.
2. **PR-2 Phase 3 join discovery** over caller-supplied keys: `discover_joins(tables,
   keys) -> list[JoinCandidate]`, cardinality-certified via `certify_cube_joins`.
3. **PR-3 Phase 2 entity typing:** `discover_entity_types(tables) -> list[EntityType]`
   over ER overlap + column signatures.
4. **PR-4 Phase 4 measure/dimension proposer** (fan-out-gated measures).
5. **PR-5 orchestrator + emit:** `discover_semantic_model(tables) -> ProposedModel`
   assembling PR-1..4, emitting via the dialect emitters + `certify_semantic_model`,
   attaching the D4 verdict block. CLI `discover-model`.
6. **PR-6 MCP/REST surfaces** (`discover_semantic_model` tool + `POST /semantic/discover`),
   shared serializer.
7. **PR-7 (optional) advisory LLM namer**, default off, self-verified. New module
   `semantic/discovery/namer.py`. Annotates a finished `ProposedModel` with business
   names for **entity types, dimension columns, low-cardinality dimension VALUES**
   (`status='C'` -> "churned"), and **measures** — attached as
   `ProposedModel.naming: list[NameSuggestion]` (default `[]`, so structural discovery
   is byte-identical without it; the emitted YAML + certification are computed BEFORE
   naming and never altered). `NameSuggestion` = `{target, kind, suggested_name,
   confidence, verified, evidence}`. **Two-pass self-critique:** per table, one
   `propose` call (all targets + their structural evidence, incl. up to 30 sampled
   distinct values per categorical dimension) then one `verify` call that critiques
   each proposed name against its evidence; names that aren't supported / fall below a
   confidence floor are kept but flagged `verified=False` (surfaced, never silently
   applied). Backend is an injectable `NamerBackend` Protocol (`propose(prompt)->str`);
   the default `load_namer_backend()` reuses `llm_labeler.detect_provider` +
   `_call_llm_with_retry` (the `goldenmatch[llm]` extra) and **abstains** (returns
   `None` -> `naming=[]`, never raises) when no provider/key resolves. Opt-in per call
   (`discover_semantic_model(..., name=False)`, CLI `discover-model --name`, MCP/REST
   `name` bool); `GOLDENMATCH_SEMANTIC_NAMER=0` is a hard kill-switch. No new MCP
   tool / CLI command -> no `api_parity` tool-list change.
8. **PR-8 (optional) `--apply-names`.** An opt-in mode that writes the VERIFIED names
   from `ProposedModel.naming` into the emitted MetricFlow YAML, turning the advisory
   layer into an applied catalog. Only entity + measure names have a native MetricFlow
   slot, so: entity business name -> the semantic_model's `label:`; measure name -> that
   measure's `label:`; dimension-column + value-glossary names -> a
   `meta.goldenmatch.glossary` block (a sibling of the key-integrity verdict already in
   `meta.goldenmatch`). **Post-certification + cosmetic:** structural discovery, emit,
   and certify run unchanged; the labels/meta are applied to the final `model.yaml`
   AFTER certification (they don't touch grain/joins/measures, so the verdict stays
   valid and is not recomputed). Only `verified=True` suggestions are applied; the full
   `naming` list is still exposed. `apply_names=False` (default) -> byte-identical
   emitted YAML. New pure `namer.apply_names(model) -> str` (no LLM; operates on the
   already-produced `naming` + `yaml`), called by the orchestrator when
   `apply_names=True` (which implies `name=True`). Surfaces:
   `discover_semantic_model(..., apply_names=False)`, CLI `--apply-names`, MCP/REST
   `apply_names` bool; `"yaml"` is added to `ProposedModel.to_dict()` so the applied
   catalog is visible on every surface. No new MCP tool / CLI command.
9. **PR-9 (optional) Cube/OSI discovery emit.** `discover_semantic_model(dialect=...)`
   emits `cube` and `osi` drafts in addition to `metricflow` (it used to raise on
   anything else, even though certification already spans all three and the
   `emit_cube_yaml` / `emit_osi_yaml` emitters + dataclasses exist). A new
   `discovery/emit.py` builds the dialect YAML from the discovered structure: per
   table, the grain -> `primary_key` (a `primary_key` `CubeDimension` / an
   `OsiDataset.primary_key` list), discovered dimensions -> `CubeDimension`/`OsiField`,
   sum-safe measures -> `CubeMeasure`/`OsiMetric`, and the key-integrity certificate ->
   `meta.goldenmatch` (cube) / `custom_extensions.goldenmatch` (osi). The certified
   **trustworthy** join graph is emitted natively: `CubeJoin` (a `{CUBE}.<fk> =
   {<to>.<pk>}` SQL condition) / `OsiRelationship` (`from_columns`/`to_columns`). Every
   emitted model is re-certified end-to-end (dialect auto-detected), and the metricflow
   path stays byte-identical. `apply_names` becomes dialect-aware: cube -> `title:` on
   cube/dimension/measure + `meta.goldenmatch.glossary`; osi -> `label`/`description` on
   field/dataset + `custom_extensions.goldenmatch.glossary`. `dialect` already plumbs
   through CLI `--dialect` + MCP/REST `dialect`; no new params. Follow-on: PR-10 lifts
   the single-column key/FK limitation (the certifier + `KeyCandidate.columns` +
   Cube/OSI composite primary keys already support multi-column).
10. **PR-10 (optional) compound + self-referential keys.** Lifts the single-column
    restriction on the discovery side (the certifier, `KeyCandidate.columns`, and all
    three emit paths already carry multi-column keys). `discover_keys` gains a
    **fallback pairs** compound search: when no single-column candidate is trustworthy
    (the grain double-counts), it takes the signal-bearing key-ish columns (capped,
    highest-cardinality first), certifies each **pair** with `certify_key_integrity`
    (which already accepts `key=[c1, c2]`), and admits the trustworthy compounds
    (`signals=["compound"]`), re-ranked trustworthy-first. `discover_joins` lifts the
    self-join exclusion for **self-referential single-column FKs** (a column whose
    values are a subset of the table's OWN certified key, `fk_col != key_col` — e.g.
    `employees.manager_id -> employees.employee_id`); same value-subset + certification
    path. Compound (multi-column) FKs and 3+-column keys stay documented follow-ons. No
    surface change — a compound grain flows through the existing metricflow entity /
    Cube `primary_key` dims / OSI `primary_key` list emit; nothing new to plumb.

## Frontier: from certified structure to a full semantic layer

The PR-1..10 arc derives the certified *structure* (grain, joins, entities, grain-gated
measures/dimensions). These slices close the gap to a semantic layer a human would
actually query. All are deterministic (no LLM) and default-on unless noted.

11. **Dimension hierarchies via FD.** New `discovery/hierarchies.py`:
    `discover_hierarchies(table, columns) -> list[Hierarchy]`. Among a table's dimension
    columns, detect **near-functional-dependencies** (a finer level determines a coarser
    one: `city → state → country`, threshold default 0.95 to survive dirty rows via a
    group-by "fraction of determinant groups mapping to a single dependent value" test),
    pick each column's **immediate parent** (the highest-cardinality coarser column it
    determines), and extract maximal coarse→fine chains as `Hierarchy(table,
    levels=[country, state, city], confidence)`. Deterministic → default-on, attached to
    `ProposedTable.hierarchies` + `ProposedModel.hierarchies` and emitted into
    `meta.goldenmatch.hierarchies` (Cube/OSI/MetricFlow — no dialect has a native
    hierarchy slot in these dataclasses); `to_dict` includes it. Reuses the FD machinery
    (`fd_identity_scores` is per-column; this adds the pairwise group-by check). Follow-on
    frontier slices: metrics (ratios/derived/cumulative), time intelligence (time spine +
    grains), cardinality (m:n bridge / 1:1), SCD dimensions, a model completeness score,
    warehouse-scale derivation off `information_schema`, catalog reconciliation, and
    real-LLM namer validation.
12. **Metrics.** New `discovery/metrics.py`: `discover_metrics(measures, grain)` proposes
    certifiable business metrics **only when the grain is trustworthy** (so the ratios
    don't double-count): per sum-safe measure an **average** (`avg_m = SUM(m)/COUNT(grain)`
    — always meaningful at a clean grain, MetricFlow's canonical *ratio* metric), and per
    sum-safe measure PAIR a **ratio** (`m1_per_m2 = SUM(m1)/SUM(m2)`, pool-capped to bound
    the pair explosion). `Metric(name, kind, numerator, denominator, expression)`; on
    `ProposedTable`/`ProposedModel.metrics` + `to_dict`, emitted NATIVELY (MetricFlow
    top-level `metrics:` ratio metrics + a declared count measure; Cube calculated
    `number` measures + a `count`; OSI `OsiMetric`s). Deterministic, default-on. *Derived
    semantic* metrics (`profit = revenue − cost`) stay a namer/advisory follow-on (they
    need to know which measure is revenue vs cost).
13. **Time intelligence.** New `discovery/time_intelligence.py`. `discover_time_dimension(
    table, dimensions)` picks the primary date column (`kind="date"`, name-preferring
    `date`/`created`/`timestamp`), infers its **finest granularity from the data** (a
    cheap value scan: time-of-day → `day`; all values on month/quarter/year starts →
    that coarser grain), and proposes the drill granularities up from it
    (`TimeDimension(table, column, grain, granularities)`). `discover_time_metrics(
    measures, time_dimension)` derives per sum-safe measure the **MTD** / **YoY** /
    **rolling-7d** variants (`TimeMetric(name, base, kind, window)`). Both on
    `ProposedTable`/`ProposedModel` (+ `to_dict`). Emitted so the engine computes time
    comparisons automatically: MetricFlow sets `defaults.agg_time_dimension` + a `type:
    time` dimension with `time_granularity`, MTD/rolling as `type: cumulative` metrics
    and YoY as a `type: derived` offset metric; Cube keeps the `type: time` dim +
    granularities and the variants in `meta.goldenmatch.time`; OSI `is_time` +
    `custom_extensions`. Deterministic, default-on.
14. **Cardinality.** `discover_joins` sets `relationship="one_to_one"` when the FK is
    unique on the from side; new `discovery/cardinality.py`
    `discover_bridges(proposed_tables, joins)` detects many-to-many junction tables (a
    trustworthy 2-column compound key whose columns are certified FKs to two different
    tables) as `Bridge(...)` on `ProposedModel.bridges`. Deterministic, default-on.
15. **SCD / temporal dimensions.** New `discovery/scd.py`: `discover_scd(table, columns)`
    flags a Slowly-Changing-Dimension (Type 2) table — validity columns (name-pattern
    `valid_from/valid_to`, `effective_*`, `start_date/end_date`, `from_date/to_date`)
    and/or an `is_current`/`is_active` flag — then **structure-confirms** it (the business
    key repeats and `(business_key, valid_from)` is unique at grain, so it really is a
    versioned dimension, not a stray `end_date`). `SCDDimension(table, business_key,
    valid_from, valid_to, current_flag, scd_type)` on `ProposedTable`/
    `ProposedModel.scd_dimensions` + `to_dict` + `meta.goldenmatch.scd`. Deterministic,
    default-on.
16. **Model completeness / trust score.** New `discovery/completeness.py`:
    `score_model(proposed_tables, joins)` aggregates the existing signals into a
    headline **grain-weighted** 0..1 `ModelCompleteness` (grain coverage 0.5,
    connectivity 0.25, sum-safe-measure coverage 0.25 — a certified grain is
    load-bearing, so it dominates) plus an explicit `gaps` list (`no_grain` /
    `no_measures` / `isolated`), so "80% complete" always names the tables that are why.
    On `ProposedModel.completeness` + `to_dict`. Pure self-assessment; no new detection.
    Deterministic, default-on.
17. **Warehouse-scale derivation off `information_schema`.** New `discovery/warehouse.py`:
    `read_information_schema(columns, table_constraints, key_column_usage, tables=None)`
    reads the three ANSI `information_schema` relations (as pyarrow tables / row dicts —
    no live DB connection, so it stays testable and credential-free) into a
    `WarehouseManifest` of `CandidateTable(name, columns, declared_pk, declared_fks,
    row_count)`. **The honest thesis marker:** every declared PK/FK is `certified=False`
    — Snowflake/BigQuery/Redshift do NOT enforce PK/FK, so an `information_schema`
    declaration is exactly the kind of guess this arc PROVES against data, never a
    certificate. The manifest is a **planning artifact**, not a model:
    `plan_certification(manifest)` ranks the tables worth pulling+certifying first
    (has-declared-PK, then FK-referenced in-degree = spine/dimension tables, then smaller
    row_count, then name) with per-table warnings that declared constraints are unproven.
    A thin `discover_from_manifest(manifest, loader)` bridge (loader: `name -> table`)
    pulls each candidate's data and runs the normal certified `discover_semantic_model`,
    so nothing about the certification-first pipeline changes — `information_schema` just
    tells you *which* tables to point it at at warehouse scale. Deterministic, default-on.
18. **Catalog reconciliation.** New `discovery/reconcile.py`:
    `reconcile_model(proposed, existing)` diffs a discovered `ProposedModel` against an
    **already-parsed** existing catalog (a list of MetricFlow `DeclaredKeySpec` or Cube
    `Cube` — reusing the existing `parse_semantic_models` / `parse_cube_models` readers,
    so no new format code). Both sides normalize to a common `(name, key, measures)` shape
    and produce a `Reconciliation` of typed `TableDiff`s: `only_in_model` /
    `only_in_catalog` (tables), `grain_drift` (the table exists in both but the discovered
    certified grain ≠ the catalog's declared key), and `measure_only_in_model` /
    `measure_only_in_catalog`. **The differentiator over a text diff:** the discovered
    side is CERTIFIED, so a `grain_drift` where the discovered grain is trustworthy and
    the catalog's declared key is *not* the certified grain is marked `proven=True` — a
    provable defect in the catalog ("your model declares `order_id` the key; we proved the
    grain is `order_id + line_no`, so every SUM at that grain double-counts"), not a
    stylistic difference. `Reconciliation.in_sync` is the headline. On its own module +
    `to_dict`; deterministic, default-on. **Scope cut (logged):** v1 covers tables, grain,
    and measures; cross-dialect *join*-edge reconciliation and a **LookML** reader (no
    parser exists yet) are follow-ons — not silently omitted, explicitly deferred.

19. **Real-LLM namer validation (eval harness).** New `discovery/namer_eval.py`:
    `score_naming(suggestions, gold)` is a pure, deterministic scorer of the advisory
    namer's (PR-7) output against a labeled `{target: accepted_name(s)}` gold map —
    `NamerQuality(coverage, accuracy, precision, verified_accuracy, results)` where a name
    matches after normalization (lowercase, alnum-only; NOT a stemmer — plural/alias
    variants must be listed as explicit gold aliases). `run_namer_eval(model, tables,
    gold, *, backend)` is a thin wrapper that names then scores. The scorer is
    backend-agnostic: CI exercises it with hand-built suggestions and a dict-driven fake
    backend (deterministic, no API calls); the real provider (`load_namer_backend`) is
    **opt-in** behind `GOLDENMATCH_NAMER_EVAL_LIVE` (a skipped live test), so the harness
    never spends live calls in CI. This closes the arc: slices 11-18 are deterministic
    features; slice 19 is the *measurement* for the one non-deterministic part.

Each PR is independently useful (PR-1 alone gives "certified key discovery per
table"). The certifier is exercised from PR-1, so the "pre-graded" property holds at
every step.

## Open questions

- **Entity-typing threshold:** how much cross-table resolved-entity overlap declares
  "same entity type"? Calibrate against a labeled multi-table corpus (the
  `_pick_missing_semantics` precedent — calibrate, don't derive).
- **FK inference at scale:** value-subset checks are O(distinct) per column pair;
  bound with the profiler's cardinality + a bloom/minhash pre-filter before the exact
  check.
- **Grain ambiguity:** a table can have several certified keys at different grains
  (order_id vs order_id+line_no). Propose the finest certified grain + surface the
  coarser ones as alternates, don't guess one.

## Success criteria

- On a labeled multi-table corpus, the proposed model's keys/joins match the
  ground-truth semantic model at ≥ target precision, AND every proposed key's verdict
  is correct (a certified-trustworthy key IS unique at grain in the data).
- Zero false "trustworthy" verdicts — the certifier's guarantee must hold end-to-end
  (an untrustworthy key never ships graded trustworthy).
- A human accepts the draft with fewer edits than starting from scratch (the
  accelerator claim), measured on a real project.
