# Hand-rolled dbt → GoldenMatch config converter (distill + verify)

**Date:** 2026-07-26
**Status:** draft (design) — pending Ben's approval
**Predecessors / reuse:**
- Splink config converter — `specs/2026-07-13-splink-config-converter-design.md` (shipped) — the `from_splink` pattern (`ConversionReport`, `RecognizedLevel`, `CoverageSummary`).
- Splink auto-verify — `goldenmatch/config/splink_verify.py` — the label-free engine-vs-engine agreement primitive (`pair_set` / `pairwise_prf`) this spec **reuses wholesale**.
- dbt integration — `packages/dbt/goldensuite/` (`run_goldenmatch_dedupe()` for DuckDB) — the existing GM-in-dbt surface this converter complements.

## The pitch (why this exists)

> A company has ~10k lines of hand-rolled dbt implementing entity resolution / dedup spread across dozens of models. The converter ingests the dbt project and says: **"these 10k lines boil down to *this* 30-line reusable GoldenMatch config — and here's proof it reproduces 97% of your existing clusters."**

The value is **distillation + proof**, NOT faithful SQL translation. A 10k-line hand-rolled ER pipeline almost always encodes a *small* set of real decisions (a few blocking keys, a handful of comparison fields + thresholds, some survivorship rules) buried in sprawl. The converter extracts that intent and **measures how close the resulting config gets to the pipeline's existing output**. Lossy extraction is acceptable *because it is verified*, exactly as the Splink converter's approximate mappings are acceptable because `--verify` measures agreement.

### Audience & framing

Far larger than the Splink converter's audience: **everyone deduping in dbt**, not just probabilistic-ER practitioners. The consolidation/legibility moment (*"your 10k lines are this config"*) plus a measured agreement number is the thing data teams need to justify replacing unmaintained SQL sprawl with a tested engine.

Two distinct value stories, surfaced honestly per project:
1. **Fuzzy/probabilistic ER sprawl** → an *accuracy + consolidation* story (GM does the fuzzy matching the SQL did badly, in a tested engine).
2. **Exact keep-latest dedup sprinkled everywhere** (the common case) → a *consolidation + maintainability* story (10k lines → a 20-line tested config), NOT an F1 story. The report must say which applies.

## Non-goals

- **NOT a faithful SQL-to-config translator.** We do not attempt to reproduce every CTE, window, or business rule. Anything not recognized is *reported*, never silently dropped.
- **NOT push-button.** Output is a *proposed* config + a coverage/agreement report for human review — an accelerator + audit, not a magic button.
- **NOT a general SQL parser.** We parse the dbt **manifest** (structured), not blind `.sql` files.
- **NOT a runtime dependency on dbt for GM.** The converter reads dbt artifacts; it does not embed dbt.

## The key enabler: parse the manifest, not raw SQL

The Splink converter was tractable because Splink hands you a *typed settings object*. Hand-rolled dbt is the opposite — arbitrary SQL — EXCEPT dbt compiles to structured metadata that recovers most of the structure we need:

- **`manifest.json`** (`dbt compile` / `dbt docs generate` / `dbt parse`): every model's **compiled SQL**, the full **DAG** (`ref()`/`source()` lineage), columns, descriptions, and **tests** (`unique` / `not_null` / `relationships`).
- **`catalog.json`** (`dbt docs generate`): column types + row counts per model (helps classify columns, and gives before/after row counts for the consolidation story).
- **The DAG** localizes the ER step: we analyze the handful of models feeding the identity/master/dim/deduped tables, not the whole warehouse.

So the input is `target/manifest.json` (+ optional `catalog.json`), produced by a single `dbt compile` the user already knows how to run. No warehouse credentials required for extraction (only for the optional verify against a live output table).

## Architecture

