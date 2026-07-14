# FS Negative Evidence (Formulation B) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `negative_evidence` works on probabilistic matchkeys: each NE field is an EM-learned constrained dimension contributing `log2(m_fire/u_fire)` when it FIRES (both present + `scorer < threshold`) and exactly 0 otherwise, with a `penalty_bits` fixed override — killing the homonym/fan-out snowball failure mode for FS configs (incl. every Splink-converted config).

**Architecture:** Extend `train_em` with appended NE dimensions stored as `__ne__<field>` entries in the existing EMResult dicts (schema-v1-transparent, cross-surface free). Scoring paths (scalar/vectorized/batch/bucket) add the fired-else-zero contribution; weight-range normalization is CENTRALIZED into one helper replacing six hand-rolled sites. Native/fused/fast paths decline NE matchkeys (capability-gate playbook). `validate_for` fails loudly on models lacking NE parameters.

**Tech Stack:** Pure Python (thesis phase 1). No new modules — all changes ride existing files, so the zero-polars import gates are not at risk (still: never add a top-level `import polars`; the lazy proxy is the law).

**Spec:** `docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md` (worktree — READ FIRST; every mechanism is pinned there with verified code anchors).

**Working branch:** `feat/fs-negative-evidence` (worktree `..\goldenmatch-wt-splink-converter`, off current main). Package root: `packages/python/goldenmatch/`.

**Env (Windows box):**
```powershell
cd D:\show_case\goldenmatch-wt-splink-converter\packages\python\goldenmatch
$env:PYTHONPATH = "D:\show_case\goldenmatch-wt-splink-converter\packages\python\goldenmatch"
$env:POLARS_SKIP_CPU_CHECK = "1"; $env:PYTHONIOENCODING = "utf-8"
D:\show_case\goldenmatch\.venv\Scripts\python.exe -m pytest tests/<file> -v
```
Targeted test files ONLY. NEVER `git stash`. Pre-push: ruff (0.15.12) + pyright 1.1.409 (config/ + core are strict CI surfaces — but check pyrightconfig include for core/; write strict-clean regardless). The worktree has an in-tree native build — do NOT set `GOLDENMATCH_NATIVE=0` on final runs so native-gated tests exercise the real gates.

**Verified anchors (from spec review):** weighted-NE firing is STRICT `<` (`core/scorer.py:292`, `backends/score_buckets.py:942`); `NegativeEvidenceField` `config/schemas.py:204` (validator `_validate_transforms_and_scorer` ~:225); matchkey validator `_validate_weighted` `schemas.py:341-369`; `train_em` matrix build `core/probabilistic.py:537-640` (int8 matrix `:435`); monotone repair `skip_fields` hook `:153`; weight-range hand-rolled at `probabilistic.py` ~1305, ~1563, ~1675, ~1966, ~2040 + `core/probabilistic_fast.py` ~79; `validate_for` `:272`; `_fs_native_eligible` `:1897`; `match_fused_fs_ready` `core/fused_match.py:255`; bucket slim projection `backends/score_buckets.py:583-604`; `derive_from` materialization is already type-agnostic (`core/matchkey.py:366-401` + frame twin `:448-479`); `train_em_continuous` `:989`.

---

## File structure (no new modules)

- `goldenmatch/config/schemas.py` — NegativeEvidenceField (`penalty` optional, `penalty_bits` new, validator matrix), MatchkeyConfig validator, stale-doc removal.
- `goldenmatch/core/probabilistic.py` — NE event encoding + EM dimensions, scoring contributions, centralized weight-range helper, validate_for, native gate, monotone skip, continuous rejection.
- `goldenmatch/core/probabilistic_fast.py` — gate decline.
- `goldenmatch/core/fused_match.py` — ready-gate decline.
- `goldenmatch/backends/score_buckets.py` — slim-projection keep-list + bucket-path NE contribution (VERIFY in N3 whether the bucket path routes through score_probabilistic or scores independently — if independent, it needs the same contribution logic or must route to the non-bucket path for NE matchkeys; investigate and pick, mirroring how level_thresholds was handled there if at all).
- Tests: `tests/test_fs_ne_schema.py`, `tests/test_fs_ne_em.py`, `tests/test_fs_ne_scoring.py`, `tests/test_fs_ne_guards.py`, `tests/test_fs_ne_e2e.py` (all new).

---

### Task N1: schema surface

**Files:** Modify `goldenmatch/config/schemas.py`; create `tests/test_fs_ne_schema.py`.

