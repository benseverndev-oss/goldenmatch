# TS FS Negative-Evidence Port (Phase 3: loud decline + full mirror)

**Date:** 2026-07-14
**Status:** Approved (design)
**Thesis phase:** Higher-language surface (phase 3) for FS negative evidence. Followed
operationally by the release train (goldenmatch 3.3.0 / goldenmatch-js 1.3.0 / golden-suite
0.2.5) -- SOP, documented in the closing section, not designed here.
**Predecessors:**
- FS negative evidence core (`specs/2026-07-14-fs-negative-evidence-design.md`, PR #1764) -- the
  semantics being mirrored; its out-of-scope list deferred the TS surface with the standing note
  "TS phase 3 must OPEN with a loud NE decline".
- Native port (`specs/2026-07-14-native-fs-ne-port-design.md`, PR #1775, goldenmatch-native
  0.1.15) -- phase 2, shipped.
- The Splink-converter TS port (PR #1755) -- the phase-3 playbook precedent (faithful port,
  Python-schema-v1 serde, cross-surface parity probes, WASM documented no-op).

## Problem

TS FS scoring silently mis-scores NE-bearing probabilistic matchkeys. `NegativeEvidenceField`
exists in `types.ts` (weighted/exact NE scoring works), and `emResultToJson`/`FromJson`
round-trip `__ne__<field>` model entries losslessly -- but `trainEM`, `scoreProbabilistic`,
`scoreProbabilisticPair`, and `validateEmResultFor` have zero NE awareness. The hand-rolled
min/max weight ranges iterate `mk.fields` only, so a Python-authored NE config+model consumed in
TS scores WITHOUT the veto and without any warning: silent wrong scores, the worst failure mode.
The fan-out migration lever (PR #1771) now emits NE-bearing configs routinely, making this a
live cross-surface hazard rather than a theoretical one.

## Decisions (from brainstorming)

- **Scope: FULL MIRROR** of Python #1764 (EM training of NE dims + scoring + validate_for +
  schema validation matrix), chosen over scoring-only-with-permanent-training-decline. Keeps the
  Py<->TS API-parity discipline honest and TS self-sufficient.
- **Approach A: loud-decline-first, then port, ONE PR.** The branch's first commit makes every
  TS FS entry point throw a named error on NE-bearing probabilistic matchkeys, killing the
  silent-wrong-scores state TDD-style; subsequent commits port the capability and lift the
  throws, except on the permanently-unsupported continuous path. Rejected: two PRs (ships a
  remedy-less hard throw for a release cycle); port-without-decline (nothing pins the
  permanently-unsupported paths, mid-branch states silently mis-score).

## 1. Loud decline (first commit; partially permanent)

New error `NegativeEvidenceUnsupportedError` (name/shape matched to the package's existing error
idiom by the implementer -- if the package uses plain `Error` with message conventions, follow
that instead of a class). Thrown when the probabilistic matchkey carries `negativeEvidence`
(non-empty) by:

- `trainEM` and `scoreProbabilistic` / `scoreProbabilisticPair` (lifted later in the branch as
  the port lands),
- `validateEmResultFor` (lifted later),
- `trainEMContinuous` / `scoreProbabilisticContinuous` (PERMANENT -- Python's continuous/Winkler
  path rejects NE too).

Message names the NE field(s) and states that FS negative evidence is not scored on this path.
Weighted/exact NE behavior is untouched throughout.

## 2. Schema / loader validation matrix (mirrors config/schemas.py)

- `NegativeEvidenceField` (types.ts): `penalty` becomes optional; new `penaltyBits?: number`.
- Per-matchkey-type validation (wherever the package validates matchkey configs -- follow the
  existing validation seam, e.g. `validate.ts` / the config loader):
  weighted/exact REQUIRE `penalty` and REJECT `penaltyBits`; probabilistic REJECTS `penalty` and
  accepts `penaltyBits` or NEITHER (the EM-learned shape). Errors name the offending knob and
  the correct one -- no silent no-op knobs.
- **Loader parsing is NEW, not a mapping tweak (reviewer-verified gap):** `parseMatchkeyConfig`
  (`src/core/config/loader.ts:342-384`) builds fresh matchkey objects and silently DROPS
  `negativeEvidence` for ALL THREE types today -- weighted NE via YAML is also lost, and the
  section-1 loud decline would never fire on loaded configs without this. The port adds
  `negativeEvidence` parsing for all three matchkey types (the generic `camelizeKeys` already
  handles `penalty_bits -> penaltyBits` key conversion) **in or before the first commit**, so
  the decline covers the real ingestion path (the fan-out lever's YAML output), with a test on
  the loaded-config path specifically. The weighted/exact loader fix is deliberately in scope
  (same parser change; add a weighted-NE-via-YAML round-trip test). There is also no per-type NE
  validation anywhere in TS today (`validate.ts` has zero NE references) -- the validation
  matrix is a NEW seam, not an edit.

## 3. Core NE machinery (mirrors core/probabilistic.py, same file `src/core/probabilistic.ts`)

- **`neFired(rowA, rowB, ne)`**: fires iff both values present post-transform AND non-empty
  (empty string = inconclusive, the deliberate NE null-handling) AND scorer similarity STRICTLY
  `<` `ne.threshold` -- using the same transform/scorer machinery `buildComparisonVector` uses.
- **`fsWeightRange(em, mk)`**: the normalization envelope -- regular fields
  (`sum(min)`/`sum(max)` over `em.matchWeights[f.field]`) plus NE contributions
  (`penaltyBits` set -> `(-abs(penaltyBits), 0)`; else min/max over
  `matchWeights["__ne__<field>"]`, defensively skipped when absent). REPLACES both hand-rolled
  min/max blocks in `scoreProbabilistic` and `scoreProbabilisticPair` -- the structural fix for
  the silent-ignore bug. Byte-identical to the old computation for non-NE configs.
- **`trainEM`**: NE dims join as constrained 2-state dimensions carried in a SEPARATE NE matrix
  (Python's `_build_ne_matrix` shape -- comparison-matrix consumers assume
  `len(row) == len(mk.fields)`, so NE columns must NOT be appended to the comparison matrix) --
  event encoding 0 = fired / 1 = not-fired (INCLUDING nulls/empties); u for NE dims from the
  same random-pair sample as regular u; m via the same EM loop using FULL likelihood internally;
  the clamp is STORAGE-ONLY: `matchWeights["__ne__<field>"] = [wFired, 0.0]`,
  `m["__ne__<field>"] = [mFire, 1 - mFire]`, `u[...] = [uFire, 1 - uFire]` (getting the clamp
  into the E-step biases m -- Python pinned this with exact probes; TS tests do the same).
  `penaltyBits` NE fields are excluded from EM entirely. Blocking-field neutralization does not
  apply to NE dims. The tiny-dataset `fallbackResult` writes the same NE entries Python's
  `_fallback_result` writes: `matchWeights = [-3.0, 0.0]`, `m = [0.0625, 0.9375]`,
  `u = [0.5, 0.5]` per penaltyBits-free NE field.
- **Scoring** (`scoreProbabilistic` + `scoreProbabilisticPair`): after the regular-field sum,
  add per NE field `wFired` when fired (from the `__ne__` entry, or `-abs(penaltyBits)`), else
  EXACTLY 0; normalize against `fsWeightRange`. Round-4 output convention unchanged.
- **`validateEmResultFor`**: a probabilistic matchkey with NE fields requires a 2-entry
  `matchWeights["__ne__<field>"]` list for each NE field WITHOUT `penaltyBits`; missing -> error
  naming the field and the two remedies (retrain, or set penaltyBits) -- Python's
  `FSModelMismatchError` message shape.
- Serde (`emResultToJson`/`FromJson`) needs NO change (generic dict passthrough already
  round-trips `__ne__` keys) -- pinned by a test rather than assumed.

## 4. Testing / parity

- Unit mirrors of the Python test families: schema validation matrix (penalty x penaltyBits x
  matchkey types); firing/encoding (fired / not-fired / null / empty-after-transform /
  transforms applied); EM (`__ne__` m/u 2-lists sum to 1; penaltyBits fields absent from EM;
  storage-only clamp pinned with exact numeric probes); scoring (NE contribution when fired,
  exactly 0 otherwise; penaltyBits override; normalized stays in [0,1] when NE fires);
  validation errors (dual-remedy message); fallbackResult NE entries; serde `__ne__`
  passthrough pin.
- **Cross-surface parity (the load-bearing test), `tests/parity/`**: a Python-authored config +
  Python-trained model (fixture generated via the Python package and committed, following the
  existing parity-fixture convention in that directory) with EM-learned NE and with penaltyBits
  NE, scored in TS -- pair scores equal Python's to full float precision (the PR #1755 probe
  standard). The serde round-trip alone is no longer sufficient; the SCORES must agree.
- **Homonym E2E in TS**: the #1764 success-bar shape -- distinct people sharing name+city,
  differing on phone; FS without NE merges the traps, with NE (EM-learned and penaltyBits
  variants) separates them while true dups still merge.
- Continuous-path throws pinned. Loud-decline first commit is itself TDD'd (tests assert the
  throws before any capability exists).