```
dbt project ──(dbt compile)──▶ manifest.json (+ catalog.json)
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
             1. IDENTIFY        2. EXTRACT          3. EMIT
             ER models         ER signals          GoldenMatchConfig
             (DAG + naming)    (per model)         + ConversionReport
                                      │                  │
                                      ▼                  ▼
                             4. VERIFY (optional)  5. REPORT
                             config vs the         CoverageSummary +
                             pipeline's existing   agreement + "couldn't
                             output table          extract" list
```

Mirrors `from_splink`'s shape: a recognizer layer (`RecognizedSignal` analogous to `RecognizedLevel`), a `ConversionReport` of findings, a `CoverageSummary`, and a `verify_against_dbt` that reuses the Splink verify math.

### 1. Identify the ER models

The DAG + naming heuristics pick the models that *do* entity resolution, so we don't analyze transformation models that aren't ER:

- **Naming signals** (case-insensitive substrings): `dedup`, `dedupe`, `_dim`, `_master`, `golden`, `mdm`, `identity`, `_unique`, `canonical`, `_deduped`, `resolve`, `xref`, `crosswalk`, `survivor`.
- **Shape signals** in the compiled SQL: window dedup (`ROW_NUMBER()`/`RANK()` + `QUALIFY`/outer filter), self-joins (a model joining a source to itself on non-PK columns), `dbt_utils.deduplicate` / `generate_surrogate_key`, `GROUP BY` collapsing a natural key with aggregates.
- **Test signals**: a `unique` test on a non-surrogate column is a strong "this is the identity key" hint.
- **DAG position**: models that are *terminal-ish* (feed marts / exposures) and *fan-in* from staging.

Output: a ranked list of candidate ER models with a confidence + the signal(s) that fired. Low-confidence models are reported, not silently included.

### 2. Extract ER signals per model

Recognizers over the **compiled SQL** (dialect-aware, see below). Each emits a `RecognizedSignal(kind, columns, params, source_model, confidence, sql_excerpt)`:

| dbt signal (in compiled SQL / manifest) | → GoldenMatch config element |
|---|---|
| `PARTITION BY <cols>` in a dedup window; `GROUP BY <natural key>`; `JOIN … ON <keys>`; `unique` test cols; `generate_surrogate_key([<cols>])` args | **blocking keys** (+ candidate exact matchkey) |
| `jaro_winkler(...)`, `levenshtein(...)`, `edit_distance(...)`, `soundex(...)`, `jaro_similarity(...)` + a threshold literal in `WHERE`/`QUALIFY`/join predicate | **comparison field + scorer + threshold** (reuse the Splink scorer mapping) |
| `LOWER`/`UPPER`/`TRIM`/`INITCAP`/`REGEXP_REPLACE`/`REPLACE`/`translate` wrapping a join/compare column | **standardizer / transform** on that field |
| `ORDER BY <col> DESC` inside `ROW_NUMBER()`; `FIRST_VALUE`/`LAST_VALUE`/`MAX`/`COALESCE(a, b, …)` in the survivor select | **survivorship / golden-record rule** (most-recent / source-priority / non-null-coalesce) |
| `QUALIFY ROW_NUMBER() OVER (…) = 1`; `dbt_utils.deduplicate`; `DISTINCT`; `GROUP BY` natural key with no fuzzy predicate | **exact / keep-latest matchkey** |

Extraction is **best-effort and confidence-scored**. Unrecognized constructs (arbitrary business logic, conditional CASE hierarchies, nested subqueries) are attached to the report as `couldn't_extract` findings with the model name + SQL excerpt — the human-review surface.

### 3. Emit the config

Aggregate signals across the identified ER models into ONE `GoldenMatchConfig`:
- de-duplicate blocking keys / comparison fields discovered in multiple models;
- resolve conflicts (two models fuzzy-matching the same field at different thresholds → keep the looser, warn — the `numeric_diff`-collapse precedent);
- attach standardizers as field transforms;
- attach survivorship as `golden_rules`.

The result is a validated `GoldenMatchConfig` + a `ConversionReport` (info/warning/error findings) + a `CoverageSummary`.

