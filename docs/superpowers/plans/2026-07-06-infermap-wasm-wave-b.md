# InferMap WASM/TS Wave B — name scorers Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the three TS name scorers (`ExactScorer`, `FuzzyNameScorer`, `InitialismScorer`) to the Wave A `infermap-wasm` backend — per-pair dispatch, byte-parity gated — so they run the same `infermap-core` kernels as Python's native wheel.

**Architecture:** Three scalar `#[wasm_bindgen]` shims added to the existing Wave A wasm module (no JSON — `&str`→`f64`/`Option<f64>`); the TS backend/loader gain three methods; each scorer's `.score()` dispatches for the numeric score when the backend is set (reasoning stays host), else pure. A parity corpus asserts kernel==pure per scorer — a real drift audit over two independent Jaro-Winkler impls + two tokenizers.

**Tech Stack:** Rust (`infermap-wasm` wasm-bindgen), TS/vitest, the Wave A `infermap_wasm` CI lane.

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-wasm-wave-b-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Constraints (READ FIRST)

**Repo:** `D:\show_case\gg-local-llm`, branch `feat/infermap-wasm-wave-b` (checked out off fresh `origin/main` — Wave A #1505 is merged, so the crate + TS wasm module + `infermap_wasm` CI lane all exist).

**THE BOX CAN RUN ALMOST NOTHING.** No `cargo`/`wasm-pack`/`wasm-bindgen` (Rust CI-only); no `vitest`/`tsc`/`tsup`/`pnpm build` (TS OOM, CI-only). Box CAN do: `node --check <file.mjs>` (NOT `.ts`), `git`, `grep`/read, eye-review against exemplars. **Every task is write-against-spec + eye-verify + commit; CI is the first real test.**

**Wave A files you extend (read them first each task):**
- `packages/rust/extensions/infermap-wasm/src/lib.rs` — has `#[cfg(target_arch="wasm32")] mod wasm { ... detect_domain_json ... }` (~line 46).
- `packages/typescript/infermap/src/core/wasm/backend.ts` — the `InfermapBackend` interface + registry.
- `packages/typescript/infermap/src/core/wasm/loader.ts` — the `glue` type + returned adapter.
- `packages/typescript/infermap/src/core/scorers/{exact,fuzzy-name,initialism}.ts` — the three scorers.
- `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts` — ends with a `});` closing the detect `d(...)` block; `d`, `existsSync`, `enableInfermapWasm`, `disableInfermapWasm`, `getInfermapBackend` are module-scope.
- `.github/workflows/ci.yml` — `infermap_wasm` filter block (~line 242).

**Git:** benzsevern (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue — `gh pr merge --auto --squash` WITHOUT `--delete-branch`. Commit trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

---

## File Structure

| File | Change | Action |
| --- | --- | --- |
| `packages/rust/extensions/infermap-wasm/src/lib.rs` | +3 `#[wasm_bindgen]` shims in `mod wasm` | Modify |
| `packages/typescript/infermap/src/core/wasm/backend.ts` | +3 interface methods | Modify |
| `packages/typescript/infermap/src/core/wasm/loader.ts` | +3 glue-type entries + adapter methods | Modify |
| `packages/typescript/infermap/src/core/scorers/exact.ts` | backend dispatch | Modify |
| `packages/typescript/infermap/src/core/scorers/fuzzy-name.ts` | backend dispatch | Modify |
| `packages/typescript/infermap/src/core/scorers/initialism.ts` | backend dispatch | Modify |
| `packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts` | +name-pair drift-audit block | Modify |
| `.github/workflows/ci.yml` | +3 scorer paths in `infermap_wasm` filter | Modify |

---

## Task 1: Crate — 3 scalar wasm-bindgen shims (CI-only)

**Do NOT run cargo.** Write against spec, verify by grep/eye, commit. CI compiles.

**Files:** Modify `packages/rust/extensions/infermap-wasm/src/lib.rs`.

- [ ] **Step 1: Read the current file** and locate the `#[cfg(target_arch = "wasm32")] mod wasm { ... }` block (holds `detect_domain_json`). Confirm `infermap_core::{exact_score, fuzzy_name_score, initialism_score}` exist with signatures `(&str,&str)->f64`, `(&str,&str)->f64`, `(&str,&str)->Option<f64>` (grep `packages/rust/extensions/infermap-core/src/lib.rs`).

- [ ] **Step 2: Add three shims** inside `mod wasm`, after the `detect_domain_json` fn (before the closing `}` of `mod wasm`):
```rust
    #[wasm_bindgen]
    pub fn exact_score(a: &str, b: &str) -> f64 {
        infermap_core::exact_score(a, b)
    }

    #[wasm_bindgen]
    pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
        infermap_core::fuzzy_name_score(a, b)
    }

    // Option<f64> marshals to `number | undefined` in the glue (abstain).
    #[wasm_bindgen]
    pub fn initialism_score(a: &str, b: &str) -> Option<f64> {
        infermap_core::initialism_score(a, b)
    }
```
Nothing else changes (no new Cargo.toml deps — score-core arrives via infermap-core; no host `_impl` needed — scalar passthroughs).

- [ ] **Step 3: Verify by eye (NO cargo)**
```bash
grep -n "mod wasm\|#\[wasm_bindgen\]\|pub fn exact_score\|pub fn fuzzy_name_score\|pub fn initialism_score" packages/rust/extensions/infermap-wasm/src/lib.rs
```
Confirm: all three new fns are INSIDE `mod wasm` (after `detect_domain_json`, before the module's closing brace), each with `#[wasm_bindgen]`, delegating to `infermap_core::`. Confirm the module's braces still balance (the closing `}` of `mod wasm` is after `initialism_score`).

- [ ] **Step 4: Commit**
```bash
cd "D:/show_case/gg-local-llm"
git add packages/rust/extensions/infermap-wasm/src/lib.rs
git commit -m "feat(infermap-wasm): name-scorer shims (exact/fuzzy_name/initialism) (Wave B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, grep output, SHA.

---

## Task 2: Backend interface + loader adapter

**Files:** Modify `.../wasm/backend.ts`, `.../wasm/loader.ts`. Box: eye-review (no tsc).

- [ ] **Step 1: Extend `InfermapBackend`** in `backend.ts` — add three methods to the interface (keep `detectDomain`):
```ts
export interface InfermapBackend {
  detectDomain(
    columns: string[],
    domains: Array<[string, string[]]>,
    minScore: number,
  ): DetectionResult;
  exactScore(a: string, b: string): number;
  fuzzyNameScore(a: string, b: string): number;
  initialismScore(a: string, b: string): number | null;
}
```

- [ ] **Step 2: Extend `loader.ts`** — add the three exports to the `glue` cast type and the three methods to the returned adapter object:
```ts
  const glue = (await import("./artifacts/infermap_wasm.js" as string)) as {
    default: (input: { module_or_path: Uint8Array }) => Promise<unknown>;
    detect_domain_json: (input_json: string) => string;
    exact_score: (a: string, b: string) => number;
    fuzzy_name_score: (a: string, b: string) => number;
    initialism_score: (a: string, b: string) => number | undefined;
  };
  await glue.default({ module_or_path: bytes });
  return {
    detectDomain(columns, domains, minScore) {
      // ...existing Wave A body, unchanged...
    },
    exactScore: (a, b) => glue.exact_score(a, b),
    fuzzyNameScore: (a, b) => glue.fuzzy_name_score(a, b),
    initialismScore: (a, b) => glue.initialism_score(a, b) ?? null,
  };
```
Keep the existing `detectDomain` adapter body verbatim. `index.ts` is unchanged.

- [ ] **Step 3: Verify by eye.** Confirm the interface + glue type + adapter each gained exactly three entries; `initialism_score` glue return is `number | undefined` and the adapter collapses it with `?? null`; `.js`/`"as string"` import unchanged.

- [ ] **Step 4: Commit**
```bash
git add packages/typescript/infermap/src/core/wasm/backend.ts packages/typescript/infermap/src/core/wasm/loader.ts
git commit -m "feat(infermap-ts): name-scorer backend methods + loader adapters (Wave B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA.

---

## Task 3: Wire the three scorer classes (byte-identical dispatch)

**Files:** Modify `scorers/{exact,fuzzy-name,initialism}.ts`. Box: eye-review. The existing scorer unit tests (`tests/unit/{exact,fuzzy-name}-scorer.test.ts`, `tests/unit/prefix-and-initialism.test.ts`) must stay green — verified in CI.

Each file: add `import { getInfermapBackend } from "../wasm/backend.js";` and route the numeric score through the backend when set, keeping reasoning host-side. Output byte-identical to today.

- [ ] **Step 1: `exact.ts`** — replace the `score` method body:
```ts
  score(source: FieldInfo, target: FieldInfo): ScorerResult {
    const backend = getInfermapBackend();
    const sim = backend
      ? backend.exactScore(source.name, target.name)
      : source.name.trim().toLowerCase() === target.name.trim().toLowerCase()
        ? 1.0
        : 0.0;
    if (sim === 1.0) {
      return makeScorerResult(1.0, `Exact name match: '${source.name}'`);
    }
    return makeScorerResult(
      0.0,
      `No exact match: '${source.name}' vs '${target.name}'`,
    );
  }
```
> The kernel returns exactly `1.0`/`0.0`, so branching on `sim === 1.0` is byte-identical to the old `src === tgt` branch; reasoning strings (raw names) unchanged.

- [ ] **Step 2: `fuzzy-name.ts`** — replace the `score` method body (keep the module-level `normalize`):
```ts
  score(source: FieldInfo, target: FieldInfo): ScorerResult {
    const srcName = source.canonicalName ?? source.name;
    const tgtName = target.canonicalName ?? target.name;
    const backend = getInfermapBackend();
    const sim = backend
      ? backend.fuzzyNameScore(srcName, tgtName) // kernel normalizes internally
      : jaroWinklerSimilarity(normalize(srcName), normalize(tgtName));
    // Reasoning stays host: re-normalize for the message (as today).
    const srcNorm = normalize(srcName);
    const tgtNorm = normalize(tgtName);
    return makeScorerResult(
      sim,
      `Jaro-Winkler similarity between '${srcNorm}' and '${tgtNorm}': ${sim.toFixed(3)}`,
    );
  }
```

- [ ] **Step 3: `initialism.ts`** — replace the `InitialismScorer.score` method body (keep `tokenize`/`isPrefixConcat`/`scorePair` exported & unchanged):
```ts
  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    const srcName = source.canonicalName ?? source.name;
    const tgtName = target.canonicalName ?? target.name;
    const backend = getInfermapBackend();
    const s = backend
      ? backend.initialismScore(srcName, tgtName)
      : scorePair(srcName, tgtName);
    if (s === null) return null;
    return makeScorerResult(
      s,
      `Initialism/abbreviation match: '${srcName}' <-> '${tgtName}' (score=${s.toFixed(3)})`,
    );
  }
```

- [ ] **Step 4: Verify by eye.** For each file: the `getInfermapBackend` import added (relative `../wasm/backend.js`); the pure branch is the EXACT current logic; reasoning strings byte-identical; `fuzzy` passes RAW names to the backend (kernel normalizes) and re-normalizes host-side for the message; `initialism` returns `null` when the score is null (backend returns `number | null`, pure `scorePair` returns `number | null`). `git diff packages/typescript/infermap/src/core/scorers/` and read it.

- [ ] **Step 5: Commit**
```bash
git add packages/typescript/infermap/src/core/scorers/exact.ts packages/typescript/infermap/src/core/scorers/fuzzy-name.ts packages/typescript/infermap/src/core/scorers/initialism.ts
git commit -m "refactor(infermap-ts): name scorers dispatch to wasm backend (Wave B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, SHA, one-line confirmation each pure path is byte-identical.

---

## Task 4: Parity gate — name-pair drift audit

**Files:** Modify `tests/parity/infermap-wasm.parity.test.ts`. Box: eye-review (skips locally, runs in CI).

- [ ] **Step 1: Add imports** near the top of the file (after the existing imports):
```ts
import { jaroWinklerSimilarity } from "../../src/core/util/string-distance.js";
import { scorePair } from "../../src/core/scorers/initialism.js";
```

- [ ] **Step 2: Append a second `d(...)` block** at the END of the file (after the existing detect block's closing `});`). `d`, `existsSync`, `enable/disableInfermapWasm`, `getInfermapBackend` are already module-scope from Wave A:
```ts

// ---------------------------------------------------------------------------
// Wave B: name-scorer parity (drift audit — 2 JW impls, 2 tokenizers)
// ---------------------------------------------------------------------------

const normalize = (s: string): string =>
  s.trim().toLowerCase().replace(/[_\- ]/g, "");
const pureExact = (a: string, b: string): number =>
  a.trim().toLowerCase() === b.trim().toLowerCase() ? 1.0 : 0.0;

// ASCII pairs — mirrors the Python Wave 2 _NAME_PAIRS. The toLowerCase / chars()
// Unicode edges stay out of the must-pass corpus (Wave 1/2 documented boundary).
const NAME_PAIRS: Array<[string, string]> = [
  ["City", "city"],
  ["provider_npi", "ProviderNPI"],
  ["first_name", "firstName"],
  ["assay_id", "ASSI"],
  ["confidence_score", "CONSC"],
  ["variant_id", "VARI"],
  ["order_id", "orderid"],
  ["abc", "xyz"],
  ["HTTPSConnection", "https_connection"],
  ["a", "a"],
  ["dob", "date_of_birth"],
  ["providerIDs", "provider_i_ds"],
  ["URLs", "ur_ls"],
  ["macOS", "mac_os"],
  ["iOS", "i_os"],
];

d("infermap name-scorer WASM-vs-pure parity", () => {
  afterAll(() => disableInfermapWasm());

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
      // kernel normalizes internally; pure normalizes then JW.
      expect(be.fuzzyNameScore(a, b)).toBe(
        jaroWinklerSimilarity(normalize(a), normalize(b)),
      );
      disableInfermapWasm();
    });
    it(`initialism '${a}'/'${b}' kernel == pure`, async () => {
      await enableInfermapWasm({ require: true });
      const be = getInfermapBackend()!;
      // both null (abstain) or the same graded float.
      expect(be.initialismScore(a, b)).toBe(scorePair(a, b));
      disableInfermapWasm();
    });
  }
});
```
> `.toBe` exact equality is deliberate — a ULP-level JW divergence or a tokenizer
> mismatch is *meant* to red (that's the drift audit). `afterAll` is already
> imported in the Wave A test — confirm; if not, add it to the vitest import.

- [ ] **Step 3: Verify by eye.**
```bash
grep -n "afterAll\|import.*vitest\|export function scorePair\|export function jaroWinklerSimilarity\|export const jaroWinklerSimilarity" packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts packages/typescript/infermap/src/core/scorers/initialism.ts packages/typescript/infermap/src/core/util/string-distance.ts
```
Confirm: `scorePair` exported from `initialism.ts`; `jaroWinklerSimilarity` exported from `string-distance.ts`; `afterAll` is in the test's vitest import (add if missing); the new `d(...)` block is AFTER the existing block's `});` and imports resolve (`.js` extensions).

- [ ] **Step 4: Commit**
```bash
git add packages/typescript/infermap/tests/parity/infermap-wasm.parity.test.ts
git commit -m "test(infermap-ts): name-scorer WASM-vs-pure parity drift audit (Wave B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, grep confirmations (scorePair + jaroWinklerSimilarity exports, afterAll import), SHA.