- Box constraint: vitest per-file only (full runs OOM this machine); CI is the authoritative
  full run. PPRL-style CI timeout allowances are not expected to be needed (pure compute, small
  fixtures).

## 5. Surfaces

- No new CLI/MCP commands: `api.ts`/pipeline already route probabilistic scoring through these
  functions, so NE support arrives transparently. The parity manifest (`parity/goldenmatch.yaml`)
  gains no entries; the API-parity gate must stay green.
- WASM: no FS scoring path exists in WASM -- documented no-op (the PR #1755 precedent).
- Docs: package README/CHANGELOG note the capability; the docs-site sweep rides the release
  rollout.

## 6. The release train (operational follow-through, after this PR merges)

Not a design item -- the established SOP, recorded for execution:

- **goldenmatch 3.3.0** (PyPI): carries #1760 (upgrade pass), #1764 (FS-NE core), #1771
  (fan-out lever), #1775's Python-side native-NE consumers. Bump pyproject.toml +
  `goldenmatch/__init__.py` + CHANGELOG + server.json in one release-prep PR (the
  version-consistency gate checks all four).
- **goldenmatch-js 1.3.0** (npm): this port. Tag `goldenmatch-js-v1.3.0` (never an unprefixed
  tag for TS).
- **golden-suite 0.2.5** (PyPI): floors goldenmatch>=3.3, goldenmatch-native>=0.1.15 (native
  already published). Members land on registries FIRST, then the suite floor bump.
- Let the publish workflows create their own releases (pre-created releases are immutable to
  asset uploads); publish-mcp auto-syncs the registry; verify from the PUBLISHED artifacts
  (PyPI JSON, npm, registry), not git tags.

## Out of scope

- WASM FS/NE kernels; TS vectorized/native scoring lanes (TS has one scalar FS path).
- TS ports of the fan-out lever / splink_upgrade (separate feature, not started in TS).
- Continuous-path NE (permanently declined, matching Python).
- Corrections-based NE refinement; autoconfig-time NE promotion on probabilistic matchkeys.
- A supervised (labels-based) NE trainer: Python has `estimate_m_from_labels` with NE handling;
  TS has no supervised trainer at all, so there is nothing to mirror -- not a completeness gap.
- Docs-site (Mintlify) sweep -- rides the release rollout.
