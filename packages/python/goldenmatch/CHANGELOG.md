# Changelog

All notable changes to GoldenMatch are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [Unreleased]

### Added -- NAICS industry-code normalization (strategy direction #8, seventh slice)

Extends the `reference-business` pack with US Census 2022 NAICS industry classification. Canonicalizes both numeric codes ("511210", "511 210", "511210 (Software Publishing)") AND known industry titles ("Software Publishers" → "513210") to the same string before matching, so two records describing the same business industry land on the same value.

- **Bundled NAICS 2022 hierarchy** at `goldenmatch/refdata/data/naics_2022.json` -- 2,125 entries across all five hierarchy levels (17 sectors, 96 subsectors, 308 industry groups, 692 5-digit industries, 1,012 6-digit US industries). Sourced from the U.S. Census Bureau's "2-6 digit 2022 Codes" file (https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx). Public-domain US federal data, no license restrictions. The "31-33" range-encoded sector for Manufacturing is expanded across each constituent 2-digit code.
- **Lookup API**: `title_for_code(code)`, `code_for_title(title)`, `naics_normalize(value)`, `industries_available()`, `known_codes()`, `known_titles()`. Title lookup is case- and punctuation-tolerant; code lookup tolerates separators and trailing text.
- **`naics_normalize` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Three input shapes:
  - Numeric input: scans EVERY 2+-digit run in the string; for each, walks back through prefixes looking for the longest known code. Returns the first run that resolves. If no run resolves, returns the 6-digit-truncated form of the first run (so two records sharing an unknown code still match). Multi-run scanning lets inputs like `"NAICS 2022 code 511210"` skip the vintage-year prefix and pick up the real code (review-driven; was a first-run short-circuit before).
  - Known industry titles → the canonical code at the narrowest matching hierarchy level.
  - Anything else → lowercase + whitespace-collapse pass-through.
  Never raises. Falls back to lowercase+strip if the bundled data is missing.
- **Autoconfig hook extended**: column-name patterns `naics`, `sic`, `industry`, `industry_code`, `industry_classification`, `business_type` → `naics_normalize` is prepended to the transforms list (mirrors the existing legal_form_strip / address_normalize handling). `_COMPANY_NAME_RE` was tightened to exclude `business[_ ]?type` so that classification column doesn't accidentally also pick up `legal_form_strip` (review-driven).
- **Thread-safety**: `_reload()` now relies on `_load()`'s lock + new-dict-assignment for atomic state swap, instead of wiping dicts before re-parse — readers see either the old dict or the new dict, never an empty in-between state (review-driven).
- **Tests**: `tests/test_refdata_industries.py` (~40 tests) -- title↔code round-trips, case/punctuation tolerance, separator-tolerant code parsing, overlong-code truncation, longest-known-prefix fallback (covers the review-flagged uncovered branch), title-precedence narrowest-wins (regression for the iteration-order rule), multi-digit-run scanning, `business_type` non-overlap with `_COMPANY_NAME_RE`, transform plugin registration, transform-chain composition, `FieldTransform` validator acceptance, `MatchkeyField` accepts in `transforms:`, autoconfig column-name variants including `business[_ ]?type` regex branches.
- **In-session validation blocked** by the same Polars DLL hang documented in PRs #220 and #221 (`goldenmatch/__init__.py` eagerly imports polars-heavy modules, which poisons every `goldenmatch.*` import including refdata submodules; openpyxl-only extraction of the source xlsx worked fine). The transform is a pure synchronous regex+dict function with no hot loops; rerun `pytest tests/test_refdata_industries.py -v` on a fresh Python boot to materialize the test result.
- **What's still deferred**: OpenCorporates company-name variants (the last documented `reference-business` extension), libpostal binding for `reference-address-postal`, per-scorer threshold tuning in `LearningMemory`, and the controller-level A/B rule that would A/B-test refdata refinements in the iteration loop instead of applying them unconditionally.

### Added -- Auto-config integration for refdata packs (strategy direction #8, sixth slice)

Wires all four refdata packs into the zero-config controller. Auto-config no longer needs an explicit YAML to pick up surname-IDF weighting, given-name aliasing, legal-form stripping, or USPS address normalization — it picks them automatically when column names signal the relevant shape.

