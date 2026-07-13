# Splink Converter + N-level Fields: TypeScript Surface (Thesis Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Splink→GoldenMatch config converter and N-level `level_thresholds` probabilistic fields to the TypeScript package (`packages/typescript/goldenmatch`), moving `import-splink` and `convert_splink_config` from `python_only` to `shared` in the parity manifest; merge to main.

**Architecture:** Pure-TS port — investigation confirmed NO WASM path does FS level banding (probabilistic.ts imports only types/scorer/transforms), so no capability guard is needed; the WASM half of "TS/WASM surface" is a documented no-op verdict. Four work fronts: (1) `levelThresholds` banding in `types.ts`/`loader.ts`/`probabilistic.ts`; (2) `EMResult` JSON (de)serialization **byte-compatible with Python's `EMResult.to_dict()` schema v1** so trained-model files round-trip across surfaces; (3) `src/core/config/from-splink.ts` porting `from_splink.py` (recognizers → comparison assembly → blocking → scalars → trained-model import → `fromSplink()` + strict mode); (4) CLI `import-splink` + MCP `convert_splink_config` + parity manifest flip.

**Tech Stack:** TypeScript (edge-safe core, no `node:` imports in `src/core`), commander CLI, vitest. **Box constraint:** full vitest/tsup OOMs — run vitest per-file (`npx vitest run <path>`), never build locally; CI validates build + parity emitters.

**Working branch:** `feat/splink-converter-ts` off fresh origin/main, in worktree `..\goldenmatch-wt-splink-converter`.

**Porting sources (Python, all on main):** `goldenmatch/config/from_splink.py` (authoritative semantics incl. `_strip_outer_parens`, `_LEV_ASSUMED_LEN=10`, level-order reversal, re-normalization warnings), `goldenmatch/cli/import_splink.py`, `goldenmatch/config/schemas.py` (level_thresholds validation), `goldenmatch/core/probabilistic.py` (banding + EMResult schema v1), the six `tests/test_from_splink_*.py` files (port the TEST VECTORS — they encode every reviewed edge case: paren-wrapped conjuncts, out-of-range threshold drops, collapsed-level summing, partial m/u epsilon fills, mixed bare/trained skips).

**Known drift, do NOT fix here:** TS `partialThreshold` default is 0.7 vs Python 0.8 (pre-existing; changing it is a behavior change out of scope — note in PR body).

---