- [ ] **Failing tests (TDD):**
  - probabilistic matchkey + NE field with `penalty_bits=3.0` (no `penalty`) → validates.
  - probabilistic + NE with `penalty=0.4` → ValidationError naming `penalty_bits` ("probabilistic matchkeys use EM-learned weights; set penalty_bits to override").
  - weighted + NE WITHOUT `penalty` → ValidationError (existing requirement now enforced in the matchkey validator).
  - weighted + NE with `penalty_bits` → ValidationError (weighted rejects it).
  - exact + NE with `penalty=0.4` → validates (byte-unchanged).
  - probabilistic + NE with NEITHER penalty nor penalty_bits → validates (EM-learned default).
  - `penalty_bits=-2` → validates (abs taken at scoring; or reject negatives — pick: ACCEPT any float, document abs; test pins it).
  - Existing YAML round-trip: a weighted-NE config dict from the current test suite still parses identically.
- [ ] **Implement:** `NegativeEvidenceField.penalty: float | None = Field(default=None, ge=0.0, le=1.0)`; add `penalty_bits: float | None = None` with doc comment (log2 LLR override, probabilistic-only, abs() applied, fires-else-zero). Per-type rules go in `MatchkeyConfig._validate_weighted` (it already branches on type): weighted/exact NE entries require `penalty is not None` + reject `penalty_bits`; probabilistic NE entries reject `penalty` + allow `penalty_bits` None-or-float. REMOVE the stale v1.13 comment block (`schemas.py:315-327`) incl. the `GOLDENMATCH_NE_FS_ESCAPE_MODE` line; replace with 3 lines pointing at the new spec.
- [ ] Run `tests/test_fs_ne_schema.py tests/test_config.py` → PASS (if any existing test constructed weighted NE relying on schema-level required `penalty` error TYPE, adjust expectations only if the error still raises — it must, just from the matchkey validator now).
- [ ] **Commit** `feat(schemas): negative_evidence on probabilistic matchkeys + penalty_bits`.

### Task N2: EM learns NE dimensions

**Files:** Modify `goldenmatch/core/probabilistic.py`; create `tests/test_fs_ne_em.py`.

- [ ] **Failing tests:**
  - `_ne_fired(row_a, row_b, ne_field)` unit: both present + `score < threshold` → True; score == threshold → False (STRICT); either side null/empty → False; transforms applied before scoring.
  - `train_em` on a ~200-row fixture (planted duplicate pairs where the NE field — phone — agrees within true matches, plus cross-entity name collisions where phone differs): result contains `m_probs["__ne__phone"]`/`u_probs["__ne__phone"]`/`match_weights["__ne__phone"]`; m/u lists are length 2 [fired, not_fired] summing to ~1; `match_weights["__ne__phone"] == [log2(m0/u0), 0.0]` — the not-fired weight is EXACTLY 0.0 (the clamp), NOT log2(m1/u1); w_fired < 0 on this fixture (matches rarely fire).
  - `penalty_bits` NE field → NO `__ne__` entries in the result (excluded from EM).
  - NE field also a blocking field → a warning is logged (`caplog`) naming the field + degeneracy.
  - Monotone repair — BOTH modes tested: `enforce` (monkeypatched) → `__ne__` entries untouched; `warn` (default) with a contrived positive w_fired → no detection message names the __ne__ key. Implementation: UNION the __ne__ keys with the existing `skip_fields=blocking_fields` at both call sites (probabilistic.py:691, :878; hook signature `skip_fields: list[str] | None` at :151-154).
  - Regular-field results unchanged by adding an NE field (compare m_probs for regular fields with/without NE present, same seed — EM posteriors WILL shift because NE evidence enters the E-step; so assert instead: without-NE run is byte-identical to pre-change behavior via an existing test staying green, and document that adding NE legitimately changes posteriors).