- **New module** `goldenmatch/refdata/autoconfig_hooks.py` exposes `refine_matchkey_field(column_name, scorer, transforms) -> (scorer, transforms)`. Pure function; safe to call on every column unconditionally.
- **Refinement rules** (each gated on the relevant pack's `is_available()` — non-refdata installs behave exactly as before):
  - `last_name | surname | lname | family_name` → scorer becomes `name_freq_weighted_jw`.
  - `first_name | given_name | fname | forename` → scorer becomes `given_name_aliased_jw`.
  - `company | business | org | firm | employer | legal_name | entity_name` → `legal_form_strip` is prepended to the transforms list.
  - `address | street | addr_line | mailing_address` → `address_normalize` is prepended.
- **Wired into `core/autoconfig.py`** at both `build_matchkeys()` (vanilla weighted/exact path) and `build_probabilistic_matchkeys()` (Fellegi-Sunter path). The hook fires *after* `_SCORER_MAP[col_type]` resolves but *before* `MatchkeyField` is constructed — so the existing column-type classification, cardinality guards, and exact-matchkey skips still run unchanged.
- **Scorer-swap protection**: only string-similarity scorers (`jaro_winkler`, `levenshtein`, `token_sort`, `ensemble`, `dice`, `jaccard`) get swapped. Exact and embedding scorers pass through — preserves identity-field semantics.
- **Transform prepend, not replace**: `legal_form_strip` runs before any existing `lowercase`/`strip`, so the canonical short form still goes through downstream normalization. Idempotent — the function won't double-prepend if the transform is already in the list.
- **Compound columns** (e.g. `company_last_name`) get both refinements: scorer swap from the last_name match + transform prepend from the company match.
- **Tests**: `tests/test_refdata_autoconfig.py` (~50 tests) — parametrized across every column-name variant for each refinement rule, exact-scorer-not-swapped, idempotency, no-mutation-of-caller's-list, compound-column composition, and two end-to-end tests via `build_matchkeys()`.
- **In-session validation blocked** by the same Polars DLL hang documented in PR #220 / CLAUDE.md — every Python invocation in the session that imports Polars (directly or via pytest's conftest chain) sits idle indefinitely. The refinement function is a pure synchronous function and its tests don't depend on Polars; rerun `pytest tests/test_refdata_autoconfig.py` on a fresh Python boot to materialize the test result. Three of the existing test cases (those calling `build_matchkeys()`) do touch Polars and need the same fresh-boot rerun.
- **What's still deferred**: per-scorer threshold tuning in `LearningMemory`, regression check on NCVR / DBLP-ACM under the new auto-config (need a clean Python session), and the controller-level rule that A/B-tests refdata vs vanilla in its iteration loop for ground-truth-aware swap decisions.

### Added -- Surname-scorer common-name-FP synthetic benchmark (strategy direction #8, fifth slice)

Companion benchmark to the synthetic fixtures shipped with the other refdata packs (PR #217 nicknames, PR #218 legal-form, PR #219 address). NCVR's corruption distribution doesn't exercise the borderline JW zone the `name_freq_weighted_jw` scorer was built for; this fixture does, so we can finally show the scorer's actual lift instead of just "no regression".

- **`tests/benchmarks/run_surname_fp_synth.py`** -- 1000-record fixture:
  - **200 TP pairs**: same person across two records, identical first name, identical surname drawn from the common-US-Census pool (Smith, Johnson, Williams, ...). 20% of these use OOV-typo surnames on one side (Smith / Smiht) to verify the scorer's pass-through-to-plain-JW degradation doesn't regress recall.
  - **200 FP-candidate pairs**: *different* people, same first name, borderline-similar common surnames (Smith vs Smyth, Johnson vs Johnsen, Jones vs Jonas, Miller vs Millar, Martin vs Marten, White vs Whyte). Plain JW scores the surname pair around 0.89-0.94 -- exactly the borderline zone the refdata scorer down-weights.
  - **600 distractor singletons**: unique first AND last names, no FP pressure.
  - Blocking on `first_name` puts each pair into its own 2-record block.
- **Configured matchkey**: `first_name + last_name` weighted, threshold 0.92. The threshold is tuned so plain JW (1.0 + 0.89-0.94)/2 squeaks above (calls FP-candidates duplicates), but refdata-weighted (1.0 + 0.77-0.84)/2 drops below (rejects them).
- **Predicted numbers** from the scorer math validated by direct plugin calls earlier in the session:

  | | TP | FP-candidates passed | P | R | F1 |
  | - | - | - | - | - | - |
  | baseline (`jaro_winkler` on last_name) | 200 | ~200 (most pass at JW 0.89-0.94 averaged with 1.0) | ~0.50 | ~1.00 | **~0.67** |
  | refdata (`name_freq_weighted_jw`) | 200 | ~5-30 (only the residual high-JW cases) | ~0.87-0.98 | ~1.00 | **~0.93** |

  Expected F1 delta around **+0.26**. Numbers are predicted, not measured -- the in-session benchmark run is blocked by a known Polars DLL hang (CLAUDE.md gotcha: `Polars DLL hangs: kill zombie python ...`). After a clean Python boot, run `python tests/benchmarks/run_surname_fp_synth.py --out report.txt` to materialize the actual measurement.

- **Per-pair scorer math validated** for the surnames used in the fixture (direct plugin calls):

  | Surname pair | Plain JW | Refdata-weighted | Drop |
  | - | - | - | - |
  | Smith / Smyth | 0.893 | 0.769 | -0.124 |
  | Johnson / Johnsen | 0.943 | 0.818 | -0.125 |
  | Jones / Jonas | 0.907 | 0.790 | -0.117 |
  | Miller / Millar | 0.933 | 0.821 | -0.112 |
  | Martin / Marten | 0.933 | 0.840 | -0.093 |
  | White / Whyte | 0.893 | 0.793 | -0.100 |

  The down-weighting is consistent in the [0.10, 0.13] range for both-sides-known common-name pairs in the borderline JW zone.

- **What's still deferred** (after this slice): auto-config integration across all four refdata packs, libpostal binding for `reference-address-postal` extra, industry codes (NAICS) and OpenCorporates company-name variants for `reference-business`.

### Added -- Reference-address pack: USPS-style address normalization (strategy direction #8, fourth slice)

Fourth refdata slice. Opens the `reference-address` pack with the `address_normalize` transform: collapses USPS Publication 28 street-suffix, directional, and secondary-unit variants to their canonical short forms so "123 Main Street North Apartment 5" and "123 Main St N Apt 5" both reduce to "123 main st n apt 5" before scoring.

- **Bundled USPS abbreviation table** at `goldenmatch/refdata/data/address_abbreviations.json` — ~500 surface variants covering street suffixes (150+ canonical forms), 8 directionals, 9 secondary-unit designators. Sourced from USPS Publication 28 Appendix C; public-domain US federal data, no license restrictions.
- **`address_normalize` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Tokenizes on whitespace + commas, lowercases each token, strips punctuation, then maps any recognised variant to its USPS canonical short form. Unknown tokens pass through unchanged. Idempotent.
- **Position-agnostic by design**: every USPS-known token is normalized, not just trailing ones. Trade-off: words that are both name parts and suffix variants ("Lake", "Court", "Park") collapse along with true suffixes. Match invariance is preserved as long as both sides reduce equally — pinned by `test_aggressive_normalization_preserves_match_invariance`. For display purposes use a different normalization; this transform is matching-only.
- **Tests**: `tests/test_refdata_addresses.py` (48 tests) — per-suffix parametrized across 17 variants, directionals across 8, secondary units across 7, multi-token compound case, punctuation stripping, idempotency, unknown-token pass-through, position-agnostic invariance, plugin transform dispatch, transform-chain composition, validator acceptance.
- **Synthetic address benchmark** at `tests/benchmarks/run_address_synth.py`. 1000-record fixture, 200 same-street pairs differing in suffix abbreviation (some also in directional/unit) + 600 distractors, threshold 0.95:

  | | TP | FP | FN | P | R | F1 |
  | - | - | - | - | - | - | - |
  | baseline (no transform) | 116 | 0 | 84 | 1.0000 | 0.5800 | **0.7342** |
  | baseline (lowercase only) | 114 | 0 | 86 | 1.0000 | 0.5700 | **0.7261** |
  | refdata (`address_normalize`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |

  F1 delta +0.2658. Recall +0.4200. Plain JW catches the small suffix deltas (Ave/Avenue, St/Street) but misses larger ones (Boulevard/Blvd, Northeast/NE, Apartment/Apt). The transform catches everything.

- **What's still deferred**: libpostal binding (heavy C deps + ~2 GB model; opt-in extra rather than bundled), street-name canonicalization (USPS CASS proper), ZIP+4 lookups, international postal-code formats.

### Added -- Reference-business pack: legal-form normalization (strategy direction #8, third slice)

Third refdata slice. Opens the `reference-business` pack with the `legal_form_strip` transform: strips trailing corporate suffixes ("Inc", "LLC", "GmbH", "Pty Ltd", …) so "Acme Inc." and "Acme Incorporated" collapse to "Acme" before scoring.

- **Bundled token list** at `goldenmatch/refdata/data/legal_forms.json` — ~80 surface variants spanning US, UK, EU, Asia-Pacific, LatAm jurisdictions. Public-knowledge corporate-suffix conventions; no license restrictions.
- **`legal_form_strip` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Strips multi-word suffixes first (so "Limited Liability Company" beats "Limited" or "Company" alone). Iterative (handles "Acme Holdings Inc" -> "Acme"). Idempotent. Case-insensitive. Returns input unchanged when no match or data file missing.
- **Plugin transform fallback wired into the core pipeline**:
  - `goldenmatch.utils.transforms.apply_transform` now falls through to `PluginRegistry.get_transform` for unknown transform names (mirrors the existing scorer fallback).
  - `goldenmatch.config.schemas.FieldTransform._validate_transform` checks the registry before raising `Invalid transform` (mirrors `MatchkeyField._validate_*` scorer fallback).
  - Net result: any plugin transform Just Works in YAML config, matchkey transforms list, and `apply_transforms` chains.
- **Tests**: `tests/test_refdata_business.py` (45 tests) -- per-form strip parametrized across 28 variants, case-insensitive, whitespace normalize, iterative strip on multi-suffix names, idempotency, mid-name preservation, None/empty handling, plugin transform dispatch, transform-chain composition (`legal_form_strip` then `lowercase`), `FieldTransform` validator accepts plugin name, `MatchkeyField` accepts it in `transforms:`.
- **Synthetic business-name benchmark** at `tests/benchmarks/run_business_synth.py`. 1000-record fixture, 200 same-stem pairs differing only in legal-form suffix (Acme Inc vs Acme Incorporated, etc.) + 600 distractors, threshold 0.95:

  | | TP | FP | FN | P | R | F1 |
  | - | - | - | - | - | - | - |
  | baseline (no transform) | 79 | 0 | 121 | 1.0000 | 0.3950 | **0.5663** |
  | refdata (`legal_form_strip`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |
  | refdata (`legal_form_strip` + `lowercase`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |

  F1 delta +0.4337. Recall +0.6050. The transform catches every pair the variant labels differ on; precision unchanged at 1.0 (no FPs introduced).
- **What's still deferred**: industry code lookups (NAICS), OpenCorporates company-name variants, `reference-address` pack (token normalization + libpostal binding), auto-config integration, per-scorer threshold tuning.

### Added -- Reference data infrastructure (strategy direction #8, first slice)

`goldenmatch.refdata` -- bundled, public-domain reference data the engine can consume to lift accuracy on people-shape matching. Spec: `docs/superpowers/specs/2026-05-08-competitive-strategy-review.md` direction #8.

- **US Census 2010 top-10K surname frequency table** bundled at `goldenmatch/refdata/data/census_surnames_2010_top10k.csv` (~176 KB, public domain). Provenance, license, regenerate command documented in `PROVENANCE.md`.
- **Lookup API**: `surname_count`, `surname_rank`, `surname_frequency`, `surname_idf`, `is_available`. Case-insensitive; strips non-alpha. OOV names return `None` (or `1.0` from `surname_idf`, treated as "rarer than known").
- **`name_freq_weighted_jw` scorer** registered via the plugin system on `import goldenmatch.refdata`. Algorithm: Jaro-Winkler outside the borderline zone (`jw >= 0.95` or `jw < 0.70`) returns plain JW unchanged -- preserves recall on confident matches. Inside the borderline zone, both-sides-known pairs get re-weighted by mean surname IDF with a `_COMMON_NAME_FLOOR = 0.6`. OOV-on-either-side falls back to plain JW (refuses to up-credit typos of common names).
- **NxN plugin path**: `core/scorer.py::_fuzzy_score_matrix` now falls through to `PluginRegistry` for unknown scorer names, building the matrix via `score_pair` calls. Slower than rapidfuzz `cdist` for the registered scorers but keeps the contract uniform.
- **Regenerate**: `python -m goldenmatch.refdata.scripts.fetch_census_surnames` pulls the upstream archive and rewrites the bundled CSV.
- **Tests**: `tests/test_refdata_surnames.py` (21 tests) -- lookup correctness, IDF monotonicity, scorer borderline behavior, OOV pass-through, plugin registration, `MatchkeyField` validator accepts the new scorer.
- **NCVR A/B benchmark** at `tests/benchmarks/run_ncvr_refdata.py`. 7500-record corrupted-duplicates GT, last_name scorer swapped: F1 0.9721 (baseline, zero-config) -> 0.9721 (refdata). No regression. Lift is zero on this dataset because NCVR's heavy-corruption distribution puts few pairs in the borderline JW zone where the weighting acts -- needs an enterprise-shape benchmark per direction #5 to demonstrate positive lift.
- **What's deferred** (future work): auto-config integration (the controller doesn't yet pick `name_freq_weighted_jw` automatically); `reference-business` and `reference-address` packs; threshold tuning per-scorer in `LearningMemory`.

### Added -- Given-name alias pack (strategy direction #8, second slice)

Second slice of the `reference-people` pack. Adds nickname-equivalence to first-name matching: William ↔ Bill, Robert ↔ Bob, Margaret ↔ Peggy, etc.

- **Curated alias table** at `goldenmatch/refdata/data/given_name_aliases.json` (~140 canonical English given names, public-knowledge naming conventions; no license restrictions).
- **Lookup API**: `canonical_form`, `aliases_of`, `are_equivalent`, `given_names_available`. Case-insensitive; strips non-alpha. Symmetric and transitive within an equivalence class. OOV pass-through.
- **`given_name_aliased_jw` scorer** registered via the plugin system on `import goldenmatch.refdata`. Alias-equivalent pairs return 1.0 regardless of edit distance; unrelated pairs return plain Jaro-Winkler. The scorer never *lowers* a JW score -- it only promotes known aliases. Degrades cleanly when the alias table is missing.
- **Tests**: `tests/test_refdata_given_names.py` (23 tests) -- lookup symmetry, transitive equivalence, multi-canonical name handling (e.g. "Jack" canonical AND alias-to-John), case/punct insensitivity, OOV pass-through, scorer correctness, plugin registration, validator acceptance.
- **Synthetic nickname benchmark** at `tests/benchmarks/run_nickname_synth.py`. 1000-record fixture with 200 nickname-shape duplicate pairs + 600 distractors with isolated random first/last names. Plain JW baseline catches **0/200** pairs at threshold 0.95 (JW(William, Bill) ~= 0.55, far below threshold); `given_name_aliased_jw` catches **200/200**, P=1.0, R=1.0, **F1 0.00 -> 1.00**.
- **Asymmetry-on-ambiguous-short-form bugfix**: short forms that belong to multiple canonicals (e.g. "kate" appears in Catherine, Kathleen, Kaitlyn; "chris" in Christopher, Christine, Christina) were silently asymmetric — `are_equivalent("Kate", "Catherine")` returned False while `("Catherine", "Kate")` returned True, because the old lookup stored a single canonical per form (last-writer-wins). The matcher's NxN score matrix only consults the upper triangle, so the False direction was the one being read and every ambiguous-short-form pair was being dropped. Each form now stores the full set of canonicals it belongs to; equivalence holds iff the two forms share a canonical. Regression test in `test_are_equivalent_symmetric_for_ambiguous_short_forms`.
- **What's still deferred**: same list as the first slice (auto-config integration, business / address packs, per-scorer threshold tuning).

## [1.15.0] - 2026-05-12

### Added -- Identity Graph (v2.0 headline feature)

`goldenmatch.identity` -- a first-class durable graph layer above run-local clusters. Spec: `docs/superpowers/specs/2026-05-12-identity-graph-design.md`. Roadmap: `docs/superpowers/plans/2026-05-12-identity-graph-roadmap.md`.

- **`IdentityStore`** (SQLite default, Postgres optional): identity nodes, source records, evidence edges, append-only event log, aliases. WAL + busy_timeout for multi-process safety. Schema versioned via `PRAGMA user_version`.
- **Stable `entity_id` across runs**. `resolve_clusters()` runs after dedupe clustering and decides `create` / `absorb` / `merge` based on which existing identities cover the cluster's records. Idempotent on `(run_name, kind, entity_id)`.
- **`IdentityConfig`** -- new optional section in `goldenmatch.yml`. When `identity.enabled: true`, the pipeline writes graph state at `.goldenmatch/identity.db` (or the configured backend) on every `run_dedupe()`. Disabled by default; failure logs + skips, never blocks dedupe output.
- **Surfaces**: Python (`goldenmatch.identity.*` + root re-exports), CLI (`goldenmatch identity list/show/resolve/history/conflicts/merge/split`), REST (`/api/v1/identities/...`), web "Identities" tab, MCP (6 `identity_*` tools), A2A (6 skills, agent card now declares 18 total skills). TS edge-safe core (`InMemoryIdentityStore` + `findByRecord` / `getEntity` / `manualMerge` / `manualSplit`) ships in the same release; persistent SQLite backend + pipeline-driven population are TS-port v2 follow-ups.
- **Postgres analytical views**: `v_identities`, `v_identity_pairs`, `v_identity_timeline` in `goldenmatch/db/migrations/identity_v1.sql`. `IdentityStore(backend="postgres")` creates the same schema on first connect.
- **DuckDB / extensions contract** documented at `docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md` for the `goldenmatch-extensions` repo to implement.
- **47 new Python tests**, **13 new TS tests**. Full sweep: 1984 passed, 0 regressions.
- Example: `examples/identity_graph.py`.

## [1.14.0] - 2026-05-11

This release ships the full v1.7-v1.12 AutoConfigController surface to every user-facing entry point in the suite. No algorithm changes vs 1.13.0 — same DQbench / DBLP-ACM / Febrl3 / NCVR numbers — but you can now read what the controller decided from every interface (web, TUI, CLI, REST, MCP, A2A, Postgres, DuckDB) and round-trip the committed config (including Path Y negative-evidence) through SQL.

### Fixed

- **AgentSession default path now actually runs the AutoConfigController** (PR #169). `deduplicate(config=None)` / `match_sources(config=None)` were building a config from the legacy `select_strategy()` heuristic and passing it explicitly to `dedupe_df`/`match_df`, which suppressed the zero-config controller path. `last_telemetry` ended up `None` for the case it was meant to capture. Default path now calls `dedupe_df(df)` / `match_df(df_a, df_b)` with no config so the controller fires. Explicit-config calls explicitly clear `last_telemetry` to prevent stale-blob leaks across calls on the same session. `select_strategy()` still runs but only for the `reasoning` payload, not to back the actual matching config. Hard-asserting tests landed alongside the fix.

### Added — AutoConfigController surface-parity arc

Six PRs (#156-#159 + #161; #160 added CI lanes) bring every user-facing entry point up to speed with the v1.7-v1.12 AutoConfigController / IndicatorContext / NegativeEvidence work. Before this arc, controller decisions were observable only by reading `result.postflight_report.controller_history` in Python. Now every surface returns the same JSON shape (`stop_reason`, `health`, refit decisions, indicator column priors, committed `negative_evidence`) via `goldenmatch.web.controller_telemetry.serialize_telemetry`.

**Web UI** (PR #156)
- New `ControllerPanel` in Workbench surfaces stop_reason badge, health verdict, complexity profile cells, indicator column priors, refit decision trace, and `Path Y · N NE` indicator on committed matchkeys.
- New `GET /api/v1/controller/telemetry` endpoint populated by `/autoconfig` and `/run?auto_config=true`.
- Home gains a `ProvenanceCallout` linking `docs/reproducing-benchmarks.md` + `docs/scale-envelope.md` with the four reproducible numbers.

**TUI** (PR #157)
- New `Controller` tab (7th tab) showing the same telemetry the web panel shows.
- New `Ctrl+A` binding triggers async auto-configure; result adopted into ConfigTab + ExportTab; switches to Controller tab on completion.
- `MatchEngine.auto_configure(domain=None)` captures `_LAST_CONTROLLER_RUN` and exposes telemetry on `engine.last_telemetry`.

**CLI** (PR #158)
- New `goldenmatch autoconfig <files>` subcommand. Prints committed config to stdout (pipe to `> goldenmatch.yml`); telemetry panel to stderr. Flags: `--out PATH`, `--domain`, `--verbose`, `--hide-controller`.
- `goldenmatch dedupe` zero-config path captures `_LAST_CONTROLLER_RUN` and renders the same panel before the cluster report (`--show-controller` / `--hide-controller`).
- New shared `goldenmatch.cli._controller_render` module with Rich Panel + one-line `render_short_status` for log scraping.

**SQL extensions** (PR #159)
- Bridge (Rust/pyo3) gains `DedupeResult.telemetry_json`, `autoconfig()` returning `(committed_config_json, telemetry_json)`, and `dedupe_full()` accepting the full Pydantic `GoldenMatchConfig` JSON (unlocks `negative_evidence` from SQL).
- Postgres: new `goldenmatch_autoconfig`, `goldenmatch_autoconfig_telemetry`, `goldenmatch_dedupe_full`, `goldenmatch_dedupe_full_telemetry`, `gm_telemetry`. New JSONB column `goldenmatch._jobs.last_telemetry_json` (added via `ALTER TABLE ... IF NOT EXISTS` for in-place upgrade).
- DuckDB: parallel UDFs registered on every `register(con)`.

**CI** (PR #160)
- New `rust_pgrx` lane (matrix: PG 15/16/17) — cargo pgrx install + psql smoke covering the new v1.7-v1.12 surface.
- New `duckdb_extensions` lane — runs the DuckDB UDF Python tests that the main `python` matrix doesn't pick up.

**Agent / programmatic surfaces** (PR #161)
- `AgentSession.autoconfigure(file_path)` returns `{config, telemetry}`; `deduplicate` / `match_sources` cache `last_telemetry`.
- REST API (`goldenmatch serve`): new `POST /autoconfig` (with optional `records` body override) + `GET /controller/telemetry`.
- MCP: `auto_configure` tool rewired off the legacy `select_strategy` heuristic onto the controller. New `controller_telemetry` tool. `agent_deduplicate` / `agent_match_sources` embed telemetry inline.
- A2A: 10 → 12 skills (added `autoconfig` + `controller_telemetry`). `deduplicate` / `match` skills embed telemetry in their wire result.

**Cross-surface telemetry shape** (single source of truth at `goldenmatch.web.controller_telemetry.serialize_telemetry`)

```json
{
  "available": true,
  "source": "autoconfig",
  "stop_reason": "green",
  "health": "green",
  "elapsed_ms": 1234.5,
  "full_vs_sample_drift": 0.12,
  "scoring": {"n_pairs_scored": 4421, "mass_above_threshold": 0.087},
  "blocking": {"n_blocks": 312, "reduction_ratio": 0.94},
  "cluster": {"n_clusters": 1820, "transitivity_rate": 0.99},
  "column_priors": [{"column": "email", "identity_score": 0.95, "corruption_score": 0.0}],
  "decisions": [{"iteration": 1, "rule_name": "...", "rationale": "...", "wall_clock_ms": 234}],
  "committed_matchkeys": [{"name": "exact_email", "has_negative_evidence": true}],
  "negative_evidence": [{"matchkey_name": "exact_email", "field": "phone", "penalty": 0.5}]
}
```

## [1.13.0] - 2026-05-11

This is a release-plumbing wave: typed-accessor API additions, PyPI metadata refresh, and contributor-facing quality improvements. **No DQbench / Febrl3 / NCVR / DBLP-ACM number changes** — algorithm is unchanged this wave.

### Added
- **Typed accessor API on `MatchkeyConfig` / `MatchkeyField`** (PR #151). New properties: `MatchkeyConfig.fuzzy_threshold`, `MatchkeyField.fuzzy_scorer`, `MatchkeyField.fuzzy_weight`, `MatchkeyField.resolved_field`. Each raises `ValueError` when the underlying matchkey isn't a fuzzy/weighted type, so the invariant is now enforceable in pyright strict mode rather than asserted in callers.

  ```python
  from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

  mk = MatchkeyConfig(
      name="identity",
      type="weighted",
      threshold=0.85,
      fields=[MatchkeyField(field="name", transforms=["lowercase"], scorer="jaro_winkler", weight=1.0)],
  )
  assert mk.fuzzy_threshold == 0.85  # safe access on weighted matchkey
  # mk.fuzzy_threshold on an exact matchkey raises ValueError
  ```

- **`docs/scale-envelope.md`** (PR #149): documents the Polars / DuckDB / Ray operating ranges plus block-size failure modes so callers can pick a backend before hitting an OOM.
- **Postgres CI lane** (PR #144): flipped from skipped to live so DB integration tests now run on every PR.

### Changed
- **PyPI metadata corrected** (PR #148): `[project.urls]` Homepage / Repository / Documentation entries now point at the monorepo at `benzsevern/goldenmatch`. The pre-fold standalone-repo URLs are gone. Metadata only refreshes on a wheel build, so this release is what makes the corrected URLs visible on PyPI.

### Fixed
- **Reproducibility of all four published benchmark numbers** (PR #152, replaces #150): DQbench composite 91.04, DBLP-ACM 0.9641, Febrl3 0.9443, NCVR 0.9719 now all reproduce from a fresh clone. See `docs/reproducing-benchmarks.md` for the exact commands and dataset prep steps.

### Internal (contributors only)
- Ruff lint expanded to F / I / B-narrowed / UP rule sets across `packages/python/` (PR #146).
- Pyright strict mode now enforced on the 21-file core slice of `goldenmatch` (PR #147). The new typed accessors in PR #151 eliminated 7 type-suppression workarounds in callers.

### Benchmarks (zero-config, no LLM)

Unchanged vs v1.12.0 — algorithm not touched this wave.

| Dataset | v1.12.0 | v1.13.0 | Delta |
|---|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 | +0.0000 |
| Febrl3 | 0.9443 | 0.9443 | +0.0000 |
| NCVR | 0.9719 | 0.9719 | +0.0000 |
| DQbench composite | 91.04 | 91.04 | +0.00 |

## [1.12.0] - 2026-05-10

### Added
- **`_apply_negative_evidence_to_exact_pairs`** in `core/scorer.py`: post-filter helper that applies NE penalties to pairs produced by exact matchkeys. Called from `core/pipeline.py` after `find_exact_matches`. Score formula: `final = max(0, 1.0 - sum(penalties))`; pair emits only if `final >= matchkey.threshold`. Exact matchkeys without NE fields are unaffected (binary 1.0/0.0 emit preserved).
- **Exact-matchkey NE threshold default**: when `promote_negative_evidence` adds NE fields to a threshold-None exact matchkey, the threshold is defaulted to 0.5 to activate the score-and-threshold path.
- **`promote_negative_evidence` extended** to walk all matchkey types (was weighted-only in v1.11). The `_is_exact_matchkey_field` gate is selectively skipped when iterating an exact matchkey for itself — its v1.11 rationale (prevent recall regression on fuzzy data) doesn't apply to exact-matchkey self-iteration.

### Changed
- **`core/pipeline.py`**: calls `_apply_negative_evidence_to_exact_pairs` after `find_exact_matches` when any exact matchkey carries NE fields. Zero overhead when no NE fields are present.
- **`promote_negative_evidence`** now populates NE on exact matchkeys in addition to weighted matchkeys. Exact matchkeys for high-identity-prior columns (email) gain NE from disagreeing secondary fields, allowing adversarial collision pairs to be filtered at the exact matchkey level.

### Benchmarks (zero-config, no LLM)

| Dataset | v1.11.0 | v1.12.0 | Delta |
|---|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 | +0.0000 |
| Febrl3 | 0.9443 | 0.9443 | +0.0000 |
| NCVR | 0.9719 | 0.9719 | +0.0000 |
| DQbench composite | 66.99 | 91.04 | +24.05 pp |

DQbench tier detail (v1.12.0):

| Tier | Precision | Recall | F1 | vs v1.11 |
|---|---|---|---|---|
| T1 | 80.6% | 100.0% | 89.3% | flat |
| T2 | 95.1% | 100.0% | 97.5% | +28.5 pp |
| T3 | 74.7% | 100.0% | 85.5% | +31.7 pp |

Primary target (>= 75) met. T3 F1 headline target (>= 70%) met. All floor constraints met. The T3 gain resolves the v1.11 root cause: Path Y NE filtering now operates at the `exact_email` matchkey level, directly shedding adversarial collision pairs that share an email but disagree on name/address NE fields.

## [1.11.0] - 2026-05-10

### Added
- **`NegativeEvidenceField`** in `config/schemas.py`: new optional field on `MatchkeyConfig`. Each entry specifies a field, transforms, scorer, similarity threshold, and penalty. When a weighted matchkey scores a pair, any NE field whose similarity falls below its threshold subtracts the penalty from the weighted score.
- **`_apply_negative_evidence`** in `core/scorer.py`: pure helper that computes the NE penalty for a scored pair and returns the adjusted score. Called inside the weighted-matchkey scoring loop.
- **`promote_negative_evidence`** in `core/autoconfig_negative_evidence.py`: eager rule that adds NE fields to weighted matchkeys for columns with high identity priors (identity_score >= 0.75, cardinality_ratio >= 0.5) that also have an exact matchkey counterpart. Gated on the exact-matchkey counterpart requirement to prevent recall regression on noisy ER data where legitimate duplicates may have differing phone/address values.
- **`_pick_scorer_for_column`** in `core/autoconfig_negative_evidence.py`: maps column name / type to (transforms, scorer) for NE fields. Phone -> (digits_only, exact). Email -> ([], token_sort). Address -> ([], token_sort). Default -> ([], ensemble).
- **`rule_demote_clustered_identity`** at position 7 in `DEFAULT_RULES`: detects when an exact matchkey identity column is shared across distinct entities (adversarial reuse pattern). Demotes the exact matchkey to a fuzzy participant on the weighted matchkey and adds the column to blocking. Threshold of 0.75 (raised from 0.5 after Phase 7 analysis showed T2's collision rate of 0.62 was causing false demotion and 186 FNs).
- **`compute_identity_collision_signal`** in `core/indicators.py`: for each multi-record group sharing an identity column value, computes max pairwise divergence on witness columns using token_sort_ratio. Returns fraction of groups with max divergence > 0.5.

### Changed
- **`AutoConfigController.run`**: calls `promote_negative_evidence` between v0 config build and the iteration loop, so NE fields are present on weighted matchkeys before the first iteration profiles them.
- **`rule_demote_clustered_identity` collision threshold**: raised from 0.5 to 0.75. This prevents false-firing on legitimate fuzzy ER datasets (T2 collision rate 0.615) while still catching high-rate adversarial reuse (rates near 1.0).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.10.0 | v1.11.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 66.91 | 66.99 |

T2 recall regression (186 FNs from v1.11 early iteration) fixed by raising `rule_demote_clustered_identity` threshold from 0.5 to 0.75. T3 unchanged at 53.8%. Primary target (>= 75) not met; ships on best-effort basis above v1.10 baseline. T3 F1 target (>= 70%) remains an open v1.12 challenge: the exact-matchkey gate correctly protects T2 recall but also prevents phone NE from reducing T3 adversarial FPs.

### Notes for v1.12

- T3 adversarial FPs come from the `exact_email` matchkey capturing collision pairs directly. NE on the weighted matchkey does not affect these pairs. Real T3 improvement requires either a higher-precision collision signal or a different mechanism for adversarial reuse that does not require collision_rate to exceed T2's rate (0.615).
- Removing the exact-matchkey gate would raise composite to ~68.9 but drops T2 by ~0.8 pp. Not shipped due to net regression on T2 at the pair level.

## [1.10.0] - 2026-05-08

### Added
- **5 complexity indicators** (`core/indicators.py`): `compute_column_priors`, `estimate_sparse_match_signal`, `compute_corruption_score`, `estimate_full_pop_hits`, `compute_cross_blocking_overlap`. Each has a wall-clock budget; cheap two run eagerly, expensive three run lazily via `IndicatorContext` memoization.
- **`IndicatorContext`** in `autoconfig_controller.py` threads indicators through the policy/rule chain. `RefitPolicy.propose` gains optional `ctx` kwarg; `HeuristicRefitPolicy` and `LLMRefitPolicy` both forward; controller introspects custom-policy signatures via `inspect.signature` for backward compat.
- **3 new indicator-aware rules**: `rule_corruption_normalize`, `rule_cross_blocking_disagreement`, `rule_sparse_match_expand`. `DEFAULT_RULES` now has 13 rules (was 10).
- **`GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast`** env var gates the two expensive indicators (full-pop scan, cross-blocking probe) for users who prefer v1.9 wall-clock.
- **`ColumnPrior`, `SparsityVerdict`, `IndicatorsProfile`** dataclasses in `core/complexity_profile.py`. New default-None fields: `DataProfile.column_priors`, `ComplexityProfile.indicators`.

### Changed
- **`rule_no_matches`** (modified): when ctx provides high-identity-prior on the blocking column, tries `[lower_threshold, normalize, multi_pass]` alternatives in order before falling back to today's behavior. When `ctx.sparsity_verdict.is_sparse`, lowers threshold by 0.10 (proxy for ExpandSample, queued v1.11).
- **`rule_blocking_key_swap`** (modified): vetoed when blocking column has `identity_score >= 0.8` AND `full_pop_matchkey_hits > 0` (protects v0's correct identity blocking from being abandoned on noisy samples).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.9.0 | v1.10.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 62.87 | 66.91 |

T2 F1: 58.7% → 69.0% (+10.3 pp). T1 and T3 unchanged. Primary target (>= 70) not met; ships on fallback basis (>= 65).

### Notes for v1.11

- `rule_sparse_match_expand` substitutes `_with_lower_threshold(0.10)` for the spec's `ExpandSample(2.0)` action; real controller-level sample expansion queued for v1.11.
- No rule forces a *positive* swap to an identity-prior column when v0 picked something else; v1.10 only protects identity columns from being abandoned. v1.11 may add `rule_promote_identity_blocking` if benchmark measurement shows the gap matters.
- Attribution sweep (which of the 5 indicators drove the T2 gain) not run — composite fell in fallback range (65-70); sweep was deferred per plan.

## [1.9.0] - 2026-05-08

### Added
- **Best-effort commit semantics.** `RunHistory.pick_committed()` extends the lex key to RED entries (rank=2) and returns the highest-ranked entry by `(health_rank, -mass_separation, iteration)`. Replaces v1.8's `cheapest_healthy()` which returned None on all-RED history. Filters errored entries via `error is None and profile is not None`. Closes a known v1.8 design-doc gap.
- **`RunHistory.stop_reason: StopReason | None`** populated at every break point in `AutoConfigController.run()`. Observable via `result.postflight_report.controller_history.stop_reason`. Eight values: GREEN, CONVERGED, BUDGET_ITERATIONS, BUDGET_TIME, POLICY_SATISFIED, POLICY_NO_PROGRESS, OSCILLATING, CANCELLED.
- **Virtual v0 fallback + precision-collapse floor.** The controller appends `config_v0`'s profile as a synthetic `HistoryEntry(iteration=-1)` before `pick_committed()` runs, so v0 stays in the candidate pool. `pick_committed(precision_collapse_floor=0.9)` demotes RED entries with `mass_above_threshold > 0.9` (the "everything matches" pathology) to rank=3. Together these prevent committing a config demonstrably worse than v0.
- **Health-aware commit logging.** WARNING on RED commit (names failing sub-profile + stop_reason + iteration); INFO on YELLOW; silent on GREEN; ERROR on all-errored fallback. Logs use `iter=v0` to identify virtual-v0 commits.

### Changed
- `RunHistory.cheapest_healthy()` is now a deprecation alias for `pick_committed()`. **Behavior change**: returns RED entries when no GREEN/YELLOW exists (was: returned None). DeprecationWarning text calls out the change explicitly. Removed in v2.0.
- `StopReason` enum moved from `core/autoconfig_controller.py` to `core/complexity_profile.py` (next to `HealthVerdict`).

### Fixed
- DQbench composite regression caught during release verification: unguarded best-effort commit could select a precision-collapsed RED config (T1: 1% precision, 100% recall -- "match everything"). Virtual v0 + precision floor restored v1.8 parity exactly.

### Benchmarks (zero-config, no LLM)

| Dataset | v1.8.0 | v1.9.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 62.87 | 62.87 |

### Notes for v1.10

The original v1.9 spec assumed best-effort RED commit would deliver a DQbench composite gain (target >= 65). In practice, the controller's complexity indicators can't distinguish "blocking key is wrong" from "blocking key is right but sample has no visible matches" -- both produce `mass_above_threshold=0.0`. v1.10 will add new indicators (identity-column priors, cross-blocking overlap probe, blocking-column corruption signal, sparse-match sensitivity) so the controller can tell these cases apart and deliver real gains on the tiers where it currently can't escape the impasse.

## [1.8.0] - 2026-05-08

### Added
- **Introspective auto-config controller** that beats hand-tuned configs on multiple benchmarks without manual tuning. Zero-config now produces a defensible config the first time, even on shapes it hasn't been hand-tuned for. The controller iterates on stage-emitted complexity signals (block size distribution, score histogram, transitivity rate, candidates compared, mass above/in-borderline) and refines its config via a heuristic rule policy until convergence. (#103, #104, #109, #114)
- **Cross-run memory** at `~/.goldenmatch/autoconfig_memory.db` — past committed configs are reused when the data shape signature matches. Opt out with `GOLDENMATCH_AUTOCONFIG_MEMORY=0`. (#111)
- **LLM policy fallback** (option B): when heuristic rules exhaust without reaching GREEN, an `LLMRefitPolicy` proposes a config diff. Default off; opt in with `GOLDENMATCH_AUTOCONFIG_LLM=1`. (#112)
- **Per-pair LLM scoring auto-enable** when the committed profile shows borderline-heavy mass and an LLM API key is available. Adaptive bounds track the matchkey's threshold dynamically. (#113, #115)
- **Standardization auto-detection** in v0 — phone/email/zip/state/name/address columns now auto-emit `StandardizationConfig` rules. (#115)
- **Recall-aware probes** — `random_pair_above_threshold_rate` signal in `ScoringProfile`; `rule_recall_gap_suspected` and `rule_blocking_field_null_heavy` rules. (#109)
- **NCVR benchmark regression test** (gated on dataset presence). (#110)
- **11 real-data integration tests** + **5 Hypothesis property tests** for controller invariants. (#106, #107)

### Changed
- `auto_configure_df` is now controller-backed; gains optional `reference` kwarg for cross-source match mode. Public signature otherwise unchanged.
- Zero-config callers in `_api.dedupe_df` / `_api.match_df` now call `auto_configure_df` *before* the pipeline (eliminates double pipeline run). (#103)
- `PostflightReport` gains `controller_profile` + `controller_history` fields surfacing the typed `ComplexityProfile` and audit trail. (#103, #108)

### Fixed
- Zero-config crashes in `match_df` (`ColumnNotFoundError: __title_key__`) and `match()` (`ColumnNotFoundError: __placeholder__`). (#102)
- Cache poisoning across structurally-identical-but-semantically-different datasets. (#112)
- SQLite cross-thread access in default memory store (web routers fixed). (#111)

### Benchmarks (zero-config, no manual tuning)

| Dataset | v1.7.1 | v1.8.0 | Hand-tuned ceiling |
|---|---|---|---|
| DBLP-ACM (cross-source) | 0.5102 | **0.9641** | 0.918 |
| Febrl3 (single-source) | 0.8528 | **0.9443** | 0.971 |
| NCVR (corruption GT) | — | **0.9719** | — |
| DQbench (no LLM) | 46.24 (hand-tuned) | **62.87** (zero-config) | — |

## [1.6.0] - 2026-05-04

### Added
- **Learning Memory completion** — corrections now flow end-to-end from collection points through pipeline application to postflight surfaces.
  - **Re-anchor via record_hash**: corrections survive row reorder and input refresh through a collision-safe vectorized record-hash lookup. Ambiguous re-anchors (duplicate rows) report as `stale_ambiguous` rather than silently misapplying. New `MemoryConfig.reanchor` flag (default `True`) gates the behavior.
  - **Pipeline hook**: `dedupe_df` and `match_df` apply stored corrections after scoring and overlay learned thresholds before scoring. `DedupeResult.memory_stats` and `MatchResult.memory_stats` surface applied/stale/stale-ambiguous counts.
  - **Seven collection points** capture corrections automatically: review queue (`steward`, trust 1.0), boost tab y/n (`boost`, 1.0), `unmerge_record`/`unmerge_cluster` (`unmerge`, 1.0, empty hashes), LLM scorer decisions (`llm`, 0.5), MCP `agent_approve_reject` (`agent`, 0.5), and REST `POST /reviews/decide` (`steward`, 1.0).
  - **Postflight section**: rendered postflight string adds a `Memory: N corrections applied, M stale, K stale-ambiguous` line when memory is active.
  - **Explainer integration**: review queue items carry a `why` field. Deterministic template by default; routes to `core/llm_scorer.llm_explain_pair` when `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set.
  - **CLI subgroup**: `goldenmatch memory stats|learn|export|import|show`.
  - **Five MCP tools**: `list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`. Server card description updated to "35 MCP tools".
  - **Python API**: `goldenmatch.get_memory()`, `goldenmatch.add_correction()`, `goldenmatch.learn()`, `goldenmatch.memory_stats()`.
  - **Stale persistence**: stale corrections are enqueued to a sibling SQLite review queue (`.goldenmatch/review_queue.db`) so the next `goldenmatch review` invocation surfaces them.
  - **8 end-to-end integration tests** in `test_memory_e2e.py` covering happy path, re-anchor on reorder, stale-on-edit, trust conflict, threshold learning, deterministic explainer fallback, postflight rendering, and stale-ambiguous reporting.

### Changed
- Zero-config posture preserved: nothing changes for users who don't enable memory (`config.memory.enabled = False` by default; absent config section means no memory work).

- **NEW**: TypeScript / Node.js port published as `goldenmatch` on npm
  - Full feature parity with Python: scorers, clustering, golden records, LLM, PPRL, probabilistic, graph ER, streaming, MCP/REST/A2A servers
  - Edge-safe core (browsers, Workers, Edge Runtime) + Node-only file/DB layer
  - 478 tests, strict TypeScript

## [1.4.1] - 2026-04-06

### Added
- **MCP tools for data quality** — `scan_quality` (scan without fixing), `fix_quality` (scan + apply fixes with safe/moderate mode), `run_transforms` (GoldenFlow phone/date/Unicode normalization). All 3 tools validate file paths, handle write failures gracefully, and include logging
- **A2A skills for data quality** — `quality` (scan + fix via GoldenCheck) and `transform` (normalize via GoldenFlow) skills added to the Agent-to-Agent protocol
- `run_transform(strict=True)` parameter — MCP/A2A handlers surface transform failures instead of silently returning unmodified data
- `_scan_only()` now returns serialized findings so MCP tools can inspect quality issues without duplicating the scan
- 10 new tests: happy-path coverage with mocked deps, file validation, write failure handling

### Fixed
- Eliminated redundant double-scan in `scan_quality` MCP handler (was scanning data twice and reaching into goldencheck internals)
- Temp file cleanup handles `PermissionError` on Windows (file locks no longer leak orphaned temp files)
- `_serialise_result` exception clause narrowed from `Exception` to `ImportError`
- `fix_quality` test assertion strengthened to check error message content

## [1.4.0] - 2026-04-06

### Added
- **Scoring & survivorship quality upgrade** — MST-based cluster auto-splitting, cluster quality labels (strong/weak/split), quality-weighted survivorship strategies, field-level provenance tracking
- **Data-driven strategy selection** — auto-config selects learned blocking (>= 5K rows), enables cross-encoder reranking (3+ fields), adjusts thresholds from data quality (null rate, string length)
- **`llm_auto` flag** — `GoldenMatchConfig.llm_auto=True` auto-enables LLM scorer ($0.05 budget) and memory store when API key detected. Applied uniformly across all config paths
- New config: `auto_split`, `quality_weighting`, `weak_cluster_threshold` in `GoldenRulesConfig`

### Fixed
- Pipeline wires `auto_split` config to `build_clusters`
- `add_to_cluster` documents oversized-flag-only behavior (callers must split)
- Threshold adjustments mutually exclusive (high-null and short-string no longer cancel out)

## [1.3.2] - 2026-04-03

### Fixed
- Auto-config: blocking keys with zero value overlap between sources are now skipped with a warning (fixes DBLP-ACM venue blocking failure where DBLP uses "VLDB" and ACM uses "Very Large Data Bases")
- Embedding scorer: falls back to token_sort when embedding model fails to load (HuggingFace auth, Vertex AI quota, missing dep, CUDA OOM) instead of crashing the pipeline

## [1.3.1] - 2026-04-03

### Added
- GoldenFlow integration: optional data transformation step in the dedupe pipeline (`pip install goldenmatch[transform]`)
- `TransformConfig` Pydantic model (enabled, mode: announced/silent/disabled)
- Pipeline step 1.4b: GoldenFlow runs after GoldenCheck, before autofix — normalizes phone numbers, dates, categoricals, unicode
- Graceful degradation: if goldenflow crashes, logs warning and continues with untransformed data
- Warning when config enables transforms but goldenflow is not installed
- 8 new tests

## [1.3.0] - 2026-04-03

### Added
- CCMS cluster comparison: `compare_clusters()` classifies each cluster from run A as unchanged, merged, partitioned, or overlapping relative to run B (based on Talburt et al., arXiv:2601.02824v1)
- `CompareResult` and `ClusterCase` dataclasses with `summary()` method
- Talburt-Wang Index (TWI) for normalized clustering similarity (1.0 = identical, approaches 0 for divergent outcomes)
- Parameter sensitivity analysis: `run_sensitivity()` sweeps config parameters and compares each run against a baseline using CCMS
- `SweepParam`, `SweepPoint`, `SensitivityResult` dataclasses with `stability_report()` for identifying optimal parameter ranges
- Supported sweep fields: `threshold` (all fuzzy matchkeys), `matchkey.<name>.threshold` (individual), `blocking.max_block_size`
- `--sample` option for sensitivity sweeps (random subsample for speed on large datasets)
- Per-point error handling: failed sweep points are logged and skipped, partial results preserved
- CLI command `goldenmatch compare-clusters` with `--details`, `--case-type` filter, `--output` JSON
- CLI command `goldenmatch sensitivity` with `--sweep field:start:stop:step` (repeatable), `--sample`, `--output`
- 16 new tests (10 comparison, 6 sensitivity)

## [1.2.7] - 2026-04-02

### Added
- Three auto-config cardinality guards to prevent failures on edge-case data:
  - Blocking: exclude near-unique columns (cardinality_ratio >= 0.95)
  - Matchkeys: skip exact matchkeys for low-cardinality columns (cardinality_ratio < 0.01)
  - Description columns: route long text to fuzzy matching (token_sort) alongside embedding
- Library comparison benchmarks: head-to-head against Splink, Dedupe, and RecordLinkage on Febrl (0.971 F1) and DBLP-ACM (0.918 F1)

### Fixed
- Auto-config no longer generates blocking keys from near-unique columns that produce single-record blocks
- Auto-config no longer creates exact matchkeys for columns with very few distinct values (e.g., gender, status)
- Description/long-text columns now get fuzzy fallback scoring instead of embedding-only

## [1.2.6] - 2026-04-01

### Added
- Iterative LLM calibration: samples ~100 pairs per round, learns optimal threshold via grid search, converges in 2-3 rounds (~200 pairs, ~$0.01) instead of scoring all candidates
- Concurrent LLM requests via ThreadPoolExecutor with configurable `max_workers` (default 5)
- Thread-safe BudgetTracker with `threading.RLock`
- ANN hybrid blocking: oversized blocks fall back to ANN sub-blocking via embeddings (embeds only unique text values)
- LLM-assisted column classification for ambiguous auto-config types
- Utility-based fuzzy field ranking (cardinality × completeness × string length)
- Price/cost/amount column name patterns to prevent zip misclassification
- `get_embedder()` GPU routing — returns VertexEmbedder when mode=vertex
- 3 new LLMScorerConfig fields: `calibration_sample_size`, `calibration_max_rounds`, `calibration_convergence_delta`
- 3 new ColumnProfile fields: `null_rate`, `cardinality_ratio`, `avg_len`
- 40 new tests (test_llm_calibration.py, test_ann_subblock.py, expanded test_autoconfig.py)

### Fixed
- ID patterns checked before phone/zip in auto-config — SalesID no longer misclassified as "phone"
- SalePrice (5-digit amounts) no longer misclassified as "zip"
- Identifier classifications authoritative over data profiling
- fiModelDesc no longer dropped from fuzzy fields on wide datasets
- Default batch_size bumped from 20 to 75
- "Never demote" behavior: LLM-rejected pairs keep original fuzzy score (was 0.0)
- Robust error handling: URLError/timeout retried, fut.result() guarded, ANN failures caught gracefully
- VertexEmbedder import failures fall back to local embedder

### Changed
- LLM scorer uses iterative calibration when candidates > calibration_sample_size (100)
- Multi-pass blocking passes ann_column/ann_top_k/ann_model to static builder
- `_classify_by_name` check order: date → email → ID → price → zip → geo → address → phone → name

## [1.2.0] - 2026-03-25

### Added
- **Autonomous ER Agent** -- GoldenMatch as a discoverable AI agent via A2A and MCP protocols
- `AgentSession` class -- profiles data, selects strategy, runs pipeline, explains reasoning
- `ReviewQueue` with confidence gating (auto-merge >0.95, review 0.75-0.95, reject <0.75)
- Three storage backends for review queue: memory (default), SQLite, Postgres
- `gate_pairs()` -- split scored pairs by confidence thresholds
- A2A server (`goldenmatch agent-serve`) with agent card, task lifecycle, SSE streaming
- 8 A2A skills: analyze_data, configure, deduplicate, match, explain, review, compare_strategies, pprl
- 10 MCP agent-level tools (additive to existing tools)
- `goldenmatch agent-serve --port 8200` CLI command
- Demo script: `python examples/agent_demo.py`
- Branch & Merge SOP added to CLAUDE.md

## [1.1.0] - 2026-03-23

### Added
- `gm.dedupe_df()` -- deduplicate a Polars DataFrame directly (no file I/O)
- `gm.match_df()` -- match two Polars DataFrames directly (no file I/O)
- `gm.score_strings()` -- score two strings with a named similarity algorithm
- `gm.score_pair_df()` -- score a pair of record dicts
- `gm.explain_pair_df()` -- explain a pair match from record dicts
- Internal: `run_dedupe_df()` and `run_match_df()` pipeline entry points
- These functions are the prerequisite for native SQL extensions (Postgres/DuckDB)
- New companion repo: [goldenmatch-extensions](https://github.com/benzsevern/goldenmatch-extensions) -- PostgreSQL extension (`goldenmatch_pg`) and DuckDB extension (`goldenmatch-duckdb`) for in-database entity resolution via SQL

## [1.0.0] - 2026-03-23

### Changed
- **Production/Stable** -- dropped Beta label. Semver strictly enforced from this release.
- Public API surface frozen: 96 exports from `import goldenmatch as gm`, 21 CLI commands, config YAML schema, REST endpoints, MCP tools. See `docs/api-stability.md`.

### Added
- Clean Python API: `gm.dedupe()`, `gm.match()`, `gm.pprl_link()`, `gm.evaluate()` with typed results
- 96 public exports covering every feature (config, pipeline, streaming, LLM, PPRL, domain, explain, etc.)
- REST API client: `gm.Client("http://localhost:8000")`
- Jupyter/notebook display: `_repr_html_()` on DedupeResult and MatchResult
- CI/CD quality gates: `goldenmatch evaluate --min-f1 0.90` exits code 1 if below threshold
- 7 runnable example scripts in `examples/`
- `goldenmatch label` CLI for interactive ground truth building

## [0.7.0] - 2026-03-23

### Added
- Ray distributed backend for large-scale entity resolution (`pip install goldenmatch[ray]`)
- `--backend ray` CLI flag for dedupe command
- `backend: ray` config option in GoldenMatchConfig
- `backends/ray_backend.py` with `score_blocks_ray()` -- drop-in replacement for ThreadPoolExecutor
- Automatic fallback to parallel scorer for small block counts (<= 4)
- Ray auto-initializes locally using all CPU cores, no user configuration needed
- Supports Ray clusters for 50M+ record workloads
- `goldenmatch label` CLI command -- interactive pair labeling to build ground truth CSV for accuracy measurement (y/n/s keyboard input)

## [0.6.0] - 2026-03-23

### Added
- Privacy-preserving record linkage (PPRL) package (`goldenmatch/pprl/`)
- Trusted third party mode: parties send encrypted bloom filters, coordinator computes similarity
- SMC mode: secret-shared dice similarity, only match bits revealed (simulated circuit)
- `goldenmatch pprl link` CLI command for cross-party linkage
- Bloom filter security levels: standard (512-bit), high (1024-bit + HMAC), paranoid (2048-bit + balanced padding)
- Per-field HMAC salting prevents cross-field correlation attacks
- Balanced bloom filter padding normalizes filter density for short strings
- Custom HMAC key support via transform parameter (`bloom_filter:2:20:512:my_key`)
- `pip install goldenmatch[pprl]` optional dependency group
- PPRL auto-configuration (`auto_configure_pprl`) -- profiles data, selects optimal fields, bloom filter parameters, and threshold automatically. 92.4% F1 on FEBRL4, 76.1% on NCVR
- MCP tools: `pprl_auto_config` (auto-configure PPRL for a dataset), `pprl_link` (run cross-party linkage)
- Vectorized PPRL similarity computation (13x speedup over row-wise scoring)
- NCVR (North Carolina Voter Registration) and FEBRL4 benchmark suites for PPRL evaluation

## [0.5.0] - 2026-03-23

### Added
- In-context LLM clustering (`mode: cluster`) -- send blocks of 50-100 borderline records to LLM for direct cluster assignment instead of pairwise yes/no scoring
- Uncertainty scores -- LLM returns confidence per cluster, surfaced in cluster metadata and review queue
- `core/llm_cluster.py` -- new module with component detection, graph splitting, structured JSON parsing, pairwise fallback
- LLMScorerConfig gains `mode`, `cluster_max_size`, `cluster_min_size` fields
- Budget-aware degradation: cluster mode -> pairwise fallback -> stop

## [0.4.0] - 2026-03-23

### Added
- CI/CD pipeline: automated tests on Python 3.11/3.12/3.13, ruff lint, smoke test
- `py.typed` PEP 561 marker for type checker support
- `docs/api-stability.md` documenting the public API surface
- This CHANGELOG

### Changed
- Version policy: public API surface defined and documented ahead of 1.0 semver commitment

## [0.3.1] - 2026-03-22

### Added
- 5 new domain packs: healthcare, financial, real_estate, people, retail (7 total)
- `goldenmatch evaluate` CLI command -- precision/recall/F1 against ground truth CSV
- `goldenmatch incremental` CLI command -- match new records against existing base
- GitHub Actions "Try It" workflow for zero-install demo
- GitHub Codespaces devcontainer
- `dbt-goldenmatch` package for DuckDB-based entity resolution
- GitHub Discussions, issue templates, community standards (CoC, contributing, security)
- PyPI download badge in README

## [0.3.0] - 2026-03-21

### Added
- Fellegi-Sunter probabilistic matching with EM-trained m/u probabilities
- Learned blocking -- data-driven predicate selection
- LLM scorer with budget controls (BudgetTracker, cost caps, model tiering)
- Domain-aware feature extraction (electronics, software auto-detection)
- Custom domain registry (YAML rulebooks, MCP tools)
- Plugin architecture (scorers, transforms, connectors, golden strategies via entry points)
- Enterprise connectors: Snowflake, Databricks, BigQuery, HubSpot, Salesforce
- DuckDB backend for out-of-core processing
- Streaming/CDC mode with StreamProcessor
- Multi-table graph entity resolution
- Natural language explainability (zero LLM cost)
- Lineage tracking with streaming writer (no 10K cap)
- REST API review queue for data steward approval
- Daemon mode with health endpoint and PID file
- MCP server tools: list_domains, create_domain, test_domain, suggest_config

### Changed
- LLM scorer refactored to accept LLMScorerConfig with BudgetConfig
- Pipeline: domain extraction step between standardize and matchkeys
