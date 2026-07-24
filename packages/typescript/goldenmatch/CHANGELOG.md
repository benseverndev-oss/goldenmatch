# Changelog

All notable changes to goldenmatch-js are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [1.21.0] - 2026-07-24

### Added
- **`runs`, `rollback`, `unmerge`, `config` CLI commands** (parity batch 4) — the run-context cluster. goldenmatch `cli_commands.python_only` **16 → 12**.
  - `runs [--output-dir]` / `rollback <run_id> [--output-dir]` — list previous runs and roll one back (deleting its output files). Both ride the **already-existing** durable run log (`node/mcp/run-log.ts` over `.goldenmatch_runs.json`), which was ported earlier for the `list_runs`/`rollback` MCP tools.
  - `unmerge <record_id> --clusters <csv> [--pairs <csv>] [--shatter] [-t thr] [-o out]` — per-entity unmerge over **exported files**, matching Python: a clusters CSV (`__row_id__` + `__cluster_id__`) plus an optional scored-pairs CSV (`id_a,id_b,score`) supplying the edge weights re-clustering needs, since a clusters CSV alone carries no pair scores. Cross-cluster pair rows are ignored. Wraps the existing `unmergeRecord` / `unmergeCluster` cores.
  - `config {save,load,list,delete,show}` — named config presets, over a new `src/node/preset-store.ts` (port of Python `prefs/store.py::PresetStore`). Same `~/.goldenmatch/presets/<name>.yaml` layout, so presets are interchangeable between the toolkits.

### Known difference (documented)
- **`runs`/`rollback` `--output-dir` is jailed to the process CWD.** `run-log.ts` routes the path through `sanitizePath` (inherited from the MCP surface, where it stops a tool escaping the working directory). Python's equivalent accepts any path. Passing a directory outside CWD raises rather than silently reading elsewhere; the tests pin this.

## [1.20.0] - 2026-07-24

### Added
- **Domain rulebooks + the `list_domains` / `create_domain` / `test_domain` MCP tools** (parity batch 3; MCP **80 → 83**). Port of Python `core/domain_registry.py` — user-authored YAML extraction rules for a data domain (medical devices, automotive parts, …), the counterpart to the compiled-in `core/domain.ts` extractors.
  - `src/core/domain-rulebook.ts` (**edge-safe**): rulebook shape, `compileRulebook`, `extractWithRulebook` (brand / identifiers / attributes / normalized name + Python's exact confidence scoring), `matchDomain`. An invalid user regex is skipped and reported, never thrown — one bad pattern can't take down a rulebook.
  - `src/node/domain-registry.ts` (**node**): YAML `loadRulebook` / `saveRulebook` / `discoverRulebooks` over `.goldenmatch/domains` + `~/.goldenmatch/domains`, in Python's snake_case key order so a rulebook authored by either toolkit loads in the other. `yaml` is an optional peer dep with an actionable error when absent.
  - `test_domain` reads the current run's rows from `RUN_STORE` (the TS analogue of Python's server-held `_rows`).
- **`interactive` CLI command** — see below.

### Fixed
- **`interactive` / `tui` were the same command counted as two gaps.** Python's CLI called it `interactive`, the TS CLI called it `tui`; both launch the TUI over optional input files, so the parity manifest listed one capability as *both* a `python_only` and a `ts_only` gap. Both CLIs now register both names (TS `cli_commands.ts_only` is now **empty**). It had to be a real second `.command()`, not `.alias()` — the surface emitter reads `program.commands.map(c => c.name())` and does not see commander aliases, so an alias would have left the manifest lying.

### Known difference (documented, not a gap)
- TS `list_domains` returns only user-authored rulebooks. Python additionally ships 7 built-in YAML packs inside its wheel (`goldenmatch/domains/`); the TS package's built-in domain knowledge is the compiled-in `core/domain.ts` extractors instead, so there is no third search path and a fresh TS install lists zero domains where Python lists 7.
- Regex parity is the common Python/JS subset. Python-only constructs (named groups `(?P<x>…)`, atomic/possessive groups, conditionals) are reported invalid rather than silently mis-matching. `\w`-style word splitting is normalized to `\p{L}\p{N}_` so it stays Unicode-aware like Python's.

## [1.19.0] - 2026-07-24

### Added
- **`analyze-blocking`, `autoconfig`, `lineage`, `explain` CLI commands** (cross-language parity batch 1). Four more `cli_commands python_only -> shared` moves; each is thin wiring over a core TS already had. This **discharges the 1.18.0 "Deferred" note** for `lineage` + `analyze-blocking`: the blocker was the missing in-memory dedupe-run context, and these commands simply run `dedupe` first to build it.
  - `analyze-blocking <files...> [-c config] [--top n] [-o out.json]` - blocking-strategy suggestions (`analyzeBlocking`) over the configured matchkey fields, de-duplicated across matchkeys; falls back to the fields zero-config picks when no `--config` is given. Mirrors Python `analyze-blocking`.
  - `autoconfig <files...> [-o out.json]` - derive and print the zero-config `GoldenMatchConfig` (`autoConfigure`): matchkeys, scorers, thresholds, blocking. Mirrors Python `autoconfig`.
  - `lineage <files...> [-c config] [-o lineage.json]` - run a dedupe and persist the per-pair lineage (`buildLineage`). Mirrors Python `lineage`.
  - `explain <files...> --pair a,b | --cluster id` - plain-language pair/cluster explanation (`explainPair` / `explainCluster`) over a fresh run. Mirrors Python `explain`.
  - Tests: `tests/unit/cli-parity-batch1.test.ts`.

