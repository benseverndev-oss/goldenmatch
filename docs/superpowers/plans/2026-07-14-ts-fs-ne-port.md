# TS FS Negative-Evidence Port Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port FS negative evidence to goldenmatch-js as a full mirror of Python #1764 — opening
with a loud decline that kills today's silent-wrong-scores state — then run the release train
(goldenmatch 3.3.0 / goldenmatch-js 1.3.0 / golden-suite 0.2.5).

**Spec:** `docs/superpowers/specs/2026-07-14-ts-fs-ne-port-design.md` — READ FIRST. Pins: the
decline-first sequencing, the loader-parsing gap (parseMatchkeyConfig DROPS negativeEvidence for
all three matchkey types today), the separate-NE-matrix trainEM shape, the storage-only clamp,
fallbackResult NE entries, and the permanent continuous-path decline.

**Architecture:** All FS work lives in `src/core/probabilistic.ts` (mirroring Python's single
module); loader/validation in `src/core/config/loader.ts` + `src/core/types.ts`. Decline lands
first (T1), capability lands module-by-module (T2-T5) lifting throws as it goes, cross-surface
parity + E2E close it out (T6).

**Tech Stack:** TypeScript (strict, `noUncheckedIndexedAccess` — index with `!` after guards, the
file's existing style), vitest (PER-FILE runs only — full runs OOM this box, exit 137; CI is the
authoritative full run), Python main venv for parity-fixture generation.

---

## Environment / repo mechanics (read before T0)

- NEW worktree `D:\show_case\gm-ts-ne`, branch `feat/ts-fs-ne` off freshly-fetched `origin/main`.
  **NEVER `git stash`.**
- TS commands from `D:\show_case\gm-ts-ne\packages\typescript\goldenmatch`:
  - Install once: `pnpm install` (worktree root or package — follow root CLAUDE.md pnpm notes;
    if node_modules already present from the checkout, skip).
  - Tests PER FILE: `npx vitest run tests/unit/<file>.test.ts` (NEVER a bare `npx vitest run` —
    OOMs). Typecheck `npx tsc --noEmit` MAY OOM (exit 137) — try once; if it dies, note it and
    rely on CI's typecheck lane (do NOT retry in a loop).
- Python (for fixture generation + cross-checking): main venv
  `D:/show_case/goldenmatch/.venv/Scripts/python.exe` with
  `PYTHONPATH="D:/show_case/gm-ts-ne/packages/python/goldenmatch" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`.
