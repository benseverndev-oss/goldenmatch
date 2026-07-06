# InferMap WASM/TS Wave B — name scorers (exact / fuzzy_name / initialism) design

**Date:** 2026-07-06
**Status:** Approved (design)
**Depends on:** Wave A (#1505, merged `90e2ff6d`) — the `infermap-wasm` crate + TS `wasm/{backend,loader,index}.ts` + CI `infermap_wasm` lane. Wave B extends all of them.
**Branch:** `feat/infermap-wasm-wave-b` off `origin/main`.

## 1. Goal

Wire the three name scorers — `ExactScorer`, `FuzzyNameScorer`, `InitialismScorer`
— to the Wave A `infermap-wasm` backend, so the TS surface runs the same
`infermap-core` kernels Python's native wheel runs (`exact_score`,
`fuzzy_name_score`, `initialism_score`, all shipped in the Python Wave 2). Per-pair
dispatch; the kernel returns the numeric score, the reasoning string stays host —
exactly the Python Wave 2 split.

The parity gate here is a **genuine drift audit**: the TS surface carries two
independent reimplementations that could diverge from the Rust reference — a
Jaro-Winkler (`util/string-distance.ts` vs `score-core`) and a tokenizer +
prefix-concat DP (`scorers/initialism.ts` vs the Rust hand-scanner). Wave B's
whole reason to exist is to collapse those onto the one reference and prove (or
surface) where they agree.

## 2. Decisions (settled in brainstorming)

