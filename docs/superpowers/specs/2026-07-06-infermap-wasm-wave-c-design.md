# InferMap WASM/TS Wave C — `profile` + `pattern_type` (final scorers) design

**Date:** 2026-07-06
**Status:** Approved (design)
**Depends on:** Wave A (#1505, detect + foundation) and Wave B (#1507, name scorers) — the `infermap-wasm` crate, the TS `wasm/{backend,loader}.ts`, the parity test, and the `infermap_wasm` CI lane. Wave C extends all of them. (Developed stacked on Wave B; rebased onto `main` once Wave B merges.)
**Branch:** `feat/infermap-wasm-wave-c`.

## 1. Goal

Wire the last two scorers — `ProfileScorer` and `PatternTypeScorer` — to the
`infermap-wasm` backend, byte-parity gated, completing the six-kernel WASM/TS
surface (`detect_domain` + the five scorers). Per-pair/per-field dispatch; kernel
returns the numeric result, reasoning stays host — the Python Wave 2/3/4 split.

`pattern_type` is the headliner: the TS surface classifies semantic types with JS
`RegExp`; WASM makes it run the Rust `regex` kernel, collapsing a **third**
independent regex engine (JS `RegExp` vs Python `re` vs Rust `regex`) onto the one
reference. Its parity gate is the sharpest drift audit of the whole surface.

## 2. `profile` — scalar per-pair (Wave 3 kernel)

Maps to `infermap_core::profile_score(src_dtype: &str, tgt_dtype: &str, src_null:
f64, tgt_null: f64, src_uniq: f64, tgt_uniq: f64, src_val_count: f64, tgt_val_count:
f64, src_avg_len: f64, tgt_avg_len: f64) -> f64` — the exact Wave 3 Python split.

- **Host keeps:** the abstain (`valueCount === 0 → null`), `avgValueLength(samples)`
  per side (the list reduction — kept host to avoid marshaling + the code-point
  length trap), and the entire `parts` reasoning string.
- **Kernel:** the five-weighted-add math (dtype 0.4 + null 0.2 + uniq 0.2 + len 0.1
  + card 0.1). Fixed op order → deterministic; scalar → cheap boundary.
- **`ProfileScorer.score`:** abstain host-side; compute `srcLen`/`tgtLen`
  host-side; `sim = backend ? backend.profileScore(source.dtype, target.dtype,
  source.nullRate, target.nullRate, source.uniqueRate, target.uniqueRate,
  source.valueCount, target.valueCount, srcLen, tgtLen) : <pure inline math>`;
  rebuild the `parts` reasoning host-side (recomputing the sub-values, as today).
  Byte-identical.

> The TS `avgValueLength` uses `String(s).length` (UTF-16 code units); the kernel
> never sees it (host passes the float). No parity risk from length here.

## 3. `pattern_type` — per-field bitmask (Wave 4 kernel)

Maps to `infermap_core::pattern_match_types(samples: &[String]) -> Vec<u32>` — a
per-sample bitmask (bit `i` set iff the sample matches `SEMANTIC_TYPES[i]`, in
insertion order). This is the **Wave 4 Python bitmask refactor ported to TS**.

### 3.1 Refactor `classifyWithPct` (byte-identical)
Today `classifyWithPct` loops the 8 regexes inline per sample and counts. Refactor
to the bitmask form the kernel enables (identical output):
```ts
const N_TYPES = Object.keys(SEMANTIC_TYPES).length; // 8

// Bitmask oracle: bit i set iff s matches SEMANTIC_TYPES[i] (insertion order).
// s is expected pre-stripped by the caller.
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
  return backend ? backend.patternMatchTypes(stripped) : stripped.map(_matchTypesPure);
}

function classifyWithPct(field, threshold = 0.6): { type: string | null; pct: number } {
  const samples = cleanSamples(field.sampleValues).map((s) => String(s).trim());
  if (samples.length === 0) return { type: null, pct: 0 };
  const masks = _matchTypesBatch(samples);
  let bestType: string | null = null;
  let bestPct = 0;
  const names = Object.keys(SEMANTIC_TYPES);
  for (let i = 0; i < names.length; i++) {
    let matches = 0;
    for (const m of masks) if (m & (1 << i)) matches++;
    const pct = matches / samples.length;
    if (pct > bestPct) { bestPct = pct; bestType = names[i]!; }
  }
  if (bestType !== null && bestPct >= threshold) return { type: bestType, pct: bestPct };
  return { type: null, pct: 0 };
}
```
> Byte-identity: `cleanSamples` + `String(s).trim()` reproduces the current
> per-sample match input; `samples.length` unchanged; per-type independent count
> (a sample may set multiple bits, exactly as it could match multiple regexes);
> strict-`>` best in insertion order; same threshold. `classifyField` and
> `PatternTypeScorer.score` are UNCHANGED (they call `classifyWithPct`).

### 3.2 Boundary
One WASM crossing per `classifyWithPct` (a field's stripped sample list → bitmask
array). `PatternTypeScorer.score` calls `classifyWithPct` twice (source, target),
so 2 crossings per field-pair — the same redundant re-classification the current
TS does (preserved, not optimized). At schema scale this is fine, per the Wave B
per-pair decision. `pattern_match_types` marshals `Vec<u32>` → `Uint32Array`; the
loader adapter returns `Array.from(...)` as `number[]`.

## 4. Crate additions (`packages/rust/extensions/infermap-wasm/src/lib.rs`)

Two `#[wasm_bindgen]` shims in the existing `#[cfg(target_arch="wasm32")] mod wasm`:
```rust
    #[allow(clippy::too_many_arguments)]
    #[wasm_bindgen]
    pub fn profile_score(
        src_dtype: &str, tgt_dtype: &str,
        src_null: f64, tgt_null: f64,
        src_uniq: f64, tgt_uniq: f64,
        src_val_count: f64, tgt_val_count: f64,
        src_avg_len: f64, tgt_avg_len: f64,
    ) -> f64 {
        infermap_core::profile_score(
            src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
            src_val_count, tgt_val_count, src_avg_len, tgt_avg_len)
    }

    #[wasm_bindgen]
    pub fn pattern_match_types(samples: Vec<String>) -> Vec<u32> {
        infermap_core::pattern_match_types(&samples)
    }
```
`#[allow(clippy::too_many_arguments)]` on `profile_score` (10 args, mirrors the
Wave 3 native shim). No new Cargo.toml deps (the `regex` dep is already in
`infermap-core` from Wave 4; it links into the wasm build via the existing
`infermap-core` path dep). No host `_impl` needed — passthroughs.

## 5. Backend + loader (`packages/typescript/infermap/src/core/wasm/`)

### `backend.ts` — extend `InfermapBackend`
```ts
  profileScore(
    srcDtype: string, tgtDtype: string,
    srcNull: number, tgtNull: number,
    srcUniq: number, tgtUniq: number,
    srcValCount: number, tgtValCount: number,
    srcAvgLen: number, tgtAvgLen: number,
  ): number;
  patternMatchTypes(samples: string[]): number[];
```

### `loader.ts` — extend the glue type + adapter
```ts
    profile_score: (
      srcDtype: string, tgtDtype: string,
      srcNull: number, tgtNull: number, srcUniq: number, tgtUniq: number,
      srcValCount: number, tgtValCount: number, srcAvgLen: number, tgtAvgLen: number,
    ) => number;
    pattern_match_types: (samples: string[]) => Uint32Array;
  // ...
    profileScore: (sd, td, sn, tn, su, tu, sv, tv, sl, tl) =>
      glue.profile_score(sd, td, sn, tn, su, tu, sv, tv, sl, tl),
    patternMatchTypes: (samples) => Array.from(glue.pattern_match_types(samples)),
```
(`Vec<u32>` marshals to `Uint32Array`; `Array.from` → `number[]` matches the
interface + the pure `_matchTypesPure` return type.) `index.ts` unchanged.

## 6. Wire the two scorer classes

- `scorers/profile.ts`: add `import { getInfermapBackend } from "../wasm/backend.js";`;
  keep `avgValueLength`/`similarity` host helpers; route `total` through
  `backend.profileScore(...)` when set (else the current inline math); rebuild the
  `parts` reasoning host-side exactly as today. Abstain stays host.
- `scorers/pattern-type.ts`: add the import + the `classifyWithPct` bitmask refactor
  (§3.1) + `_matchTypesPure` export (parity oracle). `SEMANTIC_TYPES`,
  `classifyField`, `PatternTypeScorer.score` unchanged.

## 7. Parity gate — the sharpest drift audit

Extend `tests/parity/infermap-wasm.parity.test.ts` with two `d(...)` blocks:

### 7.1 profile (float math — low risk)
Synthetic 10-tuples (the Wave 3 fixtures): identical profiles → 1.0; dtype
mismatch → drops 0.4; `max(...,1.0)` floors; lopsided null/uniqueness; asymmetric
lengths; realistic non-round rates. Assert `backend.profileScore(...args) ===
pureProfileScore(...args)` (`.toBe`), where `pureProfileScore` is a small
test-local reimpl of the five-add math (or, cleaner, export a
`_profileScorePure(...)` from `profile.ts` as the oracle — preferred, mirrors the
Python `_profile_score_pure`).

### 7.2 pattern_type (JS RegExp vs Rust regex — the real audit)
An ASCII corpus of sample strings (the Wave 4 fixtures): per type a canonical
positive + structural near-misses (`date_iso` `2026-13-99`, `ip_v4`
`999.999.999.999`) + genuine negatives + the `2026-07-06` date+phone co-match +
`currency` `£12.50`/`€1,000.00`/`$5`. Assert `backend.patternMatchTypes([s]) ...
=== _matchTypesPure(s)` (compare the single-element bitmask) per string.

> **This is where JS `RegExp` meets Rust `regex`.** They genuinely differ: JS
> `\d` is ASCII-only (`[0-9]`) while Rust `\d` = Unicode `\p{Nd}`; JS `\s` IS
> Unicode-aware while… on **ASCII** inputs all three engines agree
> (`\d`=`[0-9]`, `\s`=`[ \t\n\r\f\v]`), so the ASCII must-pass corpus should be
> green. The currency pattern differs textually (TS keeps `\£`/`\€`; the Rust
> kernel dropped them to `[$£€]` since `regex` rejects those escapes) but is
> semantically identical (literal £/€) — the gate proves that. The
> Unicode-digit / non-ASCII edge stays out of the must-pass corpus (Wave 1/2/4
> documented boundary), recorded informationally if useful. Any **ASCII**
> must-pass divergence is a real finding: WASM (== Rust == Python) is the
> reference, pure TS is the documented lossy fallback, surfaced in the PR.

## 8. CI

Extend the `infermap_wasm` paths-filter with the two scorer files:
```yaml
              - 'packages/typescript/infermap/src/core/scorers/profile.ts'
              - 'packages/typescript/infermap/src/core/scorers/pattern-type.ts'
```
The crate, wasm module, and parity test are already covered by Wave A globs. No
new lane.

## 9. Out of scope

- `alias` (dictionary/host) and `llm` (external) scorers — never kernels.
- Any change to scorer weights, thresholds, precedence, reasoning strings, or the
  engine loop. Pure cutover: `profile` byte-identical unconditionally (scalar
  float math, kernel returns the score verbatim); `pattern_type` byte-identical
  iff the parity gate is green (JS RegExp vs Rust regex — WASM is the reference
  otherwise).

## 10. Risk assessment

- **`profile` is a trivial mirror of Wave B** (scalar per-pair, host keeps
  avg-length + abstain + reasoning). Low risk; the parity float math is a fixed
  five-add order.
- **`pattern_type`'s `classifyWithPct` bitmask refactor is the one real change**
  — byte-identity-critical (like Wave 4's Python refactor). Its parity gate is
  the **most likely of the whole surface to surface a genuine divergence**,
  because JS `RegExp` is a third independent engine. On the ASCII corpus the
  three engines converge; if a must-pass pair reds, that's the audit doing its
  job (and the value of the wave).
- **Almost entirely CI-verified** (box can't cargo/wasm/vitest/tsc). Local =
  write-against-spec + `node -c`/eye review; the crate build + parity first run
  in the `infermap_wasm` lane.

## 11. Build environment constraints

- **Box-runnable:** eye/`node --check` only.
- **CI-only:** the wasm build + the vitest parity test (`infermap_wasm` lane).
- **Stacked on Wave B:** developed off the Wave B branch tip; rebase onto
  `origin/main` once Wave B (#1507) merges, keeping both waves' additions.
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