### 4. Verify (the load-bearing trust step)

**Reuse `splink_verify` wholesale.** The pipeline's *existing output* is the label-free ground truth:
- the old dbt ER model already produces a table — either a **surrogate-key → member mapping** (window-dedup style) or a **canonical/golden table** (master-data style);
- read it into an `id → cluster_id` map (the same shape `splink_verify._resolve_ids` + cluster maps use);
- run GM's proposed config via `dedupe_df` on the *source* rows → GM's `id → cluster_id`;
- compute pairwise agreement (`pair_set` / `pairwise_prf`) → **"reproduces N% of your existing clusters."**

This is the exact engine-vs-engine primitive `verify_against_splink` uses, with the dbt output table as the reference instead of a live Splink run. No labels required (consistent with the fact that dbt dedup, like Splink, is unsupervised). `is_faithful` at pairwise F1 ≥ 0.95, same bar.

Verify degrades gracefully: if the output table / dbt run isn't available, we emit the config + coverage without an agreement number (a warning finding), exactly as `verify_against_splink` returns `None` when splink is absent.

### 5. Report

A `DbtConversionCoverage` scorecard (analogous to `CoverageSummary`):
```
14 ER models analyzed -> 1 config: 4 blocking keys, 6 comparison fields (3 exact, 3 fuzzy),
2 survivorship rules. 3 constructs flagged for review. Reproduces 97.2% of existing clusters
(pairwise F1). Story: fuzzy-ER consolidation.
```
Plus the ranked list of `couldn't_extract` items so the human knows exactly what to check.

## Surface

- **Library:** `goldenmatch/config/from_dbt.py` — `from_dbt(manifest_path, *, catalog_path=None, output_table=None, source_table=None) -> DbtConversion`. Mirrors `SplinkConversion` (config + report + coverage + optional agreement).
- **CLI:** `goldenmatch import-dbt <manifest.json> [-o config.yaml] [--verify <output.parquet> --source <rows.parquet>]` and a one-shot `migrate-dbt` mirroring `migrate-splink` (convert → coverage → verify → optionally run).
- **Docs:** a "Migrating from hand-rolled dbt" page, mirroring "Migrating from Splink."
- Manifest surfaces (`parity/goldenmatch.yaml` cli_commands.python_only, agent-codemap, config-matrix) updated in the same PR, per the api_parity discipline.

## Dialect strategy

Compiled SQL is warehouse-specific (Snowflake `EDITDISTANCE`, BigQuery `EDIT_DISTANCE`, DuckDB `levenshtein`, Postgres `levenshtein` via `fuzzystrmatch`, Redshift, Spark). The `adapter` is in the manifest metadata, so recognizers are **dialect-registered**: a small per-adapter table of fuzzy-function names + `QUALIFY`/window syntax. MVP ships **Snowflake + BigQuery + DuckDB** (the dominant analytics-warehouse trio for dbt); others fall through to a dialect-agnostic core (window-dedup + `GROUP BY` + the ANSI string funcs) and are flagged.

## Boundaries / where it degrades (state honestly in the report)

- **Extraction is heuristic → partial coverage.** Needs review, not blind trust. The `couldn't_extract` list is a first-class output, not a footnote.
- **Survivorship & conditional business rules** are the hardest to extract faithfully (priority hierarchies, per-source overrides, CASE ladders). Recognize the common shapes (most-recent, source-priority, non-null-coalesce); flag the rest.
- **Dialect variance** — MVP is 3 warehouses; the long tail is flagged.
- **Exact-dedup sprawl** → the "accuracy" number is trivially ~1.0; the value is consolidation, and the report says so (no over-claiming an F1 win).
- **Verify needs the output table (or a dbt run).** With it, the proof is strong; without it, the config is a *suggestion* and the report says so.

## MVP scope (ship narrow, honest about the rest)