- **Per-pair dispatch, NOT a batch matrix.** The engine scores an M×N field-pair
  matrix (`engine.ts:237`, `sc.score(src, tgt)` per cell). Each scorer's `.score`
  calls the backend once for the numeric score. This mirrors the Python Wave 2
  native dispatch (`native_module().exact_score(a, b)` per pair — Python does not
  batch these) and Wave A's per-call adapter. The #688 boundary-cost lesson
  concerns *millions* of record pairs in dedup; schema mapping is *field counts*
  (tens–low-thousands of pairs), the regime where Python's per-pair pyo3 dispatch
  is already accepted. The batch-matrix optimization (score-wasm's `score_matrix`)
  is deferred — it restructures the engine hot loop for a perf win that doesn't
  bite at this scale.
- **Scalar boundary, NO JSON.** Unlike Wave A's structured `detect` input, the
  name scorers are scalar `(a: &str, b: &str) -> f64` / `-> Option<f64>`.
  wasm-bindgen marshals `&str` / `f64` / `Option<f64>` natively — no serde, no
  JSON round-trip.
- **`fuzzy_name` wraps `infermap-core`, not `score-wasm`.**
  `infermap_core::fuzzy_name_score` already reuses `goldenmatch_score_core::
  jaro_winkler_similarity` internally; wrapping it in `infermap-wasm` keeps a
  single glue module the Wave A loader already loads. No second wasm module.
- **Kernels normalize internally; callers pass raw names.** `exact_score` does
  `a.trim().to_lowercase() == b.trim().to_lowercase()`; `fuzzy_name_score` does
  `jaro_winkler(normalize(a), normalize(b))` where `normalize` = strip + lower +
  remove `_ - ` (space). So the TS scorers pass the raw strings they pass today;
  the kernel normalizes, exactly like Python.

## 3. Crate additions (`packages/rust/extensions/infermap-wasm/src/lib.rs`)

Add three `#[wasm_bindgen]` fns inside the EXISTING `#[cfg(target_arch="wasm32")]
mod wasm` (which currently holds `detect_domain_json`), each delegating to the
already-exported kernel:
```rust
    #[wasm_bindgen]
    pub fn exact_score(a: &str, b: &str) -> f64 {
        infermap_core::exact_score(a, b)
    }

    #[wasm_bindgen]
    pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
        infermap_core::fuzzy_name_score(a, b)
    }

    #[wasm_bindgen]
    pub fn initialism_score(a: &str, b: &str) -> Option<f64> {
        infermap_core::initialism_score(a, b)
    }
```
`Option<f64>` marshals to `number | undefined` in the glue (abstain). No new
crate dependencies (score-core arrives transitively via `infermap-core`). No host
`_impl` wrappers needed — these are scalar passthroughs; the underlying kernels
are already unit-tested in `infermap-core`. `Cargo.toml` is unchanged.

## 4. Backend + loader (`packages/typescript/infermap/src/core/wasm/`)

### `backend.ts` — extend `InfermapBackend`
```ts
export interface InfermapBackend {
  detectDomain(/* Wave A, unchanged */): DetectionResult;
  exactScore(a: string, b: string): number;
  fuzzyNameScore(a: string, b: string): number;
  initialismScore(a: string, b: string): number | null;
}
```

### `loader.ts` — extend the glue type + the returned backend
```ts
  const glue = (await import("./artifacts/infermap_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    detect_domain_json: (input_json: string) => string;
    exact_score: (a: string, b: string) => number;
    fuzzy_name_score: (a: string, b: string) => number;
    initialism_score: (a: string, b: string) => number | undefined;
  };
  // ...
  return {
    detectDomain(/* unchanged */) { /* ... */ },
    exactScore: (a, b) => glue.exact_score(a, b),
    fuzzyNameScore: (a, b) => glue.fuzzy_name_score(a, b),
    initialismScore: (a, b) => glue.initialism_score(a, b) ?? null,
  };
```
`index.ts` is unchanged (enable/disable already generic).

## 5. Wire the three scorer classes

Each dispatches for the numeric score, keeps its reasoning host-side. The pure
path is the existing code, unchanged. Inputs match Python (exact = raw `name`;
fuzzy/initialism = `canonicalName ?? name`).

### `scorers/exact.ts`
```ts
score(source, target): ScorerResult {
  const backend = getInfermapBackend();
  const sim = backend
    ? backend.exactScore(source.name, target.name)
    : (source.name.trim().toLowerCase() === target.name.trim().toLowerCase() ? 1.0 : 0.0);
  if (sim === 1.0) return makeScorerResult(1.0, `Exact name match: '${source.name}'`);
  return makeScorerResult(0.0, `No exact match: '${source.name}' vs '${target.name}'`);
}
```
> Reasoning branches on `sim === 1.0` (the kernel returns exactly 1.0/0.0), so the
> messages are byte-identical to today's `src === tgt` branch.

### `scorers/fuzzy-name.ts`
```ts
score(source, target): ScorerResult {
  const srcName = source.canonicalName ?? source.name;
  const tgtName = target.canonicalName ?? target.name;
  const backend = getInfermapBackend();
  const sim = backend
    ? backend.fuzzyNameScore(srcName, tgtName)          // kernel normalizes
    : jaroWinklerSimilarity(normalize(srcName), normalize(tgtName));
  // Reasoning stays host: re-normalize for the message (as today).
  const srcNorm = normalize(srcName);
  const tgtNorm = normalize(tgtName);
  return makeScorerResult(sim, `Jaro-Winkler similarity between '${srcNorm}' and '${tgtNorm}': ${sim.toFixed(3)}`);
}
```

### `scorers/initialism.ts`
```ts
score(source, target): ScorerResult | null {
  const srcName = source.canonicalName ?? source.name;
  const tgtName = target.canonicalName ?? target.name;
  const backend = getInfermapBackend();
  const s = backend ? backend.initialismScore(srcName, tgtName) : scorePair(srcName, tgtName);
  if (s === null) return null;
  return makeScorerResult(s, `Initialism/abbreviation match: '${srcName}' <-> '${tgtName}' (score=${s.toFixed(3)})`);
}
```
`tokenize`, `isPrefixConcat`, `scorePair` stay exported (the parity oracle uses
`scorePair`). Add `import { getInfermapBackend } from "../wasm/backend.js";` to
each of the three scorer files.

## 6. Parity gate — extend `tests/parity/infermap-wasm.parity.test.ts`

Add a name-scorer section inside the existing `existsSync(artifact) ? describe :
describe.skip` block (or a second such block). For each pair assert the backend
equals the pure reference:
```ts
import { jaroWinklerSimilarity } from "../../src/core/util/string-distance.js";
import { scorePair } from "../../src/core/scorers/initialism.js";

const normalize = (s: string) => s.trim().toLowerCase().replace(/[_\- ]/g, "");
const pureExact = (a: string, b: string) =>
  a.trim().toLowerCase() === b.trim().toLowerCase() ? 1.0 : 0.0;

// ASCII name pairs — the toLowerCase/Unicode edge stays the documented boundary
// (Wave 1/2). Mirrors the Python Wave 2 _NAME_PAIRS.
const NAME_PAIRS: Array<[string, string]> = [
  ["City", "city"], ["provider_npi", "ProviderNPI"], ["first_name", "firstName"],
  ["assay_id", "ASSI"], ["confidence_score", "CONSC"], ["variant_id", "VARI"],
  ["order_id", "orderid"], ["abc", "xyz"], ["HTTPSConnection", "https_connection"],
  ["a", "a"], ["dob", "date_of_birth"], ["providerIDs", "provider_i_ds"],
  ["URLs", "ur_ls"], ["macOS", "mac_os"], ["iOS", "i_os"],
];

// inside the `d(...)` describe, after enable:
for (const [a, b] of NAME_PAIRS) {
  it(`exact '${a}'/'${b}' kernel == pure`, async () => {
    await enableInfermapWasm({ require: true });
    const be = getInfermapBackend()!;
    expect(be.exactScore(a, b)).toBe(pureExact(a, b));
    disableInfermapWasm();
  });
  it(`fuzzy '${a}'/'${b}' kernel == pure`, async () => {
    await enableInfermapWasm({ require: true });
    const be = getInfermapBackend()!;
    expect(be.fuzzyNameScore(a, b)).toBe(jaroWinklerSimilarity(normalize(a), normalize(b)));
    disableInfermapWasm();
  });
  it(`initialism '${a}'/'${b}' kernel == pure`, async () => {
    await enableInfermapWasm({ require: true });
    const be = getInfermapBackend()!;
    expect(be.initialismScore(a, b)).toBe(scorePair(a, b));  // both null (abstain) or same float
    disableInfermapWasm();
  });
}
```
Exact `.toBe` (not `toBeCloseTo`) — the whole point is byte-identity, and the
kernel returns the score verbatim (the reasoning `.toFixed(3)` formatting is
host-side and out of the compared value). A ULP-level JW divergence is *meant* to
red here — that's the audit.

> Known non-ASCII edge (out of the must-pass corpus): the `initialism` ratio uses
> `chars().count()` in Rust vs `.length` (UTF-16 code units) in TS — equal on the
> ASCII corpus, but a surrogate-pair name would diverge. Same documented-Unicode-edge
> class as `toLowerCase`; ASCII-only fixtures per the Wave 1/2 precedent.

> **This is the drift-audit surface.** `fuzzyNameScore` compares two independent
> Jaro-Winkler implementations (Rust `score-core` vs TS `string-distance.ts`);
> `initialismScore` compares two independent tokenizers + prefix-concat DPs. If a
> must-pass ASCII pair diverges in CI, that is a real finding: WASM (== Rust ==
> Python) is the reference, the pure TS is the documented lossy fallback, and the
> divergence + resolution are surfaced in the PR — not skipped. (The Rust JW is
> `rapidfuzz`-derived and already Python-parity-validated in Wave 2; the TS JW is
> a separate hand-port, so the fuzzy pairs are the highest-drift-risk cases.)

## 7. CI

The `infermap_wasm` lane (Wave A) already builds the artifact + runs the parity
test. Only its `dorny/paths-filter` entry needs to gain the three scorer files so
a scorer-only change retriggers the lane:
```yaml
              - 'packages/typescript/infermap/src/core/scorers/exact.ts'
              - 'packages/typescript/infermap/src/core/scorers/fuzzy-name.ts'
              - 'packages/typescript/infermap/src/core/scorers/initialism.ts'
```
(The crate, the wasm module, and the parity test are already covered by the Wave A
filter globs.) No new lane, no new output, no new job.

## 8. Out of scope

- `profile` / `pattern_type` scorers (Wave C); `alias` / `llm`.
- The batch-matrix boundary optimization (deferred; §2).
- Any change to scorer weights, reasoning strings, abstain semantics, or the
  engine loop. Pure cutover: for `exact`, same output unconditionally (kernel
  returns literal 1.0/0.0). For `fuzzy`/`initialism`, the kernel is an
  *independent* implementation of the pure path, so "same output" holds **iff the
  parity gate is green** — where they diverge, WASM (== Rust == Python) is the
  reference and the pure TS is the documented lossy fallback (§6, §9).

## 9. Risk assessment

- **Crate + TS wiring is a near-trivial mirror of Wave A** — three scalar
  passthrough shims + three backend methods + three per-pair dispatches. Low risk.
- **The parity gate may legitimately red — and that is the point.** Two JW impls
  and two tokenizers are exactly the kind of subtle reimplementation that drifts.
  If CI surfaces a divergence on an ASCII must-pass pair, Wave B has done its job:
  investigate, make WASM the reference, document/fix the pure path, report in the
  PR. A green gate means the TS name scorers already match the Rust reference and
  the cutover is clean.
- **Almost entirely CI-verified** (box can't cargo/wasm-pack/vitest/tsc). Local =
  write-against-spec + `node -c`/eye review; the crate build + parity run first
  execute in the `infermap_wasm` lane.

## 10. Build environment constraints

- **Box-runnable:** eye/`node --check` only (no cargo/wasm/vitest/tsc).
- **CI-only:** the wasm build + the vitest parity test (the `infermap_wasm` lane).
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
