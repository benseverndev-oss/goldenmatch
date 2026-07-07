# InferMap WASM/TS Wave C — `profile` + `pattern_type` Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the final two TS scorers (`ProfileScorer`, `PatternTypeScorer`) to the `infermap-wasm` backend — completing the six-kernel WASM/TS surface — with `pattern_type` collapsing a third regex engine (JS `RegExp`) onto the Rust `regex` reference.

**Architecture:** `profile` is scalar per-pair (Wave 3 kernel: host keeps avg-length + abstain + reasoning, kernel does the 10-scalar math). `pattern_type` is a per-field bitmask (Wave 4 kernel): `classifyWithPct` refactors to get a per-sample bitmask from the kernel and aggregate host-side — the Wave 4 Python refactor ported to TS. Parity gates assert kernel==pure; `pattern_type`'s is the sharpest drift audit of the surface.

**Tech Stack:** Rust (`infermap-wasm` wasm-bindgen), TS/vitest, the `infermap_wasm` CI lane.

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-wasm-wave-c-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Constraints (READ FIRST)

**Repo:** `D:\show_case\gg-local-llm`, branch `feat/infermap-wasm-wave-c` (stacked on Wave B's branch tip — the crate, backend/loader, parity test, and CI lane already have Wave A + Wave B content). Wave B (#1507) merges to main in parallel; Task 7 rebases onto fresh `origin/main`.

**THE BOX CAN RUN ALMOST NOTHING.** No `cargo`/`wasm-pack`/`wasm-bindgen` (Rust CI-only); no `vitest`/`tsc`/`tsup`/`pnpm build` (TS OOM, CI-only). Box CAN do: `node --check` (NOT `.ts`), `git`, `grep`/read, YAML validate via python, eye-review. **Every task is write-against-spec + eye-verify + commit; CI is the first real test.**

**Files you extend (read each first):**
- `packages/rust/extensions/infermap-wasm/src/lib.rs` — `#[cfg(target_arch="wasm32")] mod wasm` now holds `detect_domain_json` + 3 Wave B name shims.
- `packages/typescript/infermap/src/core/wasm/{backend.ts,loader.ts}` — `InfermapBackend` has `detectDomain` + 3 Wave B methods.
- `packages/typescript/infermap/src/core/scorers/profile.ts`, `pattern-type.ts`.
- `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts` — ends with the Wave B name block; module-scope has `d`, `existsSync`, `enable/disableInfermapWasm`, `getInfermapBackend`, vitest imports incl `afterAll`.
- `.github/workflows/ci.yml` — `infermap_wasm` filter (~line 242; Wave B added the 3 scorer paths after `detect.ts`).

**Kernel signatures (verified):**
- `infermap_core::profile_score(src_dtype: &str, tgt_dtype: &str, src_null: f64, tgt_null: f64, src_uniq: f64, tgt_uniq: f64, src_val_count: f64, tgt_val_count: f64, src_avg_len: f64, tgt_avg_len: f64) -> f64`
- `infermap_core::pattern_match_types(samples: &[String]) -> Vec<u32>`

**Git:** benzsevern (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue — `--auto --squash`, no `--delete-branch`. Trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

---

## File Structure

| File | Change | Action |
| --- | --- | --- |
| `packages/rust/extensions/infermap-wasm/src/lib.rs` | +2 `#[wasm_bindgen]` shims | Modify |
| `packages/typescript/infermap/src/core/wasm/backend.ts` | +2 interface methods | Modify |
| `packages/typescript/infermap/src/core/wasm/loader.ts` | +2 glue-type entries + adapter methods | Modify |
| `packages/typescript/infermap/src/core/scorers/profile.ts` | `_profileScorePure` export + dispatch | Modify |
| `packages/typescript/infermap/src/core/scorers/pattern-type.ts` | `_matchTypesPure` + `classifyWithPct` bitmask refactor + dispatch | Modify |
| `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts` | +2 parity blocks | Modify |
| `.github/workflows/ci.yml` | +2 scorer paths in `infermap_wasm` filter | Modify |

---

## Task 1: Crate — profile_score + pattern_match_types shims (CI-only)

**Do NOT run cargo.** Write against spec, verify by grep/eye, commit. CI compiles.

**Files:** Modify `packages/rust/extensions/infermap-wasm/src/lib.rs`.

- [ ] **Step 1: Read the file**; locate `#[cfg(target_arch = "wasm32")] mod wasm { ... }` (holds `detect_domain_json` + the 3 Wave B shims `exact_score`/`fuzzy_name_score`/`initialism_score`). Confirm `infermap_core::{profile_score, pattern_match_types}` exist with the signatures above (grep `infermap-core/src/lib.rs`).

- [ ] **Step 2: Add two shims** inside `mod wasm`, after the `initialism_score` shim (before the module's closing `}`):
```rust
    #[allow(clippy::too_many_arguments)]
    #[wasm_bindgen]
    pub fn profile_score(
        src_dtype: &str,
        tgt_dtype: &str,
        src_null: f64,
        tgt_null: f64,
        src_uniq: f64,
        tgt_uniq: f64,
        src_val_count: f64,
        tgt_val_count: f64,
        src_avg_len: f64,
        tgt_avg_len: f64,
    ) -> f64 {
        infermap_core::profile_score(
            src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
            src_val_count, tgt_val_count, src_avg_len, tgt_avg_len,
        )
    }

    #[wasm_bindgen]
    pub fn pattern_match_types(samples: Vec<String>) -> Vec<u32> {
        infermap_core::pattern_match_types(&samples)
    }
```
`#[allow(clippy::too_many_arguments)]` is required (10 args > 7 under `-D warnings`). Nothing else changes (no Cargo.toml edits — `regex` is already an `infermap-core` dep from Wave 4, linked via the path dep).

- [ ] **Step 3: Verify by eye (NO cargo)**
```bash
grep -n "mod wasm\|#\[wasm_bindgen\]\|#\[allow(clippy::too_many_arguments)\]\|pub fn profile_score\|pub fn pattern_match_types\|pub fn initialism_score" packages/rust/extensions/infermap-wasm/src/lib.rs
```
Confirm: both new fns INSIDE `mod wasm`, after `initialism_score`, before the module's closing `}`; `profile_score` has the `#[allow]` + 10 args in the exact kernel order; `pattern_match_types` takes `Vec<String>` returns `Vec<u32>` and calls `&samples`. Read the file tail to confirm the `mod wasm` closing brace is after `pattern_match_types`.

- [ ] **Step 4: Commit**
```bash
cd "D:/show_case/gg-local-llm"
git add packages/rust/extensions/infermap-wasm/src/lib.rs
git commit -m "feat(infermap-wasm): profile_score + pattern_match_types shims (Wave C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, grep output, SHA.

---

## Task 2: Backend interface + loader adapter

**Files:** Modify `.../wasm/backend.ts`, `.../wasm/loader.ts`. Box: eye-review.

- [ ] **Step 1: Extend `InfermapBackend`** in `backend.ts` — add two methods (keep `detectDomain` + the 3 Wave B methods):
```ts
  profileScore(
    srcDtype: string,
    tgtDtype: string,
    srcNull: number,
    tgtNull: number,
    srcUniq: number,
    tgtUniq: number,
    srcValCount: number,
    tgtValCount: number,
    srcAvgLen: number,
    tgtAvgLen: number,
  ): number;
  patternMatchTypes(samples: string[]): number[];
```

- [ ] **Step 2: Extend `loader.ts`** — add two entries to the `glue` cast type + two methods to the returned adapter (keep the existing ones verbatim):
```ts
    // ...in the glue cast type, after the Wave B entries:
    profile_score: (
      srcDtype: string, tgtDtype: string,
      srcNull: number, tgtNull: number, srcUniq: number, tgtUniq: number,
      srcValCount: number, tgtValCount: number, srcAvgLen: number, tgtAvgLen: number,
    ) => number;
    pattern_match_types: (samples: string[]) => Uint32Array;
```
```ts
    // ...in the returned adapter object, after the Wave B methods:
    profileScore: (sd, td, sn, tn, su, tu, sv, tv, sl, tl) =>
      glue.profile_score(sd, td, sn, tn, su, tu, sv, tv, sl, tl),
    patternMatchTypes: (samples) => Array.from(glue.pattern_match_types(samples)),
```
> `Vec<u32>` marshals to `Uint32Array`; `Array.from` → `number[]` matches the
> interface + the pure `_matchTypesPure` return. `index.ts` unchanged.

- [ ] **Step 3: Verify by eye.** Interface + glue type + adapter each gained 2 entries; `profileScore` has 10 params in kernel order; `patternMatchTypes` glue returns `Uint32Array`, adapter wraps `Array.from`. `git diff` and read it.

- [ ] **Step 4: Commit**
```bash
git add packages/typescript/infermap/src/core/wasm/backend.ts packages/typescript/infermap/src/core/wasm/loader.ts
git commit -m "feat(infermap-ts): profile + pattern_type backend methods + loader adapters (Wave C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA.

---

## Task 3: `profile.ts` — `_profileScorePure` export + dispatch (byte-identical)

**Files:** Modify `scorers/profile.ts`. Box: eye-review. Existing `tests/unit/profile-scorer.test.ts` must stay green (CI).

**Context:** Current `score` computes `total` via 5 inline weighted adds. Extract that math into an exported `_profileScorePure(10 scalars)` (the parity oracle + pure fallback, single source), and route `total` through the backend when set.

- [ ] **Step 1: Read `profile.ts`.** Note the exact reasoning `parts` push order + `fmt2` usage.

- [ ] **Step 2: Add the import** (after existing imports):
```ts
import { getInfermapBackend } from "../wasm/backend.js";
```

- [ ] **Step 3: Add the exported oracle** (above the `ProfileScorer` class; keep `avgValueLength`/`similarity`/`fmt2`):
```ts
/** Pure five-add profile math — the single source for the pure fallback AND the
 *  WASM parity oracle. Byte-identical to infermap-core::profile_score. Caller owns
 *  the abstain (valueCount===0), avg-length reduction, and reasoning. */
export function _profileScorePure(
  srcDtype: string,
  tgtDtype: string,
  srcNull: number,
  tgtNull: number,
  srcUniq: number,
  tgtUniq: number,
  srcValCount: number,
  tgtValCount: number,
  srcAvgLen: number,
  tgtAvgLen: number,
): number {
  let total = 0;
  total += 0.4 * (srcDtype === tgtDtype ? 1 : 0);
  total += 0.2 * similarity(srcNull, tgtNull);
  total += 0.2 * similarity(srcUniq, tgtUniq);
  const maxLen = Math.max(srcAvgLen, tgtAvgLen, 1);
  total += 0.1 * (1 - Math.abs(srcAvgLen - tgtAvgLen) / maxLen);
  const srcCard = srcUniq * srcValCount;
  const tgtCard = tgtUniq * tgtValCount;
  const maxCard = Math.max(srcCard, tgtCard, 1);
  total += 0.1 * (1 - Math.abs(srcCard - tgtCard) / maxCard);
  return total;
}
```
> Verify the arithmetic matches the current inline body OP-FOR-OP: `0.4*dtype +
> 0.2*sim(null) + 0.2*sim(uniq) + 0.1*lenSim + 0.1*cardSim`, same `max(...,1)`
> floors, same `similarity` (already defined in the file). If the current file
> orders/computes differently, MATCH the current file.

- [ ] **Step 4: Rewrite `ProfileScorer.score`** to keep abstain + avg-length + reasoning host-side, routing `total` through the oracle/backend:
```ts
  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    if (source.valueCount === 0 || target.valueCount === 0) return null;

    const srcLen = avgValueLength(source.sampleValues);
    const tgtLen = avgValueLength(target.sampleValues);

    const backend = getInfermapBackend();
    const total = backend
      ? backend.profileScore(
          source.dtype, target.dtype,
          source.nullRate, target.nullRate,
          source.uniqueRate, target.uniqueRate,
          source.valueCount, target.valueCount,
          srcLen, tgtLen,
        )
      : _profileScorePure(
          source.dtype, target.dtype,
          source.nullRate, target.nullRate,
          source.uniqueRate, target.uniqueRate,
          source.valueCount, target.valueCount,
          srcLen, tgtLen,
        );

    // Reasoning stays host: recompute the parts (as today).
    const dtypeMatch = source.dtype === target.dtype ? 1 : 0;
    const nullSim = similarity(source.nullRate, target.nullRate);
    const uniqSim = similarity(source.uniqueRate, target.uniqueRate);
    const maxLen = Math.max(srcLen, tgtLen, 1);
    const lenSim = 1 - Math.abs(srcLen - tgtLen) / maxLen;
    const srcCard = source.uniqueRate * source.valueCount;
    const tgtCard = target.uniqueRate * target.valueCount;
    const maxCard = Math.max(srcCard, tgtCard, 1);
    const cardSim = 1 - Math.abs(srcCard - tgtCard) / maxCard;
    const parts = [
      `dtype=${dtypeMatch ? "match" : "mismatch"}`,
      `null_sim=${fmt2(nullSim)}`,
      `uniq_sim=${fmt2(uniqSim)}`,
      `len_sim=${fmt2(lenSim)}`,
      `card_sim=${fmt2(cardSim)}`,
    ];
    return makeScorerResult(total, `Profile comparison: ${parts.join(", ")}`);
  }
```
> The `parts` array must reproduce the current push order + exact strings
> (`dtype=match|mismatch`, `null_sim=`, `uniq_sim=`, `len_sim=`, `card_sim=`) with
> `fmt2`. Confirm against the current file; match it if it differs.

- [ ] **Step 5: Byte-identity verify.** `git diff packages/typescript/infermap/src/core/scorers/profile.ts`. Confirm: pure branch = `_profileScorePure(...)` whose math == the old inline `total`; reasoning `parts` byte-identical (order + strings + `fmt2`); abstain unchanged; `avgValueLength`/`similarity`/`fmt2` untouched.

- [ ] **Step 6: Commit**
```bash
git add packages/typescript/infermap/src/core/scorers/profile.ts
git commit -m "refactor(infermap-ts): profile scorer dispatch + _profileScorePure oracle (Wave C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, byte-identity confirmation.

---

## Task 4: `pattern-type.ts` — bitmask refactor + dispatch (byte-identical)

**Files:** Modify `scorers/pattern-type.ts`. Box: eye-review. Existing `tests/unit/pattern-type-scorer.test.ts` must stay green (CI).

**Context:** Current `classifyWithPct` loops the 8 regexes inline per sample. Refactor to the Wave 4 bitmask form: a per-sample bitmask (from the kernel or the JS regexes) + host-side popcount aggregation. Byte-identical output.

- [ ] **Step 1: Read `pattern-type.ts`.** Note `SEMANTIC_TYPES` (a `Record<string,RegExp>`, 8 entries insertion-ordered), `cleanSamples`, the current `classifyWithPct` loop, `classifyField`, `PatternTypeScorer.score`.

- [ ] **Step 2: Add the import** (after existing imports):
```ts
import { getInfermapBackend } from "../wasm/backend.js";
```

- [ ] **Step 3: Add `_matchTypesPure` + `_matchTypesBatch`** (above `classifyWithPct`):
```ts
/** Bitmask oracle: bit i set iff s matches SEMANTIC_TYPES[i] (insertion order).
 *  s is expected pre-stripped by the caller. Mirrors the Rust pattern_match_types. */
export function _matchTypesPure(s: string): number {
  let mask = 0;
  let i = 0;
  for (const pattern of Object.values(SEMANTIC_TYPES)) {
    if (pattern.test(s)) mask |= 1 << i;
    i++;
  }
  return mask;
}

function _matchTypesBatch(stripped: string[]): number[] {
  const backend = getInfermapBackend();
  return backend
    ? backend.patternMatchTypes(stripped)
    : stripped.map(_matchTypesPure);
}
```

- [ ] **Step 4: Rewrite `classifyWithPct`** to the bitmask form (keep the signature + return shape):
```ts
function classifyWithPct(
  field: FieldInfo,
  threshold = 0.6,
): { type: string | null; pct: number } {
  const samples = cleanSamples(field.sampleValues).map((s) => String(s).trim());
  if (samples.length === 0) return { type: null, pct: 0 };

  const masks = _matchTypesBatch(samples);
  const names = Object.keys(SEMANTIC_TYPES);
  let bestType: string | null = null;
  let bestPct = 0;
  for (let i = 0; i < names.length; i++) {
    let matches = 0;
    for (const m of masks) if (m & (1 << i)) matches++;
    const pct = matches / samples.length;
    if (pct > bestPct) {
      bestPct = pct;
      bestType = names[i]!;
    }
  }
  if (bestType !== null && bestPct >= threshold) {
    return { type: bestType, pct: bestPct };
  }
  return { type: null, pct: 0 };
}
```
> Byte-identity: current code does `cleanSamples(...)` then per-pattern
> `pattern.test(String(s).trim())`; new code pre-maps `String(s).trim()` into
> `samples` then bitmasks — same match input, same `samples.length`, same per-type
> independent count (a sample's multiple bits == matching multiple regexes), same
> strict-`>` best over `Object.keys` insertion order, same threshold. Leave
> `SEMANTIC_TYPES`, `cleanSamples`, `classifyField`, `PatternTypeScorer.score`,
> `pctStr` UNCHANGED.

- [ ] **Step 5: Byte-identity verify.** `git diff packages/typescript/infermap/src/core/scorers/pattern-type.ts`. Confirm: `classifyWithPct` output identical for all inputs (match input, count, selection, threshold); `_matchTypesPure` iterates `Object.values(SEMANTIC_TYPES)` in order (bit i == type i); `classifyField`/`PatternTypeScorer.score`/`SEMANTIC_TYPES`/`cleanSamples` untouched.

- [ ] **Step 6: Commit**
```bash
git add packages/typescript/infermap/src/core/scorers/pattern-type.ts
git commit -m "refactor(infermap-ts): pattern_type bitmask dispatch + _matchTypesPure oracle (Wave C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, byte-identity confirmation.

---

## Task 5: Parity gate — profile + pattern_type drift audits

**Files:** Modify `tests/parity/infermap-wasm.parity.test.ts`. Box: eye-review (skips locally; runs in CI).

- [ ] **Step 1: Add imports** near the top (after existing imports):
```ts
import { _profileScorePure } from "../../src/core/scorers/profile.js";
import { _matchTypesPure } from "../../src/core/scorers/pattern-type.js";
```

- [ ] **Step 2: Append two `d(...)` blocks** at the END of the file (after the Wave B name block's `});`):
```ts

// ---------------------------------------------------------------------------
// Wave C: profile scorer parity (float math)
// ---------------------------------------------------------------------------

// 10-tuple: (srcDtype, tgtDtype, srcNull, tgtNull, srcUniq, tgtUniq,
//            srcValCount, tgtValCount, srcAvgLen, tgtAvgLen)
type ProfileCase = [string, string, number, number, number, number, number, number, number, number];
const PROFILE_CASES: ProfileCase[] = [
  ["string", "string", 0.1, 0.1, 0.5, 0.5, 100, 100, 8, 8],      // identical -> 1.0
  ["string", "int", 0.1, 0.1, 0.5, 0.5, 100, 100, 8, 8],         // dtype mismatch -> -0.4
  ["string", "string", 0.0, 0.0, 0.0, 0.0, 1, 1, 0, 0],          // max(...,1) floors
  ["string", "string", 0.0, 0.0, 0.5, 0.5, 100, 100, 0, 8],      // one empty-sample side
  ["string", "string", 0.0, 0.0, 0.01, 0.02, 10, 10, 4, 4],      // tiny cards floor
  ["string", "string", 0.0, 1.0, 0.5, 0.5, 100, 100, 8, 8],      // lopsided null
  ["string", "string", 0.1, 0.1, 1.0, 0.0, 100, 100, 8, 8],      // lopsided uniqueness
  ["string", "string", 0.1, 0.1, 0.5, 0.5, 100, 100, 3, 30],     // asymmetric lengths
  ["string", "int", 0.13, 0.87, 0.42, 0.58, 250, 90, 12.5, 7.25],// realistic mixed
];

d("infermap profile-scorer WASM-vs-pure parity", () => {
  afterAll(() => disableInfermapWasm());
  for (let i = 0; i < PROFILE_CASES.length; i++) {
    const args = PROFILE_CASES[i]!;
    it(`profile case ${i}: kernel == pure`, async () => {
      await enableInfermapWasm({ require: true });
      const be = getInfermapBackend()!;
      expect(be.profileScore(...args)).toBe(_profileScorePure(...args));
      disableInfermapWasm();
    });
  }
});

// ---------------------------------------------------------------------------
// Wave C: pattern_type scorer parity (JS RegExp vs Rust regex — the drift audit)
// ---------------------------------------------------------------------------

// ASCII-digit corpus (mirrors the Python Wave 4 corpus). The only non-ASCII
// codepoints are the £/€ currency symbols, matched as literal char-class members.
const PATTERN_SAMPLES: string[] = [
  "user@example.com", "a@b.co", "no-at-sign.com", "user@nodot",
  "550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-44665544000",
  "2026-07-06", "2026-13-99", "2026-7-6",           // date_iso (2026-07-06 also matches phone)
  "192.168.0.1", "999.999.999.999", "1.2.3", "1.2.3.4.5",
  "http://example.com", "https://example.com/path?q=1", "ftp://example.com",
  "+12345678", "123-456-7890", "12345", "1234567890123456",
  "12345-6789", "1234", "123456",
  "$5", "$ 5", "$1,000.00", "£12.50", "€1,000.00", "5.00",
  "hello world",
];

d("infermap pattern-type-scorer WASM-vs-pure parity", () => {
  afterAll(() => disableInfermapWasm());
  for (const s of PATTERN_SAMPLES) {
    it(`pattern_type ${JSON.stringify(s)}: kernel == pure`, async () => {
      await enableInfermapWasm({ require: true });
      const be = getInfermapBackend()!;
      // single-element bitmask array; compare element 0.
      expect(be.patternMatchTypes([s])[0]).toBe(_matchTypesPure(s));
      disableInfermapWasm();
    });
  }
});
```
> `.toBe` exact equality on both — profile is fixed-order float math; pattern_type
> is the bitmask. A pattern_type divergence on an ASCII sample is the drift audit
> (JS RegExp vs Rust regex) — meant to red if they disagree.

- [ ] **Step 3: Verify by eye.**
```bash
grep -n "_profileScorePure\|_matchTypesPure\|profile-scorer WASM\|pattern-type-scorer WASM" packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts
grep -n "export function _profileScorePure" packages/typescript/infermap/src/core/scorers/profile.ts
grep -n "export function _matchTypesPure" packages/typescript/infermap/src/core/scorers/pattern-type.ts
```
Confirm: both oracle imports resolve to real exports (Tasks 3/4); the two `d(...)` blocks are AFTER the Wave B block's `});`; `afterAll`/`d`/`enable`/`disable`/`getInfermapBackend` are module-scope (already, from Wave A/B); `.js` extensions. `PROFILE_CASES` has NO empty-valueCount case (the scorer guards abstain before the kernel; `_profileScorePure` doesn't guard, and the kernel is only called post-abstain — so all cases use nonzero counts, which they do).

- [ ] **Step 4: Commit**
```bash
git add packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts
git commit -m "test(infermap-ts): profile + pattern_type WASM-vs-pure parity (Wave C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, grep confirmations, SHA.

---

## Task 6: CI filter — retrigger on the 2 scorer changes

**Files:** Modify `.github/workflows/ci.yml`. Box: YAML-validate.

- [ ] **Step 1: Add two scorer paths** to the `infermap_wasm` filter. Find the `- 'packages/typescript/infermap/src/core/scorers/initialism.ts'` line (Wave B added it inside the `infermap_wasm:` filter) and add after it (same 14-space indent):
```yaml
              - 'packages/typescript/infermap/src/core/scorers/profile.ts'
              - 'packages/typescript/infermap/src/core/scorers/pattern-type.ts'
```
Add ONLY these 2 lines.

- [ ] **Step 2: Validate YAML**
```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml YAML OK')"
grep -n "scorers/profile.ts\|scorers/pattern-type.ts" .github/workflows/ci.yml
```
Expect: `ci.yml YAML OK`; the 2 lines present, adjacent to the other scorer filter entries (a few lines below the `infermap_wasm:` FILTER key ~242, NOT the top-level output ~88).

- [ ] **Step 3: Commit**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(infermap): retrigger infermap_wasm lane on profile/pattern-type changes (Wave C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, YAML-OK, grep, SHA.

---

## Task 7: Rebase + push + PR + arm + monitor the drift lane (controller runs this)

**Files:** none.

- [ ] **Step 1: Rebase onto fresh origin/main** (Wave B #1507 should be merged by now; this branch is stacked on Wave B, so rebasing onto main drops the now-merged Wave B commits and keeps ONLY the Wave C commits):
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q
# Wave B tip = 10651ced (the commit feat/infermap-wasm-wave-c was branched from).
# --onto replays ONLY the Wave-C commits (everything after 10651ced) onto main.
git rebase --onto origin/main 10651ced feat/infermap-wasm-wave-c
```
`10651ced` is the tip of `feat/infermap-wasm-wave-b` this branch was cut from — everything after it is Wave-C-only. First confirm Wave B is on main (`git log origin/main --oneline | grep -i "wave b\|name scorer"`); if Wave B is NOT yet merged, wait and retry — do NOT rebase onto a main that lacks Wave B (the parity test + backend would lose the Wave B methods). Resolve any `ci.yml`/backend/loader/parity-test conflicts by KEEPING BOTH waves' additions. Re-validate YAML after.

> Simpler alternative if the `--onto` boundary is fuzzy: once Wave B is on main,
> `git rebase origin/main` and let git drop the already-merged (squashed) Wave B
> changes; resolve conflicts keeping both. Then verify the three-dot diff (Step 2)
> shows ONLY Wave C files — if Wave B files appear, the rebase base was wrong.

- [ ] **Step 2: Confirm three-dot diff is clean**
```bash
git diff --stat origin/main...HEAD
```
Expect ONLY Wave C files (spec, plan, `infermap-wasm/src/lib.rs`, backend.ts, loader.ts, profile.ts, pattern-type.ts, parity test, ci.yml). If Wave B files (detect.ts, the name scorers, wasm scaffold) appear, STOP — the rebase base was wrong.

- [ ] **Step 3: Push**
```bash
git push -u origin feat/infermap-wasm-wave-c
```

- [ ] **Step 4: Open the PR**
```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(infermap): WASM/TS Wave C — profile + pattern_type (final scorers)" \
  --body "$(cat <<'EOF'
## What

Wave C completes the InferMap WASM/TS surface: wires the last two scorers to the `infermap-wasm` backend. Six kernels (`detect_domain` + all five scorers) now run the same Rust `infermap-core` code on Python (native wheel), the Rust FFI, and TS (WASM).

- **profile**: scalar per-pair (Wave 3 kernel) — host keeps avg-length + abstain + reasoning; kernel does the 10-scalar math. `_profileScorePure` is the single-source oracle + pure fallback.
- **pattern_type**: per-field bitmask (Wave 4 kernel) — `classifyWithPct` refactored to the bitmask form; the kernel returns a per-sample bitmask, host aggregates. Byte-identical pure path.

## Parity = the sharpest drift audit of the surface

`pattern_type` collapses a THIRD independent regex engine (JS `RegExp`) onto the Rust `regex` reference. The gate asserts `backend.patternMatchTypes([s])[0] === _matchTypesPure(s)` over an ASCII-digit corpus. The three engines genuinely differ off-ASCII (JS `\d` = `[0-9]` vs Rust `\d` = Unicode `\p{Nd}`), but converge on ASCII — so a must-pass red is a real finding: WASM (== Rust == Python) is the reference. `profile` parity is fixed-order float math (low risk).

## Scope

`profile` byte-identical unconditionally; `pattern_type` byte-identical iff the gate is green. `alias`/`llm` stay host-bound — this is the last scorer wave.

Spec: `docs/superpowers/specs/2026-07-06-infermap-wasm-wave-c-design.md`
Plan: `docs/superpowers/plans/2026-07-06-infermap-wasm-wave-c.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```

- [ ] **Step 5: Arm auto-merge, then monitor the drift lane ONCE**
```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
Do NOT `--delete-branch`. Then launch ONE background monitor of the `infermap_wasm` lane (as in Waves A/B) that captures the failing parity cases if it reds. A `pattern_type` red on an ASCII sample is a **real drift finding** (JS RegExp vs Rust regex genuinely disagree) — capture WHICH sample + the expected/received bitmask, report the divergence + resolution (WASM is the reference; document/fix the pure TS), don't paper over. A green lane means all three regex engines agree on ASCII and the six-kernel surface is byte-parity complete. Report the PR number + drift verdict, then STOP.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Crate shims compile | Rust build in the lane | CI (Task 1) |
| Backend/loader typecheck | tsc/tsup | CI (Tasks 2-4) |
| profile pure path byte-identical | `profile-scorer.test.ts` stays green | CI (Task 3) |
| pattern_type pure path byte-identical | `pattern-type-scorer.test.ts` stays green | CI (Task 4) |
| **profile: kernel == pure** | 9 float 10-tuples, `.toBe` | CI lane (Task 5) |
| **pattern_type: kernel == pure** | ASCII corpus bitmask, `.toBe` | CI lane (Task 5) — **sharpest drift audit** |
| Lane retriggers on scorer edits | filter paths + YAML valid | Box (Task 6) |
| No Wave B files in diff | three-dot diff | Box (Task 7) |