1. `from_dbt` reading `manifest.json`: identify ER models (naming + window-dedup + `GROUP BY` + `generate_surrogate_key`), extract blocking + exact/keep-latest matchkeys + the common fuzzy predicates, emit a config + `DbtConversionCoverage`.
2. `verify_against_dbt` reusing the Splink verify math against a provided output table.
3. Dialects: DuckDB + Snowflake + BigQuery.
4. Recognize ~5 idioms well (`QUALIFY ROW_NUMBER`, `dbt_utils.deduplicate`, `GROUP BY` natural key, `jaro_winkler`/`levenshtein` + threshold, `LOWER/TRIM` normalization); everything else → `couldn't_extract`.
5. `import-dbt` CLI + tests (fixture manifests, no live warehouse) + a docs page.

Same posture the Splink converter shipped with: high fidelity on the recognized set, loud + honest about the unrecognized set, verified against the user's own output.

## Testing strategy

- **Fixture manifests**: hand-authored `manifest.json` fragments for each recognized idiom per dialect (no live warehouse) — the analogue of the Splink converter's captured-SQL fixtures. Assert the extracted config + coverage.
- **Round-trip verify**: a small synthetic source + a synthetic "old output" table where the config *should* reproduce the clusters → assert `is_faithful`, and a deliberately-diverging case → assert the agreement drops and is reported.
- **`couldn't_extract` coverage**: feed a model with arbitrary business logic → assert it's flagged, not silently dropped.
- Real-manifest smoke: run against a public dbt sample project (e.g. jaffle-shop-style) to confirm it doesn't crash and reports sensibly on a non-ER project (empty/low coverage, no false ER models).

## Open questions / risks

1. **Confidence calibration** — how aggressive is ER-model identification? False positives (calling a non-ER model "ER") waste review time; false negatives miss real logic. Start conservative + report the ranked list.
2. **Multi-entity projects** — a dbt project may resolve *several* entities (customers, products, suppliers). One config vs several? MVP: emit one config per identified ER "family" (grouped by the entity's source table), not one giant config.
3. **Incremental models** — dbt incremental ER models express *merge* logic (`is_incremental()` blocks). Recognizing incremental-merge → GM's incremental/identity path is a phase-2 lift.
4. **jinja/macros beyond dbt_utils** — custom macros compile to SQL in the manifest (good), but package-specific macros (`dbt_utils.deduplicate`) need per-macro recognizers. Cover the popular packages; flag custom ones.
5. **Verify without an output table** — is a config-only suggestion valuable enough on its own, or is the agreement number the whole point? (Lean: the number is the point; ship verify in the MVP, not phase 2.)

## Relationship to the two existing dbt/Splink surfaces

- **Complements the GM-in-dbt package** (`packages/dbt/goldensuite/`): that's for *adding* GM to a dbt pipeline going forward; this converter is for *replacing* hand-rolled ER a team already has. A natural pairing — the converter's emitted config can be dropped straight into a `{{ goldenmatch_dedupe(...) }}` model.
- **Reuses the Splink converter's machinery** (`ConversionReport`, `CoverageSummary`, `RecognizedLevel`→`RecognizedSignal`, the scorer/threshold mapping) and the verify primitive (`splink_verify.pair_set`/`pairwise_prf`) — so the net-new surface is the manifest reader + ER-model identifier + SQL-idiom recognizers, not a second converter framework.

## Phasing

- **Phase 1 (MVP):** manifest reader + ER identification + exact/common-fuzzy extraction + verify + report + CLI + docs (DuckDB/Snowflake/BigQuery).
- **Phase 2:** survivorship-rule extraction depth, more dialects, incremental-merge recognition, multi-entity families.
- **Phase 3:** an LLM-assisted extraction fallback for the `couldn't_extract` tail (opt-in, same posture as the auto-config LLM tier) — propose a config for a model the heuristics couldn't parse, still gated by the verify agreement number so it can't hallucinate unchecked.