---

## Task 5: CI filter — retrigger the lane on scorer changes

**Files:** Modify `.github/workflows/ci.yml`. Box: YAML-validate.

- [ ] **Step 1: Add the three scorer paths** to the `infermap_wasm` filter block. Find the `- 'packages/typescript/infermap/src/core/detect.ts'` line (~247) inside the `infermap_wasm:` filter, and add after it (same 14-space indent):
```yaml
              - 'packages/typescript/infermap/src/core/scorers/exact.ts'
              - 'packages/typescript/infermap/src/core/scorers/fuzzy-name.ts'
              - 'packages/typescript/infermap/src/core/scorers/initialism.ts'
```
(The crate `infermap-wasm/**`, the wasm module `src/core/wasm/**`, and the parity test are already covered by Wave A globs — do NOT re-add them.)

- [ ] **Step 2: Validate YAML** (broken ci.yml = zero jobs = required gate never reports):
```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml YAML OK')"
grep -n "scorers/exact.ts\|scorers/fuzzy-name.ts\|scorers/initialism.ts" .github/workflows/ci.yml
```
Expect: `ci.yml YAML OK`; the three lines present, adjacent to the `detect.ts` filter line (a few lines below the `infermap_wasm:` FILTER key ~242, NOT near the top-level output ~88).