- [ ] **Implement:**
  - `_ne_fired(...)` helper reusing `apply_transforms` + `score_field`, strict `<`.
  - **row_lookup projection (stall-preventer):** `train_em` builds `row_lookup` from
    `cols = [f.field for f in mk.fields ...]` (probabilistic.py:543-546) — NE-ONLY fields (the
    canonical phone case) are NOT in it, so `_ne_fired` would see missing keys → treated as null →
    NE never fires in EM → degenerate m/u. EXTEND the select with NE field names + `derive_from`
    synthesized names.
  - `_build_comparison_matrix` / `comparison_vector` extension: NE dims appended AFTER regular fields — cleanest: a parallel `_build_ne_matrix(pairs, row_lookup, mk) -> np.ndarray (n_pairs x n_ne)` of {0=fired, 1=not_fired} int8, rather than overloading comparison_vector's contract (its consumers assume len == len(mk.fields)); train_em consumes both matrices.
  - `train_em`: u for NE dims from the same random-pair matrix; m initialized (prior: fired rare in matches — e.g. [0.05, 0.95]) and updated in the SAME E/M loop (the E-step per-pair log-likelihood sums must include NE dims). Blocking-field neutralization does NOT apply to NE dims. `penalty_bits` fields skipped entirely. After convergence: `match_weights["__ne__<f>"] = [log2(max(m0,1e-10)/max(u0,1e-10)), 0.0]`.
  - Blocking-overlap warning: compare NE field names against `blocking_fields` param + (when blocks came from config) the blocking key fields — emit `logger.warning` once per offending field.
  - Monotone repair: pass `skip_fields={k for k in weights if k.startswith("__ne__")}` via the existing hook.
  - `_fallback_result` (DECIDED): emits a fixed conservative `w_fired = -3.0` bits entry
    (`match_weights["__ne__<f>"] = [-3.0, 0.0]`, m/u backfilled consistently) per
    non-penalty_bits NE field, WITH a logged warning — keeps the pipeline runnable on tiny data
    instead of tripping validate_for. Pin with a test.
- [ ] Run `tests/test_fs_ne_em.py tests/test_probabilistic.py tests/test_nlevel_em.py` → PASS (existing suites prove no-NE behavior unchanged).
- [ ] **Commit** `feat(probabilistic): EM-learned NE dimensions (__ne__ entries, strict-< firing)`.

### Task N3: scoring + centralized weight range + bucket path

**Files:** Modify `goldenmatch/core/probabilistic.py`, `goldenmatch/backends/score_buckets.py`; create `tests/test_fs_ne_scoring.py`.