- Commit trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01R8MSaGwsjdxzf6Z7Bt3BXs`
- `docs/superpowers/` gitignored → `git add -f` spec + plan.
- Push/PR: `unset GH_TOKEN`; push via
  `git push "https://x-access-token:$(gh auth token --user benzsevern)@github.com/benseverndev-oss/goldenmatch.git" <branch>`;
  `GH_TOKEN=$(gh auth token --user benzsevern) gh pr create/merge --auto`; STOP after arming.

**Key existing code (paths under `packages/typescript/goldenmatch/`):**
- `src/core/probabilistic.ts` — `FSModelMismatchError` (line 69: the error idiom — `extends
  Error` with a set `name`), `emResultToJson` (82) / `emResultFromJson` (109) (generic dict
  passthrough — `__ne__` keys already round-trip), `validateEmResultFor` (175),
  `buildComparisonVector` (234), `trainEMContinuous` (424), `scoreProbabilisticContinuous`
  (592), `trainEM` (656: u from random pairs w/ +1e-6 smoothing; blocking-field neutral u;
  exponential m priors; E-step in log space; M-step m-only; trains on the random-pair matrix),
  `scoreProbabilistic` (834: hand-rolled min/max at 849-856, round-4 output),
  `scoreProbabilisticPair` (905: second hand-rolled block 914-921), `fallbackResult` (941).
- `src/core/types.ts` — `NegativeEvidenceField` (59: `penalty: number` REQUIRED today, no
  penaltyBits), matchkey types with `negativeEvidence?` (72/88/102), matchkey factory spreads
  (~580-650).
- `src/core/config/loader.ts` — `camelizeKeys` (134: generic snake→camel, handles
  `penalty_bits`→`penaltyBits` for free), `parseMatchkeyConfig` (342-384: builds fresh objects,
  DROPS negativeEvidence for all three types — THE bug).
- `src/core/scorer.ts` (~939) + `src/core/pipeline.ts` (~341) — weighted/exact NE scoring
  (UNTOUCHED by this port; read for the transform/scorer application idiom `neFired` should
  reuse — TS transforms live in `src/core/transforms.ts`).
- `tests/parity/` — committed-JSON fixture convention (e.g. `autoconfig-verify-fixtures.json`,
  `negative-evidence-fixtures.json` + `negativeEvidence.parity.test.ts` for weighted NE).
- Python reference (read side-by-side while porting): `packages/python/goldenmatch/goldenmatch/
  core/probabilistic.py` — `_ne_fired` (466), `_build_ne_matrix` (503), storage clamp (847-856),
  `estimate...`/train_em NE (~726-815, m init [0.05, 0.95]), `_fallback_result` NE (1454-1460:
  m [0.0625, 0.9375], u [0.5, 0.5], w [-3.0, 0.0]), `fs_weight_range` (1468), `validate_for`
  (294-313); `config/schemas.py` NE validation matrix (391-409).

## File structure

- Modify: `src/core/types.ts` (NegativeEvidenceField: penalty optional + penaltyBits)
- Modify: `src/core/config/loader.ts` (parse negativeEvidence ×3 types + validation matrix)
- Modify: `src/core/probabilistic.ts` (decline error, neFired, fsWeightRange, trainEM NE dims,
  scoring NE, validateEmResultFor, fallbackResult)
- Create: `tests/unit/fs-negative-evidence.test.ts` (decline, firing, weight range, EM, scoring,
  validation, fallback, serde pin, homonym E2E)
- Modify: `tests/unit/config-loader.test.ts` OR the loader's existing test file (locate:
  `grep -rln parseMatchkeyConfig tests/`) — NE parsing round-trips incl. weighted-via-YAML
- Create: `tests/parity/fs-negative-evidence-fixtures.json` +
  `tests/parity/fsNegativeEvidence.parity.test.ts`
- Modify: package `CHANGELOG.md` + `README.md` capability note (T6)

---

### Task T0: Worktree + baseline

- [ ] **Step 1:** `git fetch origin main -q && git worktree add /d/show_case/gm-ts-ne -b feat/ts-fs-ne origin/main` (from D:\show_case\goldenmatch).
- [ ] **Step 2:** Copy spec + plan into the worktree docs/superpowers/{specs,plans}; `git add -f`; commit `docs: spec + plan for the TS FS-NE port` (+ trailers).
- [ ] **Step 3:** `cd packages/typescript/goldenmatch`; ensure deps (`pnpm install` if node_modules absent — from the WORKTREE root if the workspace requires it; see root CLAUDE.md pnpm section). Baseline per-file runs: `npx vitest run tests/unit/probabilistic.test.ts` (locate the actual FS test file name first: `ls tests/unit | grep -i prob`) and `npx vitest run tests/parity/negativeEvidence.parity.test.ts` → green. If red, STOP.

### Task T1: Loader parsing + the loud decline (the spec's first-commit requirement)

**Files:** Modify `src/core/config/loader.ts`, `src/core/probabilistic.ts`; Test: new
`tests/unit/fs-negative-evidence.test.ts` + the loader test file.

- [ ] **Step 1: Failing tests.**
  - Loader: YAML/JSON snippets (mirror existing loader-test style) with `negative_evidence`
    lists on weighted, exact, AND probabilistic matchkeys → parsed config carries
    `negativeEvidence` with camelized fields (field/transforms/scorer/threshold/penalty). The
    weighted case is the pre-existing-bug regression test (today it parses to undefined).
  - Decline: `trainEM`, `scoreProbabilistic`, `scoreProbabilisticPair`, `validateEmResultFor`,
    `trainEMContinuous`, `scoreProbabilisticContinuous` each THROW on a probabilistic matchkey
    with non-empty `negativeEvidence` — error name `NegativeEvidenceUnsupportedError`, message
    contains the NE field name(s). Weighted/exact NE untouched (one guard test: weighted config
    with NE still scores via the existing scorer path).
  - The loaded-path decline: load a probabilistic+NE config via the LOADER, then call
    `scoreProbabilistic` with it → throws (pins that loader parsing + decline compose — the
    fan-out-lever YAML hazard).
- [ ] **Step 2:** Run per-file → FAIL (loader drops NE; no error class).
- [ ] **Step 3: Implement.**
  - loader.ts `parseMatchkeyConfig`: parse `negativeEvidence` (post-`camelizeKeys`) for all
    three types — pass through the camelized entries onto the constructed matchkey objects
    (respect the factory helpers in types.ts ~580-650 if the parser routes through them).
  - probabilistic.ts: `export class NegativeEvidenceUnsupportedError extends Error` (mirror
    FSModelMismatchError's shape at line 69, incl. `this.name = ...`), plus a module-private
    `function assertNoNegativeEvidence(mk: MatchkeyConfig, path: string): void` throwing when
    `mk.type === "probabilistic"` and `mk.negativeEvidence?.length`. Call it at the top of all
    six entry points, `path` naming the function ("trainEM", "scoreProbabilistic", ...).
- [ ] **Step 4:** Per-file runs → PASS (new file + loader file + the existing probabilistic and
  weighted-NE test files, each individually).
- [ ] **Step 5:** Commit `feat(goldenmatch-js): loud FS negative-evidence decline + loader NE parsing` (+ trailers).

### Task T2: Types + validation matrix

**Files:** Modify `src/core/types.ts`, `src/core/config/loader.ts` (or validate.ts if the
matchkey validation seam lives there — locate where invalid matchkey configs currently throw,
`grep -rn "throw" src/core/config/loader.ts | head`); Test: extend both test files.

- [ ] **Step 1: Failing tests.** The Python matrix (schemas.py 391-409), all via the loader:
  weighted/exact NE without `penalty` → throws naming penalty; weighted/exact with
  `penalty_bits` → throws naming penaltyBits as probabilistic-only; probabilistic with
  `penalty` → throws pointing at penaltyBits; probabilistic with `penalty_bits` → parses
  (`penaltyBits` set); probabilistic with NEITHER → parses (EM-learned shape); `penalty_bits`
  range check if Python has one (schemas.py: penalty ge=0 le=1; penalty_bits is unconstrained
  float — mirror exactly, verify by reading).
  ALSO in the matrix (reviewer-caught gap): **any NE entry with `derive_from`/`deriveFrom` →
  loud loader rejection** ("derive_from negative evidence is not supported in goldenmatch-js").
  Python's fan-out lever can emit derive_from NE, and TS has NO derived-column materialization —
  without this rejection a loaded derive_from NE would pass validation, `neFired` would read a
  missing column, and NE would silently never fire (the exact failure mode this port kills).
  Mirror Python's NE `threshold` [0,1] constraint (schemas.py:216) while here.
- [ ] **Step 2:** FAIL. **Step 3:** types.ts: `penalty?: number` + `penaltyBits?: number` on
  `NegativeEvidenceField` (update the factory spreads if they enumerate NE fields); loader:
  per-type checks + the deriveFrom rejection + threshold range where matchkeys are parsed.
  While in the loader's exact branch: it currently returns `{name, type, fields}` only and also
  DROPS the exact matchkey's `threshold` (types.ts:75) — carry it (one line + a test; an
  exact+NE+threshold YAML silently loses its threshold today). Update the stale
  `ProbabilisticMatchkey.negativeEvidence` doc comment (types.ts:100-101 "unused at scoring
  time" — becomes false after T4; fix it now with a forward note or in T4). **Step 4:**
  per-file runs PASS — including the pre-existing weighted NE tests (penalty now optional at
  the TYPE level but still required by the matrix for weighted/exact, so no behavior change).
  **Step 5:** Commit `feat(goldenmatch-js): NE penalty/penaltyBits validation matrix` (+ trailers).

### Task T3: `neFired` + `fsWeightRange`

**Files:** Modify `src/core/probabilistic.ts`; Test: extend `tests/unit/fs-negative-evidence.test.ts`.

- [ ] **Step 1: Failing tests.**
  - `neFired`: fires (both present, sim < threshold via each supported scorer incl. transforms
    applied — reuse the weighted-NE scorer application idiom from scorer.ts:939); not-fired at
    sim == threshold (STRICT — use exact scorer, identical values, threshold 1.0); null either
    side; empty-after-transform (e.g. `"-"` with `digitsOnly` if the TS transform exists —
    check `src/core/transforms.ts`; else empty string directly).
  - `fsWeightRange`: regular-only equals the old hand-rolled result (compute both in-test on a
    fixture EM); `__ne__` entry extends min; `penaltyBits` contributes `(-abs, 0)`; missing
    `__ne__` entry defensively skipped.
- [ ] **Step 2:** FAIL. **Step 3:** Implement both exported functions mirroring Python
  (`_ne_fired` 466, `fs_weight_range` 1468); REPLACE the two hand-rolled min/max blocks
  (scoreProbabilistic 849-856, scoreProbabilisticPair 914-921) with `fsWeightRange` calls —
  byte-identical for non-NE configs (the regular-field part keeps the same
  reduce-min/reduce-max semantics incl. the `!w || w.length === 0` skip). Scoring throws stay
  (lifted in T4). **Step 4:** Per-file runs PASS incl. the existing probabilistic tests (the
  swap must not move any existing score). **Step 5:** Commit
  `feat(goldenmatch-js): neFired + fsWeightRange (NE-aware normalization envelope)` (+ trailers).

### Task T4: Scoring + validation + fallback

**Files:** Modify `src/core/probabilistic.ts`; Test: extend the test file.

- [ ] **Step 1: Failing tests** (these call the still-throwing entry points, so they FAIL with
  the decline error — that's the expected failure mode):
  - scoreProbabilistic/Pair on NE-bearing mk + hand-built EMResult with `__ne__` entries:
    fired pair drops by exactly `wFired` pre-normalization (assert via hand-computed normalized
    score); unfired pair identical to the no-NE score; penaltyBits override; normalized ∈ [0,1]
    when NE fires (fsWeightRange envelope); the round-4 convention applies to
    `scoreProbabilistic` ONLY — `scoreProbabilisticPair` returns raw floats on both surfaces
    (do NOT add rounding to Pair).
  - validateEmResultFor: NE mk + model missing `__ne__<field>` → FSModelMismatchError-style
    error naming the field and BOTH remedies (retrain / set penaltyBits); penaltyBits field
    needs no entry; 1-entry `__ne__` list rejected (2-entry required).
  - fallbackResult: NE fields get `matchWeights [-3.0, 0.0]`, `m [0.0625, 0.9375]`,
    `u [0.5, 0.5]`; penaltyBits NE fields get NO entries.
  - Serde pin: emResultToJson→FromJson round-trips `__ne__` keys exactly (snake_case JSON keys
    preserved — read how the serde maps matchWeights↔match_weights first).
- [ ] **Step 2:** FAIL (decline throws). **Step 3:** Implement: lift the throws from
  scoreProbabilistic/Pair + validateEmResultFor; add the per-pair NE contribution (helper
  `neContribution(rowA, rowB, ne, em)` mirroring `_ne_scalar_contribution`: 0 unless fired;
  `-abs(penaltyBits)` or `matchWeights["__ne__"+field][0]`); extend validateEmResultFor;
  extend fallbackResult. **Step 4:** Per-file PASS. **Step 5:** Commit
  `feat(goldenmatch-js): FS scoring + validation + fallback cover negative evidence` (+ trailers).

### Task T5: trainEM NE dims

**Files:** Modify `src/core/probabilistic.ts`; Test: extend the test file.

- [ ] **Step 1: Failing tests** (fail with the trainEM decline):
  - Trained result has `__ne__<field>` entries: m/u are 2-lists summing to 1 (±1e-9);
    `matchWeights[__ne__][1] === 0.0` exactly (storage clamp); `[0]` negative on a fixture
    where matches share the NE value and non-matches differ.
  - penaltyBits NE fields produce NO `__ne__` entries.
  - **Storage-only clamp probe (the Python-pinned subtlety):** on a small deterministic
    fixture, regular-field m values match a hand-replicated EM that uses FULL NE likelihood in
    the E-step (replicate 2 iterations in-test with plain JS math) — i.e. the clamp must NOT
    have leaked into training. This mirrors Python's exact-probe tests.
  - NE event encoding: null/empty rows count as NOT-fired (state 1) in the NE matrix (observable
    via u: an all-null NE column trains uFire ≈ smoothing floor).
- [ ] **Step 2:** FAIL. **Step 3:** Implement: build the NE matrix SEPARATELY (a
  `number[][]`/Int8-like of 0=fired/1=not-fired over the same sampled pairs, via `neFired`);
  NE u from the same uMatrix sample pairs; NE m init `[0.05, 0.95]` (Python's init); include NE
  dims in the E-step log-likelihood + M-step exactly like regular fields (full likelihood);
  AFTER convergence write storage: `matchWeights["__ne__"+f] = [log2(mFire/uFire), 0.0]`
  (epsilon-guard the ratio the way regular weights are guarded), m/u 2-lists. Lift the trainEM
  throw. Blocking-field neutralization skips NE dims. Continuous entry points KEEP their
  throws (pin with a test comment "permanent — mirrors Python").
- [ ] **Step 4:** Per-file PASS (new + all existing probabilistic tests). **Step 5:** Commit
  `feat(goldenmatch-js): trainEM learns NE dims (separate matrix, storage-only clamp)` (+ trailers).

### Task T6: Cross-surface parity + homonym E2E + docs

**Files:** Create `tests/parity/fs-negative-evidence-fixtures.json` +
`tests/parity/fsNegativeEvidence.parity.test.ts`; extend `tests/unit/fs-negative-evidence.test.ts`;
Modify package `CHANGELOG.md`, `README.md`.

- [ ] **Step 1: Generate fixtures with Python** (main venv + worktree PYTHONPATH): a script (run
  from scratchpad, NOT committed) builds a small deterministic dataset + probabilistic config
  with (a) EM-learned NE and (b) penaltyBits NE, trains via Python `train_em`, scores a fixed
  pair list via `score_pair_probabilistic`/`comparison_vector` scalar path, and dumps
  {config, em_model (to_dict), rows, expected_pair_scores} to the fixtures JSON (follow the
  existing parity-fixture JSON shape — read `negative-evidence-fixtures.json` first). Run the
  generator with `GOLDENMATCH_FS_CALIBRATED` and `GOLDENMATCH_FS_MONOTONIC` UNSET (defaults) —
  the posterior-calibration branch in `score_pair_probabilistic` (~2456) and the monotone
  post-processing in `train_em` (~866) would both desync the fixtures from TS. No derive_from
  NE in fixtures (TS rejects it by design, T2).
- [ ] **Step 2: Parity test:** TS loads the JSON (config through the LOADER — exercising T1/T2),
  `emResultFromJson` the model, scores the same pairs via `scoreProbabilisticPair` → equality
  to full float precision (the PR #1755 probe standard; if round-4 conventions differ between
  the two scalar paths, compare pre-round values — investigate, don't loosen).
- [ ] **Step 3: Homonym E2E** (unit file): the #1764 bar shape — traps merge without NE,
  separate with NE (EM-learned AND penaltyBits variants), true dups still merge. Keep the
  fixture small (TS scalar path).
- [ ] **Step 4:** Docs: CHANGELOG entry (unreleased/1.3.0 section) + README capability note
  (NE now scored on FS matchkeys; continuous path declines). WASM: no FS path — add the
  documented-no-op line to the CHANGELOG entry (PR #1755 precedent).
- [ ] **Step 5:** Full per-file sweep: every test file touched or adjacent
  (fs-negative-evidence, the loader file, existing probabilistic, negativeEvidence.parity,
  fsNegativeEvidence.parity, plus `grep -rln "scoreProbabilistic\|trainEM" tests/ | head` —
  run each individually). Try `npx tsc --noEmit` once (OOM → note for CI). Commit
  `test(goldenmatch-js): FS-NE cross-surface parity + homonym E2E` (+ trailers).

### Task T7: Final review + PR

- [ ] **Step 1:** Final whole-branch review (superpowers final-review stage; BASE = origin/main
  at branch point).
- [ ] **Step 2:** Push (auth dance); PR
  `feat(goldenmatch-js): FS negative evidence (loud decline -> full mirror of #1764)`; body:
  spec summary + the loader-drop pre-existing bug + parity/E2E evidence + "CI is the
  authoritative full vitest/typecheck run (box OOMs)". Arm `gh pr merge --auto`, STOP.

### Task T8: Release train (controller-run SOP after T7 merges — spec section 6)

- [ ] goldenmatch 3.3.0: release-prep PR bumping pyproject.toml + `goldenmatch/__init__.py` +
  CHANGELOG + `server.json` (version-consistency gate checks all four). Merge → tag `v3.3.0`
  → publish workflow owns the release → verify PyPI JSON shows 3.3.0.
- [ ] goldenmatch-js 1.3.0: bump package.json + CHANGELOG in the same or a sibling prep PR; tag
  `goldenmatch-js-v1.3.0` (NEVER unprefixed) → npm publish workflow (full vitest runs
  pre-publish — a flake blocks; fix + bump patch + re-tag, no retry) → verify npm.
- [ ] golden-suite 0.2.5: AFTER goldenmatch 3.3.0 is live on PyPI — floor bumps
  (goldenmatch>=3.3, goldenmatch-native>=0.1.15), version sync (pyproject + __init__ +
  server.json), merge, tag, verify PyPI.
- [ ] MCP registry auto-syncs via publish-mcp; verify listings. Update memory
  (`project_fs_negative_evidence`, `project_splink_converter`) + work tracker; docs-site sweep
  (rollout-docs-sweep skill) for the release notes.