- [ ] **Step 3: Commit**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(infermap): retrigger infermap_wasm lane on name-scorer changes (Wave B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, YAML-OK, grep, SHA.

---

## Task 6: Rebase + push + PR + arm + monitor the drift lane (controller runs this)

**Files:** none.

- [ ] **Step 1: Rebase onto fresh origin/main**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q
git rebase origin/main
```
Conflicts unlikely (isolated additions). If `ci.yml` conflicts, keep both; re-validate YAML.

- [ ] **Step 2: Confirm three-dot diff is clean**
```bash
git diff --stat origin/main...HEAD
```
Expect only the Wave B files (spec, plan, `infermap-wasm/src/lib.rs`, the 2 wasm TS files, the 3 scorers, the parity test, `ci.yml`). If unrelated files appear, STOP.

- [ ] **Step 3: Push**
```bash
git push -u origin feat/infermap-wasm-wave-b
```

- [ ] **Step 4: Open the PR**
```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(infermap): WASM/TS Wave B — name scorers (exact/fuzzy_name/initialism)" \
  --body "$(cat <<'EOF'
## What

Wave B of the InferMap WASM/TS surface: wires the three name scorers to the Wave A `infermap-wasm` backend. Each scorer's `.score()` dispatches per-pair to the Rust kernel for the numeric score (reasoning stays host), with the pure-TS impl as the lossy fallback — the same split as the Python Wave 2 native scorers.

- **Crate**: three scalar `#[wasm_bindgen]` shims (`exact_score`/`fuzzy_name_score`/`initialism_score`) — no JSON, `Option<f64>` → `number | undefined` for abstain.
- **TS**: `InfermapBackend` + loader gain three methods; the three scorer classes dispatch to them.

## Parity = real drift audit

The parity gate asserts `kernel == pure` over the Python Wave 2 name pairs, exact `.toBe`. This is a genuine audit: `fuzzy` compares two independent Jaro-Winkler implementations (Rust `score-core` vs TS `string-distance.ts`), `initialism` compares two independent tokenizers + prefix-concat DPs. **If the `infermap_wasm` lane reds on a must-pass ASCII pair, that is the audit working** — a real TS-vs-Rust divergence to investigate; WASM (== Rust == Python) is the reference. ASCII corpus; the `toLowerCase`/`chars().count()` Unicode edges stay the documented boundary.

## Scope

`exact` is byte-identical unconditionally; `fuzzy`/`initialism` are byte-identical iff the gate is green. `profile`/`pattern_type` are Wave C.

Spec: `docs/superpowers/specs/2026-07-06-infermap-wasm-wave-b-design.md`
Plan: `docs/superpowers/plans/2026-07-06-infermap-wasm-wave-b.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```

- [ ] **Step 5: Arm auto-merge, then monitor the drift lane ONCE**
```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
Do NOT `--delete-branch`. Then launch ONE background monitor of the `infermap_wasm` lane (as in Wave A): a red on a name-scorer parity case is a **real drift finding** (two JW impls / two tokenizers genuinely disagree on an ASCII pair) — investigate the specific case, make WASM the reference, document/fix the pure TS, report in the PR. A green lane means the TS name scorers already match the Rust reference. Report the PR number + the drift verdict, then STOP.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Crate shims compile | Rust build in the lane | CI (Task 1) |
| Backend/loader typecheck | tsc/tsup in the lanes | CI (Tasks 2,3) |
| Scorer pure paths byte-identical | existing scorer unit tests stay green | CI (Task 3) |
| **kernel == pure (exact/fuzzy/initialism)** | name-pair parity, `.toBe` | CI `infermap_wasm` lane (Task 4) — **drift audit** |
| Lane retriggers on scorer edits | filter paths + YAML valid | Box (Task 5) |
| No unrelated diff | three-dot diff | Box (Task 6) |