- [ ] **Investigate first (report in commit):** how the DEFAULT bucket backend scores probabilistic matchkeys — does `score_buckets.py` route through `score_probabilistic`/the vectorized scorer (then only the slim-projection keep-list needs fixing) or does it have its own scoring? Also how `level_thresholds` reached the bucket path (it did — the parity tests pass on the default backend). Mirror that route.
- [ ] **Failing tests:**
  - Centralized range helper `fs_weight_range(em, mk) -> (min_weight, max_weight)`: unit test with regular + NE + penalty_bits fields (penalty_bits contributes `-abs(bits)` to min, 0 to max; missing `__ne__` entry with penalty_bits set is fine; regular fields contribute min/max of their weight lists).
  - Scalar scoring: a pair where NE fires → total = regular sum + w_fired; not fired → regular sum exactly; null on one side → regular sum exactly.
  - `penalty_bits=3.0` → fired contribution == -3.0 even with no `__ne__` entry in em.
  - Scalar vs vectorized parity: same block, NE-bearing matchkey → identical pair sets + scores (mirror TestNativeFSParity's idiom).
  - Normalized scores stay in [0,1] when NE fires at the extremes (all-fire pair at min weight).
  - Bucket/default-backend: dedupe_df on a small df with an NE-ONLY field (phone not in mk.fields) → NE demonstrably fires (pair that would merge without NE does not merge with it) — THIS is the slim-projection pin; run WITHOUT env overrides (default backend, native enabled — native declines NE so it falls back pure-Python; that's fine and separately pinned in N4).
  - Continuous path: `train_em_continuous`/`score_probabilistic_continuous` with NE present → clear error (`NotImplementedError` or ValueError naming the limitation).
- [ ] **Implement:**
  - `fs_weight_range(em, mk)` in probabilistic.py; REPLACE the hand-rolled sums at probabilistic.py ~1305, ~1563, ~1675, ~1966, ~2040 and probabilistic_fast.py ~79 (the native-prep sites ~1966/~2040 feed the kernel — since native DECLINES NE matchkeys, those sites see no NE fields, but centralizing them anyway prevents drift; verify kernel args unchanged for non-NE configs via existing native parity tests).
  - Scalar path: after the regular-field weight sum, loop `mk.negative_evidence or []`: penalty_bits → `-abs(bits)` if `_ne_fired` else 0; else `em.match_weights["__ne__"+f.field][0]` if fired else 0.
  - Vectorized path: per NE field build the similarity matrix via the existing `_field_score_matrix_dedup` machinery + null masks → fired mask (`sim < threshold` & both-present) → add `w_fired * fired_mask` to the total-weight matrix. Same for the batch scorer if it's a distinct code path (check ~1675 site).
  - Bucket: slim-projection keep-list extended with NE field names + `derive_from` sources + synthesized names (`score_buckets.py:583-604`); plus whatever the Step-1 investigation says about the scoring route.
  - Continuous: reject NE at entry with a clear message.
- [ ] Run `tests/test_fs_ne_scoring.py tests/test_fs_ne_em.py tests/test_probabilistic.py tests/test_probabilistic_vectorized.py tests/test_nlevel_banding.py` → PASS.
- [ ] **Commit** `feat(probabilistic): NE scoring contributions + centralized fs_weight_range + bucket keep-list`.

### Task N4: guards + validate_for

**Files:** Modify `goldenmatch/core/probabilistic.py`, `goldenmatch/core/probabilistic_fast.py`, `goldenmatch/core/fused_match.py`; create `tests/test_fs_ne_guards.py`.

- [ ] **Failing tests:**
  - `_fs_native_eligible` False for NE-bearing matchkey (synthetic supporting-native mock — reuse the level_thresholds test scaffolding in tests/test_nlevel_banding.py); plain matchkey unaffected; REAL kernel test (skipif) also declines.
  - Router selects the non-native scorer for NE matchkeys with native mocked available.
  - `match_fused_fs_ready` False on NE (mirror test_fused_match.py's gate tests).
  - `_resolve_probabilistic_fast_path` declines NE.
  - `validate_for`: model WITHOUT `__ne__phone` + matchkey with NE phone (no penalty_bits) → FSModelMismatchError naming the field + both remedies; WITH penalty_bits → passes without the key; model WITH the key → passes; wrong length (not 2) → error.
- [ ] **Implement:** one-line gate additions following each gate's existing style (docstrings updated: "NE never crosses the FFI; future kernel port adds FS_SUPPORTS_NE"); `validate_for` extension per spec.
- [ ] Run `tests/test_fs_ne_guards.py tests/test_fused_match.py tests/test_nlevel_banding.py` (native enabled) → PASS.
- [ ] **Commit** `feat(probabilistic): NE guards -- native/fused/fast decline + validate_for`.

### Task N5: E2E success bar + docs + back-compat sweep

**Files:** Create `tests/test_fs_ne_e2e.py`; modify CHANGELOG, docs-site, the 2026-05-21 investigation doc.

- [ ] **The success-bar test (deterministic, DEFAULT backend, native enabled):** synthetic fixture — N true duplicate pairs (name+city+phone agree) PLUS homonym traps (distinct people sharing name+city, DIFFERENT phone). Config A: probabilistic matchkey on name+city (no NE) → assert the homonyms MERGE (the failure). Config B: same + phone as NE → assert homonyms SEPARATE and true duplicates STILL MERGE. Drive via `dedupe_df`. Make the fixture surname-soundex-diverse (project rule). If Config A doesn't merge homonyms naturally, strengthen the name/city evidence (more agreeing fields) until it does — the test must demonstrate the DELTA, not a vacuous pass; document the fixture reasoning in its docstring.
  - Same test parametrized with `penalty_bits` instead of EM-learned → same qualitative outcome.
- [ ] **Docs:** CHANGELOG Unreleased (NE on FS, Formulation B, EM-learned, penalty_bits, guards); `docs/superpowers/specs/2026-05-21-ne-fs-investigation.md` gets a SUPERSEDED header block pointing at the new spec ("Formulation B implemented 2026-07-14 with EM-learned parameters; the labeled-data deferral rationale was stale"); docs-site scoring.mdx (NE section: now valid on probabilistic, semantics, penalty_bits) + configuration.mdx if it documents negative_evidence; grep docs-site for "weighted + exact" NE claims and fix.
- [ ] **Back-compat sweep:** run the EXISTING weighted/exact NE tests untouched (`grep -rln "negative_evidence" tests/ | head` → run those files) + `tests/test_fs_autoconfig_v2.py tests/test_from_splink_api.py tests/test_splink_upgrade_levers.py` → all green, zero edits to existing NE tests.
- [ ] **Commit** `test(probabilistic): FS-NE homonym success bar + docs`.

### Task N6: land it

- [ ] Pre-push: `ruff check packages/python/goldenmatch`; pyright (repo config); the full new-test set + regression files in ONE invocation; confirm NO new module-level polars imports (`git diff origin/main..HEAD | grep -n "^+import polars\|^+from polars"` → empty).
- [ ] Push (benzsevern token-URL), PR titled `feat: negative evidence on Fellegi-Sunter matchkeys (Formulation B, EM-learned)` with the homonym-fixture evidence + the supersession story in the body, `gh pr merge --auto`, background-watch to merged; fix CI reds (lessons: zero-polars gates, ruff import sorts, pyright strict on config//core, shard timeouts).