### Fixed
- `lineage` CLI output no longer reports `LineageBundle.recordCount` as a record total. That field is a misnomer in `core/lineage.ts` (it is set to `edges.length`), so the command prints the edge count and `stats.totalRecords` separately. The misnomer is now pinned by a test; it is TS-internal (Python's `build_lineage` returns a bare list of edge dicts with no equivalent field), so no cross-language behavior changed.

### Deferred (documented)
- `sensitivity` + `unmerge` remain `python_only` from the 1.18.0 list: `sensitivity` needs sweep-spec plumbing (a param-sweep description the CLI has no syntax for yet) and `unmerge` needs a *persisted* run to mutate rather than a fresh in-memory one. Still a bounded follow-up, not a silent drop.

## [1.18.0] - 2026-07-23

### Added
- **`compare-clusters` + `incremental` CLI commands.** TS CLI front doors for two capabilities whose cores TS already had, matching the Python CLI commands (`cli_commands python_only -> shared`). No MCP tool-count change.
  - `compare-clusters <file_a> <file_b> [-o out.json]` - CCMS comparison of two ER cluster JSON files (`parseClustersJson` + `compareClusters` + `ccmsSummary`); prints UC/MC/PC/OC + TWI, optional JSON output. Mirrors Python `cli/compare.py`.
  - `incremental <base> -n <new-records> -c <config> [-t thr] [-o out.csv]` - match new records against a base dataset (`runIncremental`); prints processed/matched/new-entity/pair counts, optional CSV of the match pairs. Mirrors Python `cli/incremental.py`.
  - Tests: `tests/unit/cli-compare-incremental.test.ts` (per the repo convention of testing the wrapped core logic). Both smoke-verified end-to-end against the built CLI.

### Deferred (documented)
- `sensitivity` / `lineage` / `analyze-blocking` / `unmerge` CLI commands stay `python_only`: they need sweep-spec plumbing or an in-memory dedupe-run context that the stateless CLI path doesn't carry - a bounded follow-up, not a silent drop.

## [1.17.0] - 2026-07-23

### Added
- **`pprl_link` MCP tool (79 -> 80).** Class-A wiring of the already-ported TS `runPPRL` core: reads two parties' CSV files, encodes each party's `fields` as Bloom-filter CLKs, and links records without sharing raw values. Stateless (reads the files directly); `security_level` (standard/high/paranoid) picks the ngram/hash/bloom-size, mirroring Python `_tool_pprl_link`. Response shape matches Python (`clusters_found` / `match_pairs` / `total_comparisons` / `clusters`). Flips `python_only -> shared` under `mcp_tools`. Test in `tests/unit/mcp-server.test.ts` (real two-party link + missing-fields error).

### Deferred (documented, not silently dropped)
- **`pprl_auto_config` stays `python_only`.** Python's tool reads the server-loaded dataset and returns recommended fields + per-field profiles + a security-level bloom config + explanation via `pprl/autoconfig.py::auto_configure_pprl` (~250 lines: field profiling + capture-style threshold estimation over Bloom filters). The existing TS `autoConfigurePPRL` is a *different* two-party helper, not this. A faithful port + a data-source decision (TS has no server-loaded `_rows` concept) is a bounded follow-up; `pprl_link` (the actual linkage capability) is the high-value half and ships now.

## [1.16.0] - 2026-07-23

### Added
- **Identity read tools (MDM ops read-side, 75 -> 79).** Four tools completing the identity subsystem's read side. `identity_show` is class-A wiring (`getEntity` + `viewToDict`); the other three are a net-new edge-safe core port of Python `identity/profile.py` into `src/core/identity/profile.ts`:
  - `identity_profile` (`entityProfile`) - one entity's full profile: record count + per-source breakdown, golden record, confidence, conflict count, a canonical version (count of structural events), first/last activity.
  - `identity_stats` (`identitySummaryStats`) - graph health: entities by status, records-per-entity distribution (avg/p50/max, loop-based max), source mix, largest entities, total conflicts.
  - `identity_worklist` (`stewardWorklist`) - prioritized queue of active entities with open conflicts and/or confidence below `weak_confidence`, highest conflict count then lowest confidence.
  - Response shapes mirror `mcp/identity_tools.py` exactly. All 4 flip `python_only -> shared` under `mcp_tools`. On `a2a_skills`: `identity_show` -> `shared` (Python A2A exposes it), `identity_profile`/`identity_stats`/`identity_worklist` -> `ts_only` (they feed the TS A2A card via IDENTITY_TOOLS but Python's A2A does not advertise them - verified against `emit_python_surface`). Tests in `tests/unit/mcp-identity-tools.test.ts`.

## [1.15.0] - 2026-07-23

### Added
- **Identity-audit crypto + 3 audit MCP tools (PR-B of the identity-audit port, 72 → 75).** Byte-identical port of Python `identity/audit.py`, so a seal/entry-hash computed in TS verifies under Python and vice-versa over a shared `.goldenmatch/identity.db`.
  - **`src/core/identity/audit.ts`** (edge-safe, Web Crypto): `canonicalJson` (a purpose-built serializer reproducing Python `json.dumps(sort_keys=True, separators=(",",":"))` with `ensure_ascii` — recursive codepoint key-sort, ASCII `\uXXXX` escaping, Python float repr so `1.0` renders `"1.0"` not `"1"`, integer `previous_claim_id`, conditional claim-authority keys), `eventContentHash`, `foldStep`, `sealAuditLog`, `verifyAuditChain`.
  - **`emitEvent` stamps `entryHash`** at insert in BOTH stores (`SqliteIdentityStore` + `InMemoryIdentityStore`) when absent, mirroring Python `store.emit_event`.
  - **3 MCP tools** in `IDENTITY_TOOLS` (so they feed BOTH the MCP surface and the A2A card): `identity_audit` (export the log), `identity_audit_seal` (anchor a tamper-evidence seal), `identity_audit_verify` (replay + detect content edits / deletion / reorder / insertion). Response shapes mirror `mcp/identity_tools.py` exactly. All 3 flip `python_only → shared` under `mcp_tools` AND `a2a_skills`.
  - **Cross-language parity gate:** `tests/parity/audit-hash.parity.test.ts` against the Python-oracle fixture `tests/parity/fixtures/identity/audit-hash.json` (`scripts/emit_audit_hash_fixture.py`) — TS `eventContentHash` byte-matches Python across non-ASCII payloads, `trust=1.0`, ms-aligned micros, and set/unset claim fields, and `sealAuditLog` reproduces the committed root; plus a tamper-detection case.
  - **Round-trip fix:** `sqlite-store.ts::parseDate` now treats a naive `recorded_at` (written via `pyIsoformat`) as UTC, so a read-back event re-hashes to its stored `entry_hash` on any machine timezone (already-zoned edge/alias timestamps are untouched). **Sub-ms caveat:** a JS `Date` is ms-precision, so cross-verifying a Python-written sub-millisecond event from TS is an inherent `Date` limitation, not a port defect; TS-authored events are always ms-precision.

## [1.14.0] - 2026-07-23

### Added
- **Identity provenance + audit schema foundation (PR-A of the identity-audit port).** Brings the TS identity schema + provenance up to Python's existing v5 schema so the follow-up audit crypto has somewhere to live. TS is catching UP to Python (`identity/store.py` is already SCHEMA_VERSION 5 with all these columns + an `audit_seals` table); TS was at v2 and missing them. This restores the intended cross-toolkit schema identity — a `.goldenmatch/identity.db` written by either toolkit is now schema-identical. **No new MCP tools (count stays 72); no crypto.** PR-B adds `eventContentHash`/seal/verify + the 3 audit MCP tools.
  - **`SqliteIdentityStore` schema → v5** (`src/node/identity/sqlite-store.ts`): `identity_events` gains `actor`/`trust`/`claim_type`/`evidence_ref`/`previous_claim_id`/`entry_hash`; `evidence_edges` gains `actor`/`trust`; a new `audit_seals` chain table + index. Column names/types match Python `store.py` `_SCHEMA` EXACTLY. `SCHEMA_VERSION` 2 → 5.
  - **Real idempotent v2 → v5 migration** in `open()`: probes `PRAGMA table_info` and `ALTER TABLE ... ADD COLUMN` ONLY for missing columns (never blindly — re-adding an existing column errors), mirroring Python `_migrate`. A no-op on a Python-written v5 DB (columns already present); adds only the absent columns on an old TS v2 DB (they read back NULL). Both paths tested.
  - **Audit-log store methods** on the `IdentityStore` interface + both backends (`InMemoryIdentityStore` + `SqliteIdentityStore`): `exportAuditLog(dataset?)` (`ORDER BY event_id`, dataset scoping), `addSeal(seal)`, `latestSeal(dataset?)` (`dataset IS NULL` = global chain), `listSeals(dataset?)` (`ORDER BY seal_id`), mirroring Python `store.py`.
  - **New types** in `src/core/identity/types.ts`: `AuditSeal`, the `ClaimType` + `EvidenceRef` string unions, optional `actor`/`trust`/`claimType`/`evidenceRef`/`previousClaimId`/`entryHash` on `IdentityEvent`, optional `actor`/`trust` on `EvidenceEdge`, and the `EventKind` members `consolidated`/`promote`/`amend`/`revoke`.
  - **actor/trust threading:** `manualMerge`/`manualSplit`/`claimRecord` (`query.ts`) and `mediateConflict` (`mediation.ts`) accept optional `actor`/`trust` and stamp them onto the emitted events/edges (mediation derives `steward:<steward>` when no actor is given, mirroring Python). Pipeline-driven events in `resolve.ts` stay `actor=null` (byte-parity with Python's pipeline path). The 4 shipped identity MCP tools (`identity_merge`/`identity_split`/`identity_claim`/`identity_resolve_conflict`) gain optional `actor`/`trust` params + an `_actorTrust` helper (default `actor="agent"`; absent `trust` derived via the shared `trustForSource`), mirroring Python `mcp/identity_tools.py::_actor_trust`.
  - **`pyIsoformat` helper** (`src/core/identity/pyDatetime.ts`, edge-safe): reproduces Python `datetime.isoformat()` for a UTC datetime (`YYYY-MM-DDTHH:MM:SS`, `.NNNNNN` microseconds only when non-zero, no `Z`). Used as the `recorded_at` value on the TS `emitEvent` write path so the stored string hashes identically cross-toolkit (load-bearing for PR-B's byte-identical hashing).
  - Version 1.13.0 → 1.14.0. Tests: `tests/node/identity/sqlite-store.test.ts` (v2→v5 migration adds columns, new columns round-trip, a v5 DB opens unchanged, `audit_seals` CRUD, `exportAuditLog`/`listSeals`/`latestSeal` ordering + dataset scoping), `tests/unit/pyDatetime.test.ts`, extended `tests/unit/mcp-identity-tools.test.ts` (the 4 tools accept + persist `actor`/`trust`).

## [1.13.0] - 2026-07-23

### Added
- **`retrieve_similar` MCP tool** (TS↔Python parity, Tier 3 final), taking the MCP surface from **71 → 72 tools**. This is a class-B net-new edge-safe core port — the backing TS core did not exist and was ported here — and the LAST buildable Tier 3 tool (only the deferred identity-audit trio remains). It flips `python_only → shared` in `parity/goldenmatch.yaml` under `mcp_tools`.
  - Semantic retrieval (#1089): return the rows in a CSV most similar to a free-text query, ranked by cosine similarity. `src/core/retrieve-similar.ts` ports Python `core/retrieval.py::retrieve_similar_records`: embed the chosen column + the query, run ANN cosine search via the existing edge-safe `ANNBlocker` (no new ANN impl), apply an optional `{column: value}` equality pre-filter BEFORE embedding, and return the top-`k` records over `threshold`. Faithful semantics: empty on blank query / empty (filtered) corpus / nothing clearing threshold; a filter on an absent column yields no results; `__row_id__` used for the returned id when present (else row position); `__`-prefixed keys stripped from the returned record; results ranked highest-similarity first. Python-parity response `{file, query, column, count, results:[{row_id, score, record}]}` (score rounded 4dp).
  - **EDGE-MODEL CAVEAT (caller-supplied embedder):** unlike Python — whose default `"inhouse"` embedder is a bundled, zero-config, no-cloud model — the TS surface carries only the embedding KERNEL (`goldenembed-wasm`), NOT a bundled model, and the HTTP `Embedder` needs a provider + credentials. So `retrieveSimilar` REQUIRES an explicit `embedder` and throws `RetrieveSimilarError` with a clear message when none is supplied (no silent default model). The node MCP handler requires a `provider` (openai/vertex/voyage) arg + credentials (`api_key` or the provider's env var), building the embedder via the existing `getEmbedder`, and returns a clear error if the provider is missing.
  - **A2A:** `retrieve_similar` does not surface on the TS A2A card (built from BASE_SKILLS + AGENT_SKILLS + MEMORY_TOOLS + IDENTITY_TOOLS; base MCP tools do not feed it). So `a2a_skills` is unchanged: it stays `python_only` (Python's A2A still exposes the skill; TS does not) — verified via `scripts/emit_ts_surface.mjs`.
  - Tests: `tests/unit/retrieve-similar.test.ts` (top-k ordering + score shape via a stub embedder, missing-embedder errors clearly, threshold + k caps, filters incl. absent-column, `__row_id__`/position ids, `__`-key stripping, Python-parity response shape).

## [1.12.0] - 2026-07-23

### Added
- **`incremental` + `sensitivity` MCP tools** (TS↔Python parity, Tier 3 PR-4), taking the MCP surface from **69 → 71 tools**. Both are class-B net-new edge-safe core ports — the backing TS core did not exist and was ported here. Both flip `python_only → shared` in `parity/goldenmatch.yaml` under `mcp_tools`.
  - `incremental` (stateless, reads `base_file` + `new_records`) matches a batch of new records against an existing base dataset without re-running the base. `src/core/incremental.ts` ports Python `core/incremental.py::run_incremental`: base rows get `__row_id__` 0..h-1, new rows are offset above the base max so the two populations never collide; the combined frame is standardized + matchkeyed, then the matchkeys are **split into exact vs fuzzy exactly as Python does** — EXACT matchkeys resolve via `findExactMatches` (a hash equijoin over the combined frame, cross-source-filtered so only new↔base pairs survive) and FUZZY matchkeys via per-new-record `matchOne` (reuses `src/core/match-one.ts`). Best score per `(new,base)` pair; returns the Python-parity `{base_records, new_records, matched_to_base, new_entities, total_pairs, matches:[{new_row_id, base_row_id, score}]}`. **LANDMINE:** routing every matchkey through `matchOne` — or forgetting the exact matchkeys — silently drops exact-only matches (the exact path is a separate hash join). The node MCP handler reads both files, auto-configures from the base file when no `config` is given, and honors an optional `threshold` override.
  - `sensitivity` (reads `file_path` + `sweep` specs) sweeps config parameters across a range and reports how stable the clustering is at each value. The Python-faithful sweep engine was **added alongside** the pre-existing Cartesian `runSensitivity`/`stabilityReport` in `src/core/sensitivity.ts` (which stay untouched): `runSensitivitySweep` + `sweepStabilityReport` (+ `SweepSpec`/`SweepPointResult`/`SweepResult`) port `core/sensitivity.py::run_sensitivity` + `SensitivityResult.stability_report`. Each parameter (`threshold`, `blocking.max_block_size`, `matchkey.<name>.threshold`) sweeps independently over a `start:stop:step` range, and every run is compared to ONE baseline clustering via **CCMS** (`compareClusters` — not re-implemented). **LANDMINE:** per-point errors are caught so **partial results are preserved** (a failing sweep point is skipped, not fatal), and `unchanged %` is measured against the baseline. The node handler parses `'field:start:stop:step'` specs, auto-configures the file when no `config` is given, and returns the Python-parity `{results:[{best_value, best_unchanged_pct, points:[{value, unchanged, merged, partitioned, overlapping, twi}]}]}`.
  - **Boundary-prose reversal:** `sensitivity` + `incremental` were both declared **Python-only by design** (TS package `CLAUDE.md` "Deliberately not ported"). Porting them REVERSES that decision — the docs now record both as TS↔Python shared. No goldenmatch agent tools remain Python-only after this PR.
  - **A2A:** neither tool surfaces on the TS A2A card (built from BASE_SKILLS + AGENT_SKILLS + MEMORY_TOOLS + IDENTITY_TOOLS; base MCP tools do not feed it). So `a2a_skills` is unchanged: both stay `python_only` (Python's A2A still exposes them; TS does not).
  - Tests: `tests/unit/incremental.test.ts` (exact-only match found via the hash join where the fuzzy path misses it, fuzzy-only match via `matchOne`, `new_entities` counting, threshold override, Python response shape), `tests/unit/sensitivity-sweep.test.ts` (a sweep runs and CCMS-compares each point to the baseline, the baseline-value point reproduces the baseline clustering exactly, **partial results preserved when one point errors**, `stability_report` wire shape, unsupported-field + unknown-matchkey rejection).

## [1.11.0] - 2026-07-23

### Added
- **`analyze_blocking` + `certify_recall` MCP tools** (TS↔Python parity, Tier 3 PR-3), taking the MCP surface from **67 → 69 tools**. Both are class-B net-new core ports — the backing edge-safe TS core did not exist and was ported here. Both flip `python_only → shared` in `parity/goldenmatch.yaml` under `mcp_tools`.
  - `analyze_blocking` (reads the current run) diagnoses blocking on the loaded dataset. `src/core/block-analyzer.ts` ports Python `core/block_analyzer.py::analyze_blocking`: generate blocking-key candidates by column-type heuristic (`detectColumnType`) + compound pairs, score each on block-size distribution (`group_count` / `max_group_size` / `mean_group_size` / sample-std `total_comparisons` = Σ n(n-1)/2 / a composite score rewarding many small even blocks), estimate recall for the top 10 via JaroWinkler pair sampling (**reusing the existing `jaroWinkler` kernel** — no new similarity impl), demote non-covering candidates (0.5×), and rank. Same heuristics + thresholds as Python. The node MCP handler reads the current run from `RUN_STORE` (matchkey columns + rows) and returns the Python-parity `{matchkey_columns, suggestions}` (each suggestion is `asdict(BlockingSuggestion)`: `keys`/`group_count`/`max_group_size`/`mean_group_size`/`total_comparisons`/`estimated_recall`/`score`/`description`); `{error: "No dataset loaded"}` when no run is loaded.
  - `certify_recall` (stateless, reads a file) estimates match RECALL without ground truth. `src/core/recall-certificate.ts` ports Python `core/recall_certificate.py::certify_recall_df` + `estimate_recall`: auto-configure, split the matchkeys/passes into ≥3 decorrelated "systems" (`buildDecorrelatedSystems`), dedupe under each, expand clusters to within-cluster pairs (`clustersToPairs`), and fit the FP-aware **capture-recapture** estimator (recall of the union = `1 − (1−p)^K`, `p` fit from the slope of `log f_k − log C(K,k)` over the FP-free `k≥2` cells). **The result is a LOWER-BOUND POINT ESTIMATE treating each pass as a decorrelated system — NOT a supervised/ground-truth recall number.** The Python `note` framing is preserved verbatim (`"point estimate (no labels); a trustworthy lower bound needs a small labelled audit"`, the ≥3-systems / too-correlated caveats, and the high-overlap optimism warning). The node MCP handler reads a CSV `file_path` and returns the Python-parity `{estimated_recall, n_systems, found_pairs, system_overlap, estimable, note}`.
  - **Boundary-prose reversal:** `certify_recall` was previously declared **Python-only by design** (TS package `CLAUDE.md` "Deliberately not ported" + `sensitivity`/`certify_recall` unadvertised note). Porting it REVERSES that decision — the docs now record it as TS↔Python shared, with only `sensitivity` + `incremental` remaining the Python-only agent-tool delta.
  - **A2A:** neither tool surfaces on the TS A2A card (built from BASE_SKILLS + AGENT_SKILLS + MEMORY_TOOLS + IDENTITY_TOOLS; base MCP tools do not feed it). So `a2a_skills` is unchanged: `analyze_blocking` stays `python_only` (Python's A2A still exposes it; TS does not) and `certify_recall` stays absent from `a2a_skills` entirely.
  - Tests: `tests/unit/block-analyzer.test.ts` (column-type heuristic, oversized-block detection via `max_group_size`, `total_comparisons`, Python suggestion shape, score-descending order), `tests/unit/recall-certificate.test.ts` (capture-recapture lower-bound estimate + verbatim caveat framing, the <3-systems / too-correlated refusals, `toCertifyRecallResponse` wire shape, `buildDecorrelatedSystems` split, `clustersToPairs`).

## [1.10.0] - 2026-07-23

### Added
- **`schema_match` + `config_weaknesses` MCP tools** (TS↔Python parity, Tier 3 PR-2), taking the MCP surface from **65 → 67 tools**. Both are class-B net-new core ports — the backing edge-safe TS core did not exist and was ported here. Both flip `python_only → shared` in `parity/goldenmatch.yaml` under `mcp_tools`.
  - `schema_match` (stateless) auto-maps columns between two files with different schemas. `src/core/schema-match.ts` ports Python `core/schema_match.py::auto_map_columns`: score every `(col_a, col_b)` pair via exact-name / synonym / fuzzy name similarity / partial-name / value-overlap / type-compatibility, keep those `>= min_score`, greedily assign best-first (each column used at most once), then append composite mappings (e.g. `full_name → first_name + last_name`). The reference-string similarity **reuses the existing `jaroWinkler` kernel** (`scorer.ts`) — no new similarity impl. Mapping objects use the Python-parity snake_case wire shape (`col_a`/`col_b`/`score`/`method`, plus `composite_cols`). The node MCP handler reads two files via `readFile` and returns `{mappings: [...]}`.
  - `config_weaknesses` (reads the current run) diagnoses weaknesses in the run's auto-config. `src/core/config-critique.ts` ports Python `core/config_critique.py::diagnose_config` — the deterministic detectors: `source_admitted` (provenance labels), `id_admitted` (per-row IDs), `shared_value_block` (oversized blocks), `over_merge` / `distributed_over_merge`, `null_sink`, `low_signal_key` — each returning a ranked finding with a plain/technical explanation, evidence, and a `fix_config_hint`, plus a deterministic template summary. Detectors are defensive (a missing signal is skipped, never an error). The optional LLM summary (Python `GOLDENMATCH_WEAKNESS_LLM`) is deliberately not ported — the offline default is the template summary. The node MCP handler reads the current run from `RUN_STORE` (config + rows + clusters + postflight signals) and returns the Python-parity `{findings, summary_plain}` (`{error: "No dataset loaded"}` when no run is loaded).
  - **A2A:** neither tool surfaces on the TS A2A card (the card is built from BASE_SKILLS + AGENT_SKILLS + MEMORY_TOOLS + IDENTITY_TOOLS; base MCP tools do not feed it — same as PR-1's `lineage`). So `a2a_skills` is unchanged: `schema_match` stays `python_only` (Python's A2A still exposes it; TS does not) and `config_weaknesses` stays absent from `a2a_skills` entirely (Python A2A never exposed it).
  - Tests: `tests/unit/schema-match.test.ts` (synonym/exact/composite/greedy/empty + snake_case shape), `tests/unit/config-critique.test.ts` (id/source/null-sink findings, clean-config no-op, response shape, ranking + `max_findings`).

## [1.9.0] - 2026-07-23

### Added
- **`memory_import` + `lineage` MCP tools** (TS↔Python parity, Tier 3 PR-1), taking the MCP surface from **63 → 65 tools**. Both are class-A wiring on state layers that already existed — no net-new core algorithm. Both flip `python_only → shared` in `parity/goldenmatch.yaml`.
  - `memory_import` (in `MEMORY_TOOLS`, `src/node/mcp/memory-tools.ts`) is the inverse of `memory_export`: it accepts a list of correction dicts and writes each via `SqliteMemoryStore.addCorrection`, so the store's trust upsert (incoming trust < existing ⇒ ignore; same-or-higher ⇒ replace) applies for free. `record_hash`/`field_hash` are preserved **VERBATIM** — never regenerated — because `applyCorrections` re-anchors them later and `record_hash` excludes `__row_id__` so corrections survive row reordering; regenerating would break that durability. Default `source="api"` maps to trust 0.5 via `trustForSource`. Response is `{imported}` (counts rows processed, matching Python's handler even when the upsert skips a lower-trust row).
  - `lineage` (in `RUN_TOOLS`, `src/node/mcp/run-tools.ts`) reads the current run from the server-held `RUN_STORE` (Tier 1) and calls the edge-safe `core/lineage.ts::buildLineage(result)` — zero net-new core. Input schema mirrors Python's `{max_pairs, natural_language}`; response is `{count, lineage}` (one field-provenance record per golden record, with an optional natural-language summary). Returns the Python-shaped `{error: "No dataset loaded"}` when no run is loaded.
  - Tests: `tests/unit/mcp-memory-tools.test.ts` (import round-trip with verbatim hashes + the lower-trust-incoming-ignored upsert + the `api`/0.5 default), `tests/unit/mcp-run-tools.test.ts` (lineage per golden record, `natural_language` summary, `max_pairs` cap, no-run error).

## [1.8.0] - 2026-07-23

### Fixed
- **`agent_approve_reject` now PERSISTS the decision** instead of returning a `{recorded: true}` no-op. The tool was advertised on the MCP/A2A agent surface but its handler silently discarded the approve/reject — a correctness bug (it claimed to record a decision but wrote nothing). It now writes a durable `Correction` to Learning Memory, faithful to Python's `_write_agent_correction`: `source='agent'`, `trust=0.5`, empty field/record hashes, `original_score` 0.0, and the pair canonicalized to `(min, max)` before storage (the project-wide invariant, same as `add_correction`). The tool count is UNCHANGED (stays 63) — this is a behavior fix, and `agent_approve_reject` stays `ts_only` in `parity/goldenmatch.yaml` (functional-but-TS-only; the three still-unported agent tools `sensitivity`/`incremental`/`certify_recall` remain the Python-only delta).
  - Threaded an optional durable-store handle into the agent `SkillContext` (`openMemoryStore` factory + `dataset`). The node MCP/A2A surface (`handleAgentTool`) supplies it via `SqliteMemoryStore` (`.goldenmatch/memory.db`, `path` override, `better-sqlite3` optional peer dep), mirroring the `add_correction` tool's `openStore` lifecycle; only `agent_approve_reject` invokes it, so no SQLite handle opens for unrelated skills. On the edge path (no store wired) the decision is still returned but not persisted, matching Python's `memory_store=None` branch. The ephemeral review-queue side is unchanged.
  - Response shape now mirrors Python's handler: `{status: "ok", decision, job_name?, id_a, id_b, decided_by?}` (was `{recorded: true, decision, id_a?, id_b?}`).
  - Tests: `tests/unit/agent-skills.test.ts` proves persistence for both approve and reject via an in-memory `MemoryStore` (canonicalized pair, `source='agent'`/`trust=0.5`/empty hashes, reason + dataset threading), the Python-shaped response on the edge path (no store), and that an invalid decision persists nothing.

## [1.7.0] - 2026-07-23

### Added
- **`identity_claim` + `identity_resolve_conflict` MCP tools** (TS↔Python parity, mirroring `goldenmatch/mcp/identity_tools.py`). These complete the identity mutation set (`identity_merge`/`identity_split` already shipped), flipping from `python_only` to `shared` in `parity/goldenmatch.yaml` and taking the TS MCP server to **63 tools** (was 61). Both are class-B net-new core ports — the backing TS core functions did not exist and were ported here.
  - `claimRecord` (`src/core/identity/query.ts` ← Python `claim_record`) reassigns a record to a target entity and emits a `claimed` event on BOTH the gaining and losing entities. Idempotent: claiming a record already in the target is a no-op (`moved: false`, no events). Added `"claimed"` to the `EventKind` union.
  - `mediateConflict` + `openConflicts` (`src/core/identity/mediation.ts` ← Python `mediation.py`) adjudicate a `conflicts_with` pair: `same` keeps the entity intact, `distinct` splits `record_b` out via `manualSplit`, `defer` only logs. Every verdict is a durable `mediation_verdict` evidence edge + a `conflict_mediated` event. Added `"mediation_verdict"` to `EdgeKind` and `"conflict_mediated"` to `EventKind`.
  - New `IdentityStore.edgesByKind(kind, dataset?)` store method (the generic counterpart to `findConflicts`, ports Python `store.edges_by_kind`) added to BOTH the `InMemoryIdentityStore` and `SqliteIdentityStore` backends; `openConflicts` depends on it. Newest-first with a `recordedAt DESC, edgeId DESC` tiebreak for deterministic latest-wins verdict lookups.
  - **Idempotency + re-mediation:** `claimRecord`/`mediateConflict` preserve the identity-event guarding — a claim replay is a no-op, and each mediation mints a unique `mediation:<iso>:<seq>` run_name so re-adjudicating the SAME pair appends a new verdict edge instead of silently no-op'ing against the edge `UNIQUE(entity, a, b, kind, run_name)` constraint. All edge lookups canonicalize via `canonRecordPair`.
  - These mutate the durable identity graph via `SqliteIdentityStore` (`.goldenmatch/identity.db`) — the node-only, stateful, durable-SQLite-backed surface. The new core functions stay edge-safe; only the persistence backend is node-only. They do NOT touch the ephemeral `RUN_STORE`.
  - Tests: `tests/unit/identity-mediation.test.ts` (edge-safe core on `InMemoryIdentityStore`: claim reassign/no-op-replay/both-entity events, `same`/`distinct`/`defer`, `distinct` splits `record_b` via `manualSplit`, re-mediation is NOT a silent no-op, `edgesByKind` filtering), `tests/node/identity/sqlite-store.test.ts` (durable backend `edgesByKind`/`openConflicts`/claim/re-mediation), `tests/unit/mcp-identity-tools.test.ts` (the two tools' dispatch + snake_case wire format).

## [1.6.0] - 2026-07-23

### Added
- **`unmerge_record` + `shatter_cluster` MCP run-surgery tools** (TS↔Python parity, mirroring `goldenmatch/mcp/server.py`'s `_tool_unmerge_record`/`_tool_shatter_cluster`, which delegate to `MatchEngine.unmerge_record`/`unmerge_cluster`). These flip from `python_only` to `shared` in `parity/goldenmatch.yaml`, bringing the TS MCP server to **61 tools** (was 59). Both live in the node-only MCP server (`src/node/mcp/surgery-tools.ts` — `SURGERY_TOOLS`/`SURGERY_TOOL_NAMES`/`handleSurgeryTool`), NOT the edge-safe core.
  - `unmerge_record` removes a record from its cluster in the current run and re-clusters the remaining members from the stored `pairScores` (no re-scoring); the removed record becomes a singleton. `shatter_cluster` breaks a whole cluster into singletons (pair scores discarded).
  - **In-place mutation design:** both tools mutate the CURRENT run (the last `dedupe` in this stdio session) via a new `RunStore.update(runId, newResult)` method, which replaces the stored run's `result` while preserving its `runId` / `createdAt` / `rowsById` / `sourcePath` / insertion order / current pointer. Surgery edits an existing run, so it must NOT `put` a new run id — a subsequent `get_stats`/`get_cluster` reads the mutated run under the same id. The stats on the rebuilt result are recomputed the same way the dedupe pipeline computes them.
  - **Memory-write parity:** the surgery kernels (`unmergeRecord`/`unmergeCluster` in `core/cluster.ts`) accept an OPTIONAL `memoryStore` that auto-writes `reject` corrections; Python's MCP path passes none, so the TS wiring omits it too (no auto-emitted corrections).
  - Tests: `tests/unit/mcp-surgery-tools.test.ts` (record pulled out + remainder re-clustered via stored pair scores, members → singletons, run id preserved across the in-place mutation, and no-run / unknown-cluster-id / missing-record-id error paths).

## [1.5.0] - 2026-07-23

### Added
- **`list_runs` + `rollback` MCP tools** (TS↔Python parity, mirroring `goldenmatch/core/rollback.py`). These flip from `python_only` to `shared` in `parity/goldenmatch.yaml`, bringing the TS MCP server to **59 tools** (was 57 after the Tier-1 run store). Both live in the node-only MCP server (`src/node/mcp/`), NOT the edge-safe core.
  - New node-only run-log module `src/node/mcp/run-log.ts` — a faithful port of Python's `rollback.py`: `saveRunSnapshot` / `listRuns` / `rollbackRun` over an on-disk `.goldenmatch_runs.json` log (append, keep last 50, mark-and-rewrite). This is a **separate, durable** state layer from the ephemeral in-memory `RUN_STORE` (added for the run-query tools) — rollback needs a persistent record of which output *files* a run wrote.
  - `list_runs` returns the parsed run log (empty list when the log is absent or corrupt). `rollback` deletes a run's output files (each path-jailed via the shared `sanitizePath` cwd guard, mirroring Python's `safe_path` — a jailed or missing file is reported under `not_found`, never aborting the call), refuses an unknown (`{ error, available_runs }`) or already-rolled-back run, then marks it `rolled_back` + `rolled_back_at` and rewrites the log.
  - **Writer parity note:** like Python, `saveRunSnapshot` is a callable that is NOT auto-wired into the dedupe pipeline (Python's own pipeline never calls `save_run_snapshot` either — only tests do). Wiring a snapshot writer into `dedupe()`/`export_results` is a deliberate follow-up on both surfaces.

## [1.3.0] - 2026-07-14

### Added
- **Fellegi-Sunter negative evidence** (full mirror of Python #1764): the FS API surface now honors `negative_evidence` on probabilistic matchkeys end-to-end (`trainEM` -> `scoreProbabilistic` -> validation -> fallback). `trainEM` learns each penalty-bits-free NE field as a constrained 2-state dimension (separate NE matrix, u from the same random-pair sample, full likelihood inside the EM loop, `[wFired, 0.0]` clamp applied at STORAGE only under `__ne__<field>`); scoring (`scoreProbabilistic` / `scoreProbabilisticPair`) adds the fired weight (or `-abs(penalty_bits)` for the fixed override) and normalizes against the NE-aware `fsWeightRange` envelope; `validateEmResultFor` requires a 2-entry `__ne__<field>` model entry per EM-learned NE field; the tiny-dataset fallback ships Python's exact NE entries (`[-3.0, 0.0]`). New exports: `neFired`, `fsWeightRange`, `NegativeEvidenceUnsupportedError`.
- Loader/validation matrix for `negative_evidence`: the config loader now PARSES `negative_evidence` for all three matchkey types (previously it was silently dropped for every type — weighted/exact NE via YAML/JSON configs was lost too, a pre-existing bug fixed here) and enforces the per-type knob matrix: weighted/exact require `penalty` and reject `penalty_bits`; probabilistic rejects `penalty` and accepts `penalty_bits` or neither (the EM-learned shape).
- Cross-surface parity: a committed Python-trained fixture (`tests/parity/fs-negative-evidence-fixtures.json`) is re-scored in TS at FULL float equality (EM-learned and penalty-bits variants), plus a TS homonym E2E mirroring the Python #1764 success bar.

### Changed
- **Loud decline instead of silent mis-scoring.** This port opened by making every FS entry point throw `NegativeEvidenceUnsupportedError` on NE-bearing probabilistic matchkeys (before it, a Python-authored NE config consumed in TS scored WITHOUT the veto and without warning), then lifted the throws as the capability landed. Two declines are PERMANENT, matching Python: the continuous (Winkler) path (`trainEMContinuous` / `scoreProbabilisticContinuous`) rejects NE, and `derive_from` negative evidence is rejected at the loader (goldenmatch-js has no derived-column materialization — materialize the column in your data, or use the Python runtime). A third decline is TS-scope-specific: the pipeline (`dedupe` / `matchRecords`) throws `NegativeEvidenceUnsupportedError` on a probabilistic+NE matchkey, because its probabilistic scoring is a simplified weighted-style average (a pre-existing TS scope gap) that cannot apply the NE veto — use the FS API (`trainEM` + `scoreProbabilistic`) directly, or remove `negative_evidence`. Exact and weighted NE run in the pipeline unchanged.
- WASM: documented no-op — no FS scoring path exists in WASM, so negative evidence changes nothing there (the PR #1755 precedent).

## [1.2.0] - 2026-07-13

### Added
- Splink -> GoldenMatch config converter, ported from Python: `fromSplink()` (edge-safe core, `src/core/config/from-splink.ts`), the `import-splink` CLI command, and the `convert_splink_config` MCP tool. Converts Splink comparison levels, blocking rules, and trained m/u probabilities (via a Python-schema-compatible `EMResult` JSON round-trip) into a GoldenMatch config, with a findings report for lossy mappings and an opt-in `--strict` mode. `mcp_tools`/`cli_commands` parity manifest entries moved from `python_only` to `shared`.
- `levelThresholds` N-level custom banding on `MatchkeyField`, and `modelPath` on probabilistic matchkeys (persisted EM model path, Splink-style train-once -> reuse).

## [1.1.0] - 2026-06-27

### Added
- **Config-suggestion healer on WASM + TypeScript.** The `suggest-core` Rust
  kernel is compiled to `suggest-wasm` and wired into `dedupe(rows, { suggest,
  heal })` at full default-pipeline parity: a free trigger off the run's
  postflight surfaces raw candidate `suggestions` (no extra pipeline run),
  `suggest: true` runs the verified gate, and `heal: true` runs the bounded
  apply-and-re-run loop (returning `healTrail` + the healed config). The kernel
  is reached through the opt-in subpath `goldenmatch/core/suggest-wasm`
  (`enableSuggestWasm()` / `disableSuggestWasm()`) — the `[native]` analog;
  without it every surface degrades to graceful-empty and never throws.
  `buildColumnSignals` (`suggestColumnSignals.ts`) builds the kernel's column
  signals TS-side with a Python-parity fixture. Surfaces: CLI `--suggest` /
  `--heal`, MCP `review_config` tool (MCP 44 → 45), A2A `review_config` skill
  (15 agent skills). Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE`. Cross-surface
  golden-vector parity (TS == Rust == Python on shared fixtures). Design:
  `context-network/decisions/0027-healer-wasm-ts.md`.
- **MinHash/LSH sketch kernel (#1081).** `src/core/sketch.ts` is a pure-TS,
  edge-safe port of the Python/Rust sketch kernel (shingling → MinHash → banded
  LSH) using `BigInt` for u64/u128 math; it reproduces the shared golden vectors
  byte-for-byte. `src/core/lshBlocker.ts` adds `MinHashLSHBlocker` (over
  `string[]`) for near-duplicate candidate generation: `candidatePairs(texts)`
  returns deduplicated `(min,max)` pairs. WASM acceleration is deferred (pure-TS
  is the default + fallback). Part of the training-data dedup tier (#1080).
- **SimHash sketch kernel (#1082).** `src/core/simhash.ts` is a pure-TS,
  edge-safe port of the Python/Rust SimHash kernel (random ±1 hyperplane
  projection → sign-bit signature → banded LSH) for *semantic* near-duplicate
  detection over dense embeddings. Exports `simhashSignature` and
  `simhashBandHashes`; reuses the `sketch.ts` `baseHash`/`splitmix64` bitstream,
  so the projection matrix and band hashes reproduce the shared golden vectors
  (`tests/fixtures/sketch_simhash_golden.json`) byte-for-byte. The semantic
  blocker stays Python-primary (the TS port has no real embedder); this is the
  cross-language kernel parity surface. Builds on the MinHash sketch above;
  part of the dedup epic (#1080).

## [1.0.0] — 2026-06-15

**First stable release.** The API is now stable: breaking changes only at the next
major (2.0.0). This is a one-time jump from the `0.x` wave line (`0.13.0 → 1.0.0`) —
a deliberate "this is stable" signal, not a `2.0.0` product-alignment bump (see
`docs/versioning-policy.md`; npm and PyPI keep independent semver, not lockstep).

- **AgentSession agent surface ported** (the last undeclared parity gap, 2026-06-15):
  the edge-safe `AgentSession` decision core + shared `AGENT_SKILLS` registry, 14
  agent-level MCP tools (MCP **30 → 44**), the A2A skill-union agent card + fail-closed
  bearer auth + unified dispatch (`/tasks/send`, `/tasks/{id}/cancel`), and node
  file-loaders (`analyzeFile` / `deduplicateFile` / `matchSourcesFile`). Behavior-fixture
  parity vs Python (`selectStrategy` decision table). The 3 agent tools with no TS core
  (`sensitivity` / `incremental` / `certify_recall`) are declared Python-only.
- **Opt-in WASM scorer backend.** `enableWasm()` / `disableWasm()` register a
  WebAssembly scorer (the Rust `score-core` kernel via the new `score-wasm`
  crate) behind the sync `scoreMatrix` for `jaro_winkler`/`levenshtein`/`exact`.
  Because the WASM kernel IS rapidfuzz, it matches the Python source of truth
  exactly; it is CI-gated (`wasm_score` lane) against canonical rapidfuzz
  goldens. Pure-TS remains the default and the fallback; runs in Node, browsers,
  and Workers. Note: the hand-rolled pure-TS Jaro-Winkler has small known
  divergences from rapidfuzz (the 0.7 boost threshold; transposition counting on
  repeated-character words), so enabling WASM can shift borderline scores toward
  the Python values without changing dedup decisions. Aligning the pure-TS
  scorers with rapidfuzz, token_sort WASM coverage, and non-BMP codepoint parity
  are tracked follow-ups.

### Added — refdata-aware name scorers (parity with Python `refdata`)

- `given_name_aliased_jw` scorer: Jaro-Winkler with an alias-aware exact bonus
  (William <-> Bill -> 1.0), backed by a bundled given-name alias table.
- `name_freq_weighted_jw` scorer: Jaro-Winkler modulated by US Census 2010
  surname IDF in the borderline zone, backed by a bundled top-10k surname table.
- Auto-config now refines first-name columns to `given_name_aliased_jw` and
  last-name columns to `name_freq_weighted_jw` (port of `refine_matchkey_field`,
  last-name checked before first-name; `multi_name` left unrefined for parity).
- Both refdata tables are generated from the Python source of truth via
  `scripts/sync_ts_refdata.mjs` and drift-guarded by `tests/unit/refdata-sync.test.ts`.

### Changed — auto-config blocking-selection parity

- `buildBlocking` now matches Python's `build_blocking`: exact-blocking candidates
  are gated at `cardinality_ratio <= 0.5` (was 0.95), with the null and cardinality
  gates applied only to the exact pool (name columns are ungated), and the name
  multi-pass adds secondary-name-column passes when two name columns are present.

Closes the controller-stoppoint parity drift (#857, from the #856 audit). Numeric
scorer parity is locked by Python-computed ground truth in
`tests/parity/scorer-ground-truth.test.ts`.

### Fixed — weighted-matchkey null-gate parity (#860)

- `buildWeightedMatchkey` no longer drops `nullRate > 0.5` columns. Python's
  `build_matchkeys` applies no null gate to fuzzy fields — high-null name columns
  are kept and demoted via the downstream avg-null threshold adjustment, not
  dropped. Surfaced by the #857 whole-branch review: TS emitted an empty weighted
  matchkey on heavily-null person data (`sparse_people`, ~75% null `first`/`last`)
  where Python emits a `given_name_aliased_jw` + `name_freq_weighted_jw` weighted
  matchkey at threshold 0.75. `sparse_people` is now byte-equal in the
  controller-stoppoint parity suite.

### Fixed — scorer rapidfuzz parity
- `jaro` / `jaroWinkler` / `levenshtein` / `indel` now match rapidfuzz to 4
  decimals on non-BMP (codepoint iteration via `Array.from` instead of UTF-16
  code units), sub-0.7-prefix (Winkler prefix boost gated on `jaro > 0.7`), and
  repeated-char (floored transposition count `t // 2`) inputs. Existing canonical
  anchors (MARTHA, DIXON, JELLYFISH, …) are unchanged. New parity gate:
  `tests/parity/scorer-rapidfuzz.test.ts`, goldens from `rapidfuzz` 3.14.5 via
  `packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py`. Closes
  two of the three pure-TS-vs-rapidfuzz divergences the WASM slice flagged (the
  0.7 boost threshold and non-BMP codepoints) plus the repeated-char
  transposition count; token_sort WASM coverage remains the open follow-up.

## [2.0.0] - 2026-05-22

Major version: v1.18.2 plugin parity for the TS port (#208).

### Added — predefined golden-strategy plugins

All 22 v1.18.2 builtins from Python's `goldenmatch.plugins.builtin`
are now byte-identically ported. Validated by 304 vitest parity
tests that load JSON fixtures emitted from the Python sibling.

**Numeric (6):** `numeric_max`, `numeric_min`, `numeric_mean`,
`numeric_median`, `numeric_sum`, `numeric_weighted_average`.

**Format (7):** `shortest_value`, `concat_unique`, `email_normalize`,
`phone_digits_only`, `url_canonical`, `whitespace_normalize`,
`boolean_normalize`.

**Business (6):** `system_of_record`, `lifecycle_stage`,
`freshness_with_max_age`, `enum_canonical`, `regex_validated`,
`weighted_by_recency`. Date-based strategies accept
`ruleKwargs.now_iso` to pin the reference instant for deterministic
testing.

**Aggregation (3):** `count_distinct`, `count_non_null`,
`agreement_rate`.

### Added — PluginRegistry singleton

- `PluginRegistry.instance()` / `reset()` / `discover()` /
  `registerGoldenStrategy()` / `getGoldenStrategy()` /
  `hasGoldenStrategy()` / `listPlugins()`
- `BUILTIN_PLUGINS` const array of all 22 builtins
- User-registered plugins override builtins on the same name
  (matches Python's last-write-wins)

### Why v2.0.0 (not v0.11.0)

- Adds a new top-level public-API surface (22 plugins + registry)
- Goldenmatch-js had been on a 0.x track. The plugin port is the
  v2.0 milestone called out in
  `docs/superpowers/specs/2026-05-22-phase-5-typescript-port-design.md`.
- No breaking changes to existing exports -- all v0.10.0 surfaces
  still work unchanged.

### Migration from v0.10.0

No code changes required for existing callers. To consume the new
plugins:

```ts
import { PluginRegistry, NumericMaxStrategy } from "goldenmatch";

const registry = PluginRegistry.instance();
registry.discover();

const [value, conf, idx] = new NumericMaxStrategy().merge([10, 50, 25]);
// -> [50, 1.0, 1]

// Or via registry:
const strategy = registry.getGoldenStrategy("numeric_max")!;
const result = strategy.merge([10, 50, 25]);
```

User plugins:

```ts
registry.registerGoldenStrategy("my_custom", {
  name: "my_custom",
  merge: (values) => [values[0], 1.0, 0] as const,
});
```

### Out of scope (v2.1+)

- TS port of scorer / transform / connector plugin types
- Entry-point-style discovery (npm has no Python-style entry-point
  system; user plugins always register manually)

## [0.10.0] - 2026-05-19

Identity Graph on the CLI and REST API surfaces. The v0.9.0 persistent backend lit up the storage layer; v0.10.0 lights up two of the four user-facing surfaces Python ships (`web` UI is Python-only, TUI has no identity tab in Python either).

### Added — CLI

- New `goldenmatch identity` subgroup with 6 subcommands, mirroring `packages/python/goldenmatch/goldenmatch/cli/identity.py`:
  - `goldenmatch identity list [--dataset] [--status] [--limit] [--offset] [--json]`
  - `goldenmatch identity show <entity-id> [--json]`
  - `goldenmatch identity history <entity-id> [--limit] [--json]`
  - `goldenmatch identity conflicts [--dataset] [--json]`
  - `goldenmatch identity merge <source-id> <target-id>` (target stays, source absorbed)
  - `goldenmatch identity split <entity-id> <record-ids...>`
- All commands accept `--path <path>` (default `.goldenmatch/identity.db`). The store is opened lazily per command via the v0.9.0 `SqliteIdentityStore`.

### Added — REST API

- `setServerIdentityStore(store)` binder mirrors `setServerMemoryStore`. When set, the following routes are live; otherwise 503 with a hint.
  - `GET /identities?dataset&status&limit&offset` → list
  - `GET /identities/:id` → node + records + edges + events (full `IdentityView`)
  - `GET /identities/:id/history?limit` → event log
  - `GET /identities/conflicts?dataset` → conflict edges
  - `POST /identities/merge` body=`{keep, absorb}` → manualMerge
  - `POST /identities/split` body=`{entity_id, record_ids[]}` → manualSplit
- 9 new integration tests under `tests/unit/api-identity.test.ts` exercising every route plus the 503-when-unbound path.

### Not ported (Python deltas with no TS analogue)

- **Web UI Identities tab.** TS port doesn't ship a React workbench — the Python web UI at `packages/python/goldenmatch/web/frontend/` is the only one. Out of scope.
- **TUI Identities tab.** Python TUI has no identity tab either (controller tab landed v1.14, no identity tab on the roadmap).
- **`resolve` CLI subcommand.** Python ships it because the pipeline writes identity events post-cluster. The TS pipeline doesn't yet wire `resolveClusters`; deferred to a future wave.
- **MCP identity tools.** Six tools (`identity_list/show/resolve/history/conflicts/merge/split`) on the Python MCP server. TS port can ship these in a follow-up now that the API surface is stable; not in this PR to keep scope tight.

### Test counts
877 → 886 (+9 API identity).

## [0.9.0] - 2026-05-19

Persistent Identity Graph backend (Python `goldenmatch.identity.IdentityStore(backend="sqlite")` parity).

### Added

- **`SqliteIdentityStore`** in `src/node/identity/sqlite-store.ts` — full Node-only persistent backend for the Identity Graph. Implements every method on the `IdentityStore` interface (19 methods covering nodes, source records, evidence edges, events, aliases). Schema is byte-identical to Python's `goldenmatch/identity/store.py`, so an `identity.db` produced by either toolkit is readable by the other.
  - `better-sqlite3` is an optional peer dep (same pattern as `SqliteMemoryStore`).
  - WAL journal mode + 5s busy timeout + `foreign_keys=ON` for multi-process safety.
  - Schema versioning via `PRAGMA user_version` (currently v2). Migration body from Python v1 → v2 (evidence_edges unique key) preserved verbatim so a TS-opened Python v1 DB upgrades in place.
  - Record pairs canonicalized to `(min, max)` on insert (mirrors `canon_record_pair`).
- **23 new TS unit tests** under `tests/node/identity/sqlite-store.test.ts` covering every method, the close/reopen round trip, and edge canonicalization.
- **Public API:** `SqliteIdentityStore`, `SqliteIdentityStoreOptions` re-exported from `goldenmatch` (Node entry).

### Not yet shipped (deferred to v0.10.0)

- **Pipeline-driven population** — the Python `resolve_clusters(...)` hook runs after dedupe clustering and writes identity events. Wiring this into the TS pipeline is the v0.10 wave.
- **MCP identity tools** — Python ships 6 `identity_*` MCP tools backed by the persistent store. Will follow in v0.10 alongside the resolveClusters hook.

## [0.8.0] - 2026-05-12

Identity Graph edge-safe core (Python `goldenmatch` v1.15 partial parity).

### Added

- **`goldenmatch.identity`** edge-safe surface mirroring the Python `goldenmatch.identity.*` module. All exports are safe to import from Vercel Edge, Cloudflare Workers, and other Web-Standards runtimes — no `node:*` imports.
  - **Types:** `IdentityNode`, `SourceRecord`, `EvidenceEdge`, `IdentityEvent`, `IdentityAlias`, `IdentityStatus`, `EventKind`, `EdgeKind`, `IdentityView`, `IdentityStore` interface.
  - **`newEntityId(prefix?)`** — UUIDv7 via Web Crypto; deterministic when seeded for tests.
  - **`InMemoryIdentityStore`** — process-local store satisfying the full `IdentityStore` interface. Suitable for tests, edge-runtime scratch state, and code paths that don't require cross-call durability.
  - **`findByRecord(store, record)` / `getEntity(store, id)` / `listEntities(store)`** — read paths.
  - **`manualMerge(store, sourceId, targetId, ...)` / `manualSplit(store, entityId, recordIds, ...)`** — steward operations.
- **TS parity tests** (13 cases) under `tests/identity/` covering the cluster-resolve absorb/merge/create branches, `findByRecord` semantics, manual merge/split idempotency, and `IdentityView` shape parity vs the Python `IdentityView` dataclass.

### Not yet shipped (deferred to a future wave)

- **Persistent SQLite-backed `IdentityStore`** in `src/node/identity/` — the Python `IdentityStore(backend="sqlite")` writes to `.goldenmatch/identity.db`. The TS port keeps the edge-safe interface but the Node-only persistent implementation is a future wave's work. Today, `InMemoryIdentityStore` resets on process restart.
- **Pipeline-driven population** — the Python `resolve_clusters(...)` hook runs after dedupe clustering and writes identity events; the TS pipeline doesn't yet wire this hook.
- **MCP identity tools** — Python ships 6 `identity_*` MCP tools backed by the persistent store. TS will follow after the persistent backend lands.

### Python deltas NOT relevant to this wave

- **Python v1.13 (release plumbing, typed accessors).** TypeScript's strict mode already enforces equivalent invariants without runtime accessor properties; the TS surface didn't drift.
- **Python v1.14 (AutoConfigController surface-parity arc).** The arc threaded telemetry through TUI / CLI / Postgres / DuckDB surfaces that TS doesn't expose. TS already surfaces telemetry on its MCP server (added v0.5.0); the shared `serialize_telemetry` JSON shape is preserved.
- **Python v1.16 (`backend="bucket"` 5M-on-one-node)**. Polars-only path; TS port runs edge-safe in Web Crypto and doesn't ship Polars. The `backend="bucket"` Python recommendation has no TS analogue and is intentionally not ported. The TS port's scale envelope is unchanged from v0.7.0 — single-node workloads, no out-of-core backend.

## [0.7.0] - 2026-05-10

Negative-evidence parity with Python `goldenmatch` v1.11 + v1.12 (Path Y).
Python v1.12 lifted DQbench T3 from 53.8% F1 to 85.5% (+31.7 pp) by applying
NE as a post-filter on exact matchkeys directly; this release ports that
machinery to the TS runtime.

### Added

- `NegativeEvidenceField` interface and `makeNegativeEvidenceField` factory
  in `src/core/types.ts` (defaults: `threshold=0.5`, `penalty=0.5`).
  `MatchkeyConfig` variants (`ExactMatchkey`, `WeightedMatchkey`,
  `ProbabilisticMatchkey`) now accept optional `negativeEvidence`.
  `ExactMatchkey` also gains optional `threshold` so Path Y can stamp the
  default 0.5 cutoff when NE is added without a user-set threshold.
- `src/core/autoconfigNegativeEvidence.ts`:
  - `applyNegativeEvidence(mk, rowA, rowB)` — per-pair penalty sum.
  - `applyNegativeEvidenceToExactPairs(pairs, mk, allRows)` — v1.12 Path Y
    post-filter for `findExactMatches` output.
  - `promoteNegativeEvidence(config, rows, columnPriors)` — eager rule
    that walks both weighted AND exact matchkeys (v1.12 change). The
    `_is_exact_matchkey_field` anchor gate is skipped on the exact branch.
  - `pickScorerForColumn(colName, colType?)` — name-keyed scorer dispatch
    matching Python `_pick_scorer_for_column` (`email→token_sort`,
    `phone→exact+digits_only`, `address→token_sort`, otherwise
    `ensemble`).

### Changed

- `findFuzzyMatches` (`src/core/scorer.ts`) — applies NE penalty after
  weighted-sum aggregation, before the threshold compare. No-op when the
  matchkey has no `negativeEvidence`.
- `pipeline.ts` — after `findExactMatches`, calls
  `applyNegativeEvidenceToExactPairs` when the exact matchkey has NE set.
  Mirrors Python v1.12 post-filter design; `findExactMatches`'s signature
  is unchanged.
- `AutoConfigController.run()` — eager `promoteNegativeEvidence` pass runs
  once on the full row set (not the sample) before the iteration loop,
  matching Python's `auto_configure_df` pre-iteration pass.

### Tested

- 19 new unit tests across `types.negativeEvidence`, `autoconfigNegativeEvidence`,
  `scorer.negativeEvidence`, `scorer.pathY`, and `autoconfigRules.negativeEvidence`.
- 6 new Python-parity fixtures in
  `tests/parity/negative-evidence-fixtures.json` covering
  clustered-email-different-surname, clustered-phone-different-name,
  dense-population promotion, sparse no-op, blocking-field skip, and
  idempotency. All 6 green vs Python `promote_negative_evidence`.

## [0.6.0] - 2026-05-10

Indicator-aware refit parity with Python `goldenmatch` v1.9 + v1.10.

### Added

- `IndicatorContext` memoization layer (`src/core/indicators.ts`) and 5 pure
  complexity indicators ported from Python `core/indicators.py`:
  `computeColumnPriors`, `estimateSparseMatchSignal`,
  `computeCorruptionScore`, `estimateFullPopHits`,
  `computeCrossBlockingOverlap`, plus `computeIdentityCollisionSignal`
  used by the collision-aware refit rule.
- 7 new indicator-aware refit rules in `autoconfigRules.ts`:
  `ruleUniformHeavyBlocking`, `ruleBlockingFieldNullHeavy`,
  `ruleRecallGapSuspected`, `ruleCollisionSignalTooHigh`,
  `ruleSparseMatchExpand`, `ruleCrossBlockingDisagreement`,
  `ruleCorruptionNormalize`.
- `DEFAULT_RULES_V1_10` — 14-rule list mirroring Python's `DEFAULT_RULES`
  order. The legacy `DEFAULT_RULES_V1_7_V1_8` 7-rule list is still exported
  for callers that opt into base-only behavior.
- `RuleContext.indicators` optional field carries the per-iteration
  `IndicatorContext`; rules that need indicator signals are silent no-ops
  when callers run the legacy v1.7/v1.8 rule list.
- `RefitPolicy.propose(profile, current, history, indicators?)` — fourth
  positional argument (back-compat: defaults to `null`).

### Changed

- `autoConfigureRows` rewrite: matchkey naming now matches Python
  (`fuzzy_match` for weighted, `exact_<col>` for exact). Scorer selection
  follows Python's `_SCORER_MAP` (e.g. `name → ensemble`,
  `email → exact`). Adaptive threshold uses Python's formula plus the
  post-build data-quality adjustment (avg_null > 0.15 → −0.05;
  avg_len < 5 → +0.05).
- `buildBlocking` aligned with Python: prefers high-cardinality
  exact-eligible columns (email/phone/zip/identifier/year) for static
  blocking, falls back to multi-pass name blocking
  (`soundex` + `substring:0:5` + `token_sort + substring:0:8`).
- Controller provisions a fresh `IndicatorContext` per iteration and
  threads it into `policy.propose()` for v1.10 rule consumption.

### Parity status

- Controller stoppoint parity: 6/6 datasets pass shape-level assertions,
  2/6 (`dirty_people`, `mixed_blocking`) byte-equal on the normalized
  committed config. The remaining 4 diverge because Python's iteration
  path hits a `ModuleNotFoundError` on subsequent iterations and falls
  back to a virtual v0 entry (out-of-scope to replicate in TS).
- Indicators parity: 8/8 fixture datasets pass at 4-decimal tolerance
  on the 5 indicators. Identity-collision signal is unit-tested only —
  the TS pure-JS token-sort approximation diverges numerically from
  Python's `rapidfuzz.token_sort_ratio` at sub-rule precision, but the
  rule-firing boundary (rate > 0.75) is preserved.

## [0.5.0] - 2026-05-10

Auto-config controller parity with Python `goldenmatch` v1.7 + v1.8.

### Added

- `AutoConfigController` (async `.run()`) — iterative auto-config with
  pathological-input gates, deterministic sampling, policy-driven refit loop,
  and best-effort commit via `RunHistory.pickCommitted`.
- `ComplexityProfile` + sub-profiles (`DataProfile`, `DomainProfile`,
  `MatchkeyProfile`, `BlockingProfile`, `ScoringProfile`, `ClusterProfile`,
  `ProfileMeta`, `IndicatorsProfile`) with `HealthVerdict` rollup.
- `RunHistory` audit trail with `PolicyDecision` / `ErrorRecord` / `HistoryEntry`
  and `pickCommitted(precisionCollapseFloor)` lexicographic commit selection.
- `HeuristicRefitPolicy` rule dispatcher + 7 base v1.7/v1.8 rules:
  `ruleBlockingSingletonTrap`, `ruleBlockingTooCoarse`, `ruleBlockingKeySwap`,
  `ruleLowReductionRatio`, `ruleLowTransitivity`, `ruleNoMatches`,
  `ruleUnimodalScoring`.
- `StopReason` telemetry (8 variants matching Python).
- `autoConfigureRowsIterate(rows)` async iterative entry point.
- `AutoconfigOptions.iterate` field (default `false`; preserves pre-0.5.0
  behavior).
- `getLastControllerRun()` debug accessor mirroring Python's
  `_LAST_CONTROLLER_RUN` ContextVar.
- Parity test suite: 6 dataset fixtures generated from the Python sibling
  via `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py`.

### Deferred to v0.6.0 (Wave 2)

- 5 complexity indicators + `IndicatorContext` memoization.
- Indicator-aware refit rules (`ruleCorruptionNormalize`,
  `ruleCrossBlockingDisagreement`, `ruleSparseMatchExpand`).
- Indicator-aware extensions to `ruleBlockingKeySwap` and `ruleNoMatches`.

## [0.4.0] - 2026-05-05

### BREAKING

- `Correction.verdict` renamed to `Correction.decision` (`"approve" | "reject"`)
- `Correction.feature` renamed to `Correction.matchkeyName`
- `MemoryStore` interface methods are now async (return `Promise<...>`)
- `runDedupePipeline` and `runMatchPipeline` are now async
- `dedupe`, `match`, `dedupeFile`, `matchFile` API functions are now async
- Hash algorithm changed from FNV-1a to SHA-256 (cross-language storage parity with Python goldenmatch v1.6.0)
- `MemoryConfig.backend` enum: `"sqlite" | "postgres"` -> `"memory" | "sqlite"`
- `MemoryConfig.trust`: `number` -> `{ human: number; agent: number }` (matches Python)

### Added

- Pipeline integration for Learning Memory (`config.memory.enabled = true`)
- Five MCP tools: `list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`
- CLI subgroup: `goldenmatch-js memory <stats|learn|export|import|show>`
- Python API mirror: `getMemory`, `addCorrection`, `learn`, `memoryStats`
- `SqliteMemoryStore` (Node only; requires `better-sqlite3` peer dep)
- Cross-language parity tests (JSON, SQLite, apply-outcome) -- Python and TS both run against the same fixtures
- Postflight rendering: `Memory: N applied, M stale, K stale-ambiguous, J unanchorable`
- Re-anchoring: corrections survive row reordering across runs (collision-safe; `record_hash` excludes `__row_id__`)
- `CorrectionStats.staleAmbiguous` and `staleUnanchorable` counters
- Explainer integration: `ReviewItem` carries a one-sentence `why`, deterministic by default with LLM upgrade when API key is set