### Task T1: `levelThresholds` in the TS core

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/types.ts` (MatchkeyField ~line 26), `src/core/config/loader.ts` (`parseMatchkeyField` ~239, `configToYaml` snake_case emit), `src/core/probabilistic.ts` (`buildComparisonVector` ~81)
- Test: `tests/unit/nlevel.test.ts` (create; vitest.config.ts only includes `tests/**/*.test.ts` — src-side test files are NOT picked up; all tests go under tests/unit/ per package convention)

- [ ] Add `levelThresholds?: readonly number[]` to `MatchkeyField` (doc comment mirroring the Python semantics: descending cutoffs, level = count satisfied, length must equal `levels-1`).
- [ ] `parseMatchkeyField`: parse `levelThresholds` (loader camelizes `level_thresholds`) + validate exactly like Python schemas.py: length == levels-1, every value in (0,1], strictly descending — throw the loader's established error type with messages matching its style.
- [ ] `buildComparisonVector`: insert the custom branch BEFORE `n === 2` (after the null check): `let level = 0; for (const t of f.levelThresholds) if (s >= t) level += 1;`.
- [ ] `configToYaml`: emit `level_thresholds` back (verify the camelize/snake round-trip helper handles it automatically or add explicitly).
- [ ] Tests (port the Python vectors from tests/test_nlevel_banding.py + test_nlevel_schema.py): `[1.0,0.92,0.88]` over sims `[1.0,0.95,0.90,0.5,0.88]` → `[3,2,1,0,1]` (drive via buildComparisonVector with a stub scorer or exact values); legacy 2/3 unchanged; loader validation failures (wrong length / non-descending / out-of-range); YAML round-trip.
- [ ] Run: `npx vitest run src/core/nlevel.test.ts` + the existing probabilistic test file only. Commit.

### Task T2: `EMResult` JSON (de)serialization, Python-schema-compatible

**Files:**
- Modify: `src/core/probabilistic.ts` (EMResult interface ~31)
- Test: `tests/unit/em-serde.test.ts` (create)

- [ ] `emResultToJson(em: EMResult): object` and `emResultFromJson(data: object): EMResult` matching Python `EMResult.to_dict()`/`from_dict()` EXACTLY: `{"__type__": "goldenmatch.EMResult", "__version__": 1, "m_probs": {field: number[]}, "u_probs": ..., "match_weights": ..., "converged": bool, "iterations": int, "proportion_matched": float, "tf_freqs": null, "tf_collision": null}`. NOTE the Python field names are snake_case and keyed by FIELD NAME — check how TS `EMResult.m`/`u`/`matchWeights` are keyed (per-field record? array?) and map accordingly; reject `__version__ > 1` with a clear error (mirror Python's forward-compat message). TS `EMResult` has no TF fields: `emResultFromJson` must PRESERVE non-null `tf_freqs`/`tf_collision` through a round-trip (carry them on the returned object as optional fields, e.g. `tfFreqs?`/`tfCollision?` added to the interface) rather than silently dropping Python-trained TF tables — losing them would corrupt a Python-produced model file on TS re-save.
- [ ] `validateEmResultFor(em, matchkey)` port of Python `EMResult.validate_for` (every field has weights; length == field level count).
- [ ] Tests: round-trip TS→JSON→TS; cross-surface fixture — embed a JSON literal PRODUCED BY PYTHON (copy one from tests/test_from_splink_model_import.py's expected shape or generate once with the Python venv and paste as a const) and assert TS loads it; version-too-new rejection; validateFor mismatch cases.
- [ ] Run the new test file. Commit.

### Task T3: `fromSplink()` — the converter port

**Files:**
- Create: `src/core/config/from-splink.ts`
- Test: `tests/unit/from-splink.test.ts` (create; one file, sectioned describe blocks mirroring the six Python `test_from_splink_*.py` files; T4 vectors come from `test_cli_import_splink.py` + `test_mcp_splink_convert.py`)

Port `from_splink.py` faithfully — same recognizers, same warning/info/error findings (message text may be shared verbatim), same structure:
- [ ] `ConversionFinding`/`ConversionReport` (severity, splinkPath, message, mappedTo; hasWarnings/hasErrors/summary) + `SplinkConversionError`.
- [ ] `recognizeLevel(sql, isNullLevel)`: the anchored case-insensitive regexes (null / ELSE / exact / jaro_winkler_similarity|jaro_winkler / jaro_similarity (approx) / jaccard / levenshtein|damerau_levenshtein distance→similarity with `LEV_ASSUMED_LEN=10` + clamp, approx). Same column-atom rules (`"col_l"`/bare, l/r base names must match).
- [ ] `convertComparison`: null-skip + info, ELSE disagree, per-level warn on unrecognized, out-of-range band drop + warn, single-family rule (exact rides at 1.0; mixed non-exact families drop), column consistency, threshold dedupe/desc-sort, 2-level legacy shape vs levelThresholds, TF warnings (tf_adjustment_column same-column only, weight != 1 dropped), approx warnings WITH the conversion formula.
- [ ] `convertBlocking`: `_strip_outer_parens` port (balance-checked; strip whole rule, then per-conjunct), split on `/ AND /i` (case-INSENSITIVE literal-pattern regex, exactly Python's `re.split(r' AND ', ..., flags=re.IGNORECASE)` — no `\s+` (ReDoS), input whitespace-collapsed first; a plain `.split(" AND ")` would miss lowercase ` and `), `l."col"`/SUBSTR conjunct recognizers (SUBSTR(x,1,4) → `substring:0:4`; start<1 or len<1 rejected), non-string rule guard, field dedupe, mixed-rule widening WARNING, multi_pass (keys AND passes both set — check how TS BlockingConfig models passes; mirror the TS loader's shapes), zero-survivors → error finding + null.
- [ ] `importEm` + `detectTrained` + `convertScalars`: level-order reversal (Splink strongest-first → index N-1; ELSE → 0), collapsed-level SUM + warn, partial m/u epsilon (1e-6) + warn naming the missing side, re-normalize + warn, unassigned epsilon fill + warn, matchWeights = log2(m/u) floored 1e-10, proportionMatched default 0.05 + info, em_convergence/max_iterations/unique_id_column_name/link_type/infra-key findings, TF-tables-absent info.
- [ ] `fromSplink(source: object, opts?: {strict?: boolean}): SplinkConversion` — TS core is edge-safe: take the PARSED settings object only (no file reading in core; the CLI does file I/O). Strict raises on warnings+; default raises on errors; findings preview capped at 10; placeholder patching (`matchkeys[?].fields[?]` → real indices) with the shared-constant pattern.
- [ ] Tests: port the Python vectors — the 4-level JW fixture → field with levelThresholds [1.0,0.92,0.88]; pure-exact → 2-level; mixed families → null+warn; paren-wrapped Splink-4 conjuncts convert; `(a OR b)` → dropped; trained import exact-copy + reversal asserts; strict mode; zero comparisons/blocking → throws. Aim for the same edge coverage, not the same test count.
- [ ] Run per-file. Commit (split into 2-3 commits if natural: report+recognizers / comparison+blocking / model+fromSplink).

### Task T4: CLI + MCP + parity flip + docs

**Files:**
- Modify: `src/cli.ts` (new `program.command("import-splink")`), `src/node/mcp/server.ts` (TOOLS + handler), `parity/goldenmatch.yaml`, package exports if needed (`src/index.ts` or package.json exports — check how core/config modules are exported)
- Test: extend from-splink.test.ts or a node-side test file per package convention

- [ ] CLI `import-splink <input> [-o out.yaml] [--model-out m.json] [--strict]` mirroring the Python command exactly: reads JSON file (node side), calls fromSplink, findings table (match the CLI's existing output style — plain console, no rich), config-before-model write order, partial-model refusal, exit 1 on errors/strict, dropped-probabilities warning without --model-out. Export `fromSplink` from the package's public surface the same way other core/config APIs are exported.
- [ ] MCP tool `convert_splink_config` in TOOLS: `settings_json: string`, `strict?: boolean` → `{config_yaml, findings, summary, em_model, usage_note}` matching the Python tool's response shape (cross-surface parity); error convention per the TS server's existing tools.
- [ ] `parity/goldenmatch.yaml`: MOVE `convert_splink_config` from `mcp_tools.python_only` → `mcp_tools.shared`, `import-splink` from `cli_commands.python_only` → `cli_commands.shared` (keep list sorting).
- [ ] Docs: packages/typescript/goldenmatch/README.md (if it lists commands/features, add the converter; match tone), CHANGELOG if the TS package keeps one (check), docs-site TS/parity page if one mentions the converter as Python-only (grep docs-site for "import-splink").
- [ ] Tests: CLI command registered (`program.commands` contains it — same enumeration the parity emitter uses); MCP TOOLS contains the tool + handler happy path & error path.
- [ ] Run per-file vitest + `npx tsc --noEmit` IF it doesn't OOM (try once; if OOM, note it and rely on CI typecheck). Commit.

### Task T5: land it

- [ ] Push `feat/splink-converter-ts` (benzsevern token-URL), open PR (base main) noting: WASM no-op verdict (no WASM FS path exists), partialThreshold 0.7/0.8 pre-existing drift NOT changed, parity manifest flip, cross-surface EMResult JSON compatibility. `gh pr merge --auto`.
- [ ] CI validates the TS build + parity emitters (can't run locally). Watch the PR to merged (background watcher; fix CI failures if any — TS job is single non-matrix; PPRL tests have long timeouts; new test files don't shift Python shards).
