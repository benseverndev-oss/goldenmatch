# Pure-TS scorer ⇄ rapidfuzz parity — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pure-TS `jaro` / `jaroWinkler` / `levenshtein` / `indel` (→ `token_sort`) scorers match `rapidfuzz` to 4 decimals across non-BMP, sub-0.7-prefix, and repeated-char inputs — the parity anchor that unblocks the opt-in WASM slice.

**Architecture:** Three localized fixes inside `packages/typescript/goldenmatch/src/core/scorer.ts`: (1) iterate Unicode **codepoints** (`Array.from`) instead of UTF-16 code units; (2) apply the Jaro-Winkler prefix bonus only when `jaro > 0.7`; (3) **floor** the transposition halving (`Math.floor(t/2)`). A Python emitter generates rapidfuzz-sourced goldens; a new fixture-backed parity test is the gate. All three fixes are pre-validated against rapidfuzz in Python (0/50000 disagreements to 4dp, incl. emoji + accents).

**Tech Stack:** TypeScript (vitest, strict `noUncheckedIndexedAccess`); Python 3.13 + `rapidfuzz` 3.14.5 (emitter only).

**Spec:** `docs/superpowers/specs/2026-06-12-scorer-rapidfuzz-parity-design.md`

---

## Pre-flight (read once, do not skip)

- **Worktree:** This plan runs in `.worktrees/scorer-rapidfuzz-parity` (branch `feat/scorer-rapidfuzz-parity`, off **main**). The spec lives here already.
- **Python oracle:** the repo-root venv has rapidfuzz: `/d/show_case/goldenmatch/.venv/Scripts/python`. Always prefix non-ASCII console runs with `PYTHONIOENCODING=utf-8` (cp1252 console bug). The emitter imports **only** `rapidfuzz` — never `goldenmatch` (avoids the polars WMI hang, `reference_polars_wmi_hang_windows`).
- **Local TS test execution:** this worktree has **no `node_modules`** (chosen: CI validates TS, `feedback_box_memory_oom_ts` + `reference_ts_worktree_install_exfat`). So `npx vitest …` steps below are **authoritative in CI** (push the branch; the existing TS job runs `tests/parity/**`). The *algorithm* is already proven correct by the Python oracle in the spec; the TS code in this plan is a direct transcription of that validated reference. Each fix's red→green is therefore: (red) the Python-mirror of the CURRENT scorer fails the new fixture rows — shown in the spec; (green) CI runs the fixture test post-fix. An executor who wants a tight local loop may `pnpm install` in the worktree first, but it is not required.
- **No value-shift in existing tests:** every current `scorer-ground-truth.test.ts` anchor is ASCII, jaro > 0.7, even-`t` (e.g. MARTHA/MARHTA `t=2`), so **none of them change**. They stay green as the no-regression proof. Do NOT edit their expected values.
- **Strict TS idioms:** `arr[i]!` after a length check; `Array.from(s)` returns `string[]` so `ca[i]` is `string | undefined` under `noUncheckedIndexedAccess` — index inside bounded loops and assert `!` exactly as the current code does.

---

## File Structure

- **Modify:** `packages/typescript/goldenmatch/src/core/scorer.ts` — `jaro` (~114), `jaroWinkler` (~160), `levenshteinDistance` (~178), `levenshteinSimilarity` (~210), `indelDistance` (~225), `indelSimilarity` (~253). `tokenSortRatio` is unchanged (its normalize pre-strips non-BMP) but inherits the fixed `indelSimilarity`.
- **Create:** `packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py` — rapidfuzz-sourced golden emitter.
- **Create:** `packages/typescript/goldenmatch/tests/parity/fixtures/scorer-rapidfuzz.json` — generated goldens (committed).
- **Create:** `packages/typescript/goldenmatch/tests/parity/scorer-rapidfuzz.test.ts` — 3 named per-divergence cases + the fixture-driven loop (the gate).
- **Modify:** `packages/typescript/goldenmatch/CHANGELOG.md` — Unreleased entry.

---

## Task 1: rapidfuzz golden emitter + fixture

**Files:**
- Create: `packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py`
- Create (generated): `packages/typescript/goldenmatch/tests/parity/fixtures/scorer-rapidfuzz.json`

- [ ] **Step 1: Write the emitter**

`packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py`:
```python
#!/usr/bin/env python3
"""Emit rapidfuzz-sourced scorer parity goldens for the TS port.

Writes tests/parity/fixtures/scorer-rapidfuzz.json: rows of
[scorer, a, b, expected]. `expected` is rapidfuzz's normalized_similarity
(jaro/jaro_winkler/levenshtein), the token_sort base (normalize + Indel), or
exact 1/0. This is the BINDING oracle the pure-TS scorers must match to 4dp.

rapidfuzz 3.14.5. Deterministic (seeded). Imports rapidfuzz ONLY (no goldenmatch).
Run: PYTHONIOENCODING=utf-8 /d/show_case/goldenmatch/.venv/Scripts/python \
        packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py
"""
import json
import random
import re
from pathlib import Path

from rapidfuzz.distance import Indel, Jaro, JaroWinkler, Levenshtein

OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript/goldenmatch/tests/parity/fixtures/scorer-rapidfuzz.json"
)

def _token_sort_norm(s: str) -> str:
    toks = sorted(t for t in re.sub(r"[^a-z0-9\s]", " ", s.lower()).split() if t)
    return " ".join(toks)

def _score(scorer: str, a: str, b: str) -> float:
    if scorer == "jaro":
        return Jaro.normalized_similarity(a, b)
    if scorer == "jaro_winkler":
        return JaroWinkler.normalized_similarity(a, b)
    if scorer == "levenshtein":
        return Levenshtein.normalized_similarity(a, b)
    if scorer == "token_sort":
        return Indel.normalized_similarity(_token_sort_norm(a), _token_sort_norm(b))
    if scorer == "exact":
        return 1.0 if a == b else 0.0
    raise ValueError(scorer)

EMOJI = "\U0001F600"  # grinning face (one codepoint, two UTF-16 code units)

# Named anchors that MUST appear (the divergence red->green targets + canon).
ANCHORS = [
    ("jaro", "dabaeb", "dbea"),                 # transposition floor target -> 0.8056
    ("jaro_winkler", "ad", "abaed"),            # boost-threshold target -> 0.5667
    ("jaro", EMOJI + "ab", EMOJI + "ac"),       # non-BMP jaro -> 0.7778
    ("jaro_winkler", EMOJI + "ab", EMOJI + "ac"),   # non-BMP jw -> 0.8222
    ("levenshtein", EMOJI + "ab", EMOJI + "ac"),
    ("jaro_winkler", "café", "cafe"),
    ("levenshtein", "café", "cafe"),
    ("token_sort", "Café Bar", "bar café"),
    # canonical references (must stay byte-stable)
    ("jaro_winkler", "MARTHA", "MARHTA"),
    ("jaro_winkler", "DIXON", "DICKSONX"),
    ("jaro_winkler", "DWAYNE", "DUANE"),
    ("jaro_winkler", "John", "Jon"),
    ("jaro", "MARTHA", "MARHTA"),
    ("levenshtein", "kitten", "sitting"),
    ("token_sort", "John Smith", "Smith Johnson"),
    ("exact", "abc", "abc"),
    ("exact", "abc", "xyz"),
    ("jaro_winkler", "", ""),
    ("jaro_winkler", "abc", ""),
]

random.seed(2026)
POOLS = ["abcde", "abcdefghijklmnop", EMOJI + "\U0001F601ab", "éüname"]

rows: list[list] = [[s, a, b, round(_score(s, a, b), 6)] for s, a, b in ANCHORS]
for _ in range(120):
    pool = random.choice(POOLS)
    a = "".join(random.choice(pool) for _ in range(random.randint(0, 9)))
    b = "".join(random.choice(pool) for _ in range(random.randint(0, 9)))
    for s in ("jaro", "jaro_winkler", "levenshtein"):
        rows.append([s, a, b, round(_score(s, a, b), 6)])

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps({"_rapidfuzz_version": "3.14.5", "cases": rows}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)
print(f"wrote {len(rows)} cases -> {OUT}")
```

- [ ] **Step 2: Run it — verify the fixture lands with the named anchors**

Run:
```bash
cd /d/show_case/goldenmatch/.worktrees/scorer-rapidfuzz-parity
PYTHONIOENCODING=utf-8 /d/show_case/goldenmatch/.venv/Scripts/python \
  packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py
```
Expected: `wrote ~379 cases -> ...scorer-rapidfuzz.json`. Spot-check with the oracle that `jaro('dabaeb','dbea')≈0.8056`, `jaro_winkler('ad','abaed')≈0.5667`, `jaro_winkler('😀ab','😀ac')≈0.8222` appear in the file.

- [ ] **Step 3: Commit the emitter + fixture**
```bash
git add -f docs/superpowers/plans/2026-06-12-scorer-rapidfuzz-parity.md
git add packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py \
        packages/typescript/goldenmatch/tests/parity/fixtures/scorer-rapidfuzz.json
git commit -m "test(ts): rapidfuzz-sourced scorer parity goldens + emitter"
```

---

## Task 2: failing parity test (the gate)

**Files:**
- Create: `packages/typescript/goldenmatch/tests/parity/scorer-rapidfuzz.test.ts`

- [ ] **Step 1: Write the test (3 named per-divergence cases + fixture loop)**

`tests/parity/scorer-rapidfuzz.test.ts`:
```ts
/**
 * rapidfuzz parity for the hand-rolled string scorers. The pure-TS jaro /
 * jaroWinkler / levenshtein / indel must match rapidfuzz (the engine the Rust
 * score-core, the Python wheel, and the Python goldens all use) to 4 decimals,
 * INCLUDING non-BMP, accented, sub-0.7-prefix, and repeated-char inputs.
 *
 * Goldens: tests/parity/fixtures/scorer-rapidfuzz.json (emit_scorer_parity_fixtures.py).
 */
import { describe, it, expect } from "vitest";
import { scoreField, jaro } from "../../src/core/index.js";
import fixture from "./fixtures/scorer-rapidfuzz.json" with { type: "json" };

type Case = readonly [scorer: string, a: string, b: string, expected: number];
const CASES = fixture.cases as readonly Case[];

const score = (scorer: string, a: string, b: string): number =>
  scorer === "jaro" ? jaro(a, b) : (scoreField(a, b, scorer) as number);

// Named red->green targets — one per divergence (clear failure messages).
describe("scorer rapidfuzz parity — named divergences", () => {
  it("transposition floors t/2 (jaro 'dabaeb'/'dbea' = 0.8056)", () => {
    expect(jaro("dabaeb", "dbea")).toBeCloseTo(0.8056, 4);
  });
  it("Winkler boost only above jaro>0.7 (jaro_winkler 'ad'/'abaed' = 0.5667)", () => {
    expect(scoreField("ad", "abaed", "jaro_winkler")).toBeCloseTo(0.5667, 4);
  });
  it("codepoint iteration on non-BMP (jaro '😀ab'/'😀ac' = 0.7778)", () => {
    expect(jaro("\u{1F600}ab", "\u{1F600}ac")).toBeCloseTo(0.7778, 4);
  });
});

describe("scorer rapidfuzz parity — full fixture (4dp)", () => {
  for (const [scorer, a, b, expected] of CASES) {
    it(`${scorer}(${JSON.stringify(a)}, ${JSON.stringify(b)}) ≈ ${expected}`, () => {
      expect(score(scorer, a, b)).toBeCloseTo(expected, 4);
    });
  }
});
```

- [ ] **Step 2: Run it — verify it FAILS** (CI, or local if you installed)

Run: `npx vitest run tests/parity/scorer-rapidfuzz.test.ts`
Expected: **FAIL** — the three named cases fail (0.7639 vs 0.8056; 0.6100 vs 0.5667; code-unit jaro vs 0.7778) and the matching fixture rows fail. (If running in CI: push and confirm the `scorer-rapidfuzz` cases are red before applying fixes.)

- [ ] **Step 3: Commit the failing test**
```bash
git add packages/typescript/goldenmatch/tests/parity/scorer-rapidfuzz.test.ts
git commit -m "test(ts): failing rapidfuzz scorer parity gate (RED)"
```

---

## Task 3: codepoint fix (divergence 3)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/scorer.ts`

- [ ] **Step 1: Rewrite `jaro` to index codepoints** (keep float `/2` for now — the floor fix is Task 5; this step targets the non-BMP named case)

Replace the body of `jaro` (scorer.ts ~114-154) with:
```ts
export function jaro(a: string, b: string): number {
  if (a === b) return 1.0;
  const ca = Array.from(a);
  const cb = Array.from(b);
  const lenA = ca.length;
  const lenB = cb.length;
  if (lenA === 0 || lenB === 0) return 0.0;

  const matchWindow = Math.max(Math.floor(Math.max(lenA, lenB) / 2) - 1, 0);
  const aMatched = new Uint8Array(lenA);
  const bMatched = new Uint8Array(lenB);
  let matches = 0;

  for (let i = 0; i < lenA; i++) {
    const lo = Math.max(0, i - matchWindow);
    const hi = Math.min(lenB - 1, i + matchWindow);
    for (let j = lo; j <= hi; j++) {
      if (bMatched[j] !== 0 || ca[i] !== cb[j]) continue;
      aMatched[i] = 1;
      bMatched[j] = 1;
      matches++;
      break;
    }
  }
  if (matches === 0) return 0.0;

  let transpositions = 0;
  let k = 0;
  for (let i = 0; i < lenA; i++) {
    if (aMatched[i] === 0) continue;
    while (bMatched[k] === 0) k++;
    if (ca[i] !== cb[k]) transpositions++;
    k++;
  }

  return (
    (matches / lenA + matches / lenB + (matches - transpositions / 2) / matches) / 3
  );
}
```

- [ ] **Step 2: Codepoint-index the prefix scan in `jaroWinkler`** (boost gate is Task 4)

Replace `jaroWinkler` (scorer.ts ~160-173) with:
```ts
export function jaroWinkler(a: string, b: string): number {
  const jaroSim = jaro(a, b);
  if (jaroSim === 0.0) return 0.0;

  const ca = Array.from(a);
  const cb = Array.from(b);
  const maxPrefix = Math.min(4, Math.min(ca.length, cb.length));
  let prefix = 0;
  for (let i = 0; i < maxPrefix; i++) {
    if (ca[i] === cb[i]) prefix++;
    else break;
  }

  return jaroSim + prefix * 0.1 * (1 - jaroSim);
}
```

- [ ] **Step 3: Codepoint-index `levenshteinDistance` + `levenshteinSimilarity`**

`levenshteinDistance` (~178-205) — swap to codepoint arrays:
```ts
export function levenshteinDistance(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const lenA = ca.length;
  const lenB = cb.length;
  if (lenA === 0) return lenB;
  if (lenB === 0) return lenA;

  let prev = new Uint32Array(lenB + 1);
  let curr = new Uint32Array(lenB + 1);
  for (let j = 0; j <= lenB; j++) prev[j] = j;

  for (let i = 1; i <= lenA; i++) {
    curr[0] = i;
    for (let j = 1; j <= lenB; j++) {
      const cost = ca[i - 1] === cb[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j]! + 1, curr[j - 1]! + 1, prev[j - 1]! + cost);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[lenB]!;
}
```
`levenshteinSimilarity` (~210-215) — use codepoint length for `maxLen`:
```ts
export function levenshteinSimilarity(a: string, b: string): number {
  if (a === b) return 1.0;
  const maxLen = Math.max(Array.from(a).length, Array.from(b).length);
  if (maxLen === 0) return 1.0;
  return 1 - levenshteinDistance(a, b) / maxLen;
}
```

- [ ] **Step 4: Codepoint-index `indelDistance` + `indelSimilarity`** (drop `charCodeAt`)

`indelDistance` (~225-247):
```ts
export function indelDistance(a: string, b: string): number {
  if (a === b) return 0;
  const ca = Array.from(a);
  const cb = Array.from(b);
  const m = ca.length;
  const n = cb.length;
  if (m === 0) return n;
  if (n === 0) return m;

  let prev = new Uint32Array(n + 1);
  let curr = new Uint32Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      if (ca[i - 1] === cb[j - 1]) {
        curr[j] = prev[j - 1]!;
      } else {
        curr[j] = Math.min(prev[j]! + 1, curr[j - 1]! + 1);
      }
    }
    [prev, curr] = [curr, prev];
  }
  return prev[n]!;
}
```
`indelSimilarity` (~253-257) — codepoint length for `total`:
```ts
export function indelSimilarity(a: string, b: string): number {
  const total = Array.from(a).length + Array.from(b).length;
  if (total === 0) return 1.0;
  return 1 - indelDistance(a, b) / total;
}
```

- [ ] **Step 5: Run the test — non-BMP case + non-BMP fixture rows now pass; transposition + boost still RED**

Run: `npx vitest run tests/parity/scorer-rapidfuzz.test.ts` (CI or local)
Expected: the "codepoint iteration on non-BMP" named case PASSES; the transposition + boost named cases still FAIL.

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit`
Expected: clean. (Watch `noUncheckedIndexedAccess` on `ca[i]`/`cb[j]` inside the bounded loops — they mirror the existing `a[i]` access; `prev[j]!` etc. keep their `!`.)

- [ ] **Step 7: Commit**
```bash
git add packages/typescript/goldenmatch/src/core/scorer.ts
git commit -m "fix(ts): codepoint iteration in jaro/jaroWinkler/levenshtein/indel (non-BMP parity)"
```

---

## Task 4: Winkler boost threshold (divergence 1)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/scorer.ts` — `jaroWinkler`

- [ ] **Step 1: Gate the prefix bonus on `jaro > 0.7`**

In `jaroWinkler`, replace the final `return` with:
```ts
  // rapidfuzz applies the Winkler prefix bonus ONLY when jaro > 0.7 (strict).
  if (jaroSim <= 0.7) return jaroSim;
  return jaroSim + prefix * 0.1 * (1 - jaroSim);
```

- [ ] **Step 2: Run the test — boost named case + sub-0.7 fixture rows pass; transposition still RED**

Run: `npx vitest run tests/parity/scorer-rapidfuzz.test.ts`
Expected: "Winkler boost only above jaro>0.7" PASSES; "transposition floors t/2" still FAILS.

- [ ] **Step 3: Commit**
```bash
git add packages/typescript/goldenmatch/src/core/scorer.ts
git commit -m "fix(ts): apply Jaro-Winkler prefix boost only when jaro>0.7 (rapidfuzz parity)"
```

---

## Task 5: transposition floor (divergence 2)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/scorer.ts` — `jaro`

- [ ] **Step 1: Floor the transposition halving**

In `jaro`, change the final `return` so `transpositions / 2` becomes `Math.floor(transpositions / 2)`:
```ts
  return (
    (matches / lenA +
      matches / lenB +
      (matches - Math.floor(transpositions / 2)) / matches) /
    3
  );
```

- [ ] **Step 2: Run the test — ALL named cases + the full fixture now GREEN**

Run: `npx vitest run tests/parity/scorer-rapidfuzz.test.ts`
Expected: **PASS** (3 named + every fixture row, 4dp). This is the integration gate.

- [ ] **Step 3: Commit**
```bash
git add packages/typescript/goldenmatch/src/core/scorer.ts
git commit -m "fix(ts): floor Jaro transposition count t//2 (rapidfuzz parity)"
```

---

## Task 6: no-regression sweep + downstream-snapshot audit

**Files:** (read-only audit; modify only if a stale snapshot is found)

- [ ] **Step 1: Existing scorer goldens + scorer unit test still green**

Run:
```bash
npx vitest run tests/parity/scorer-ground-truth.test.ts tests/unit/scorer.test.ts
```
Expected: PASS unchanged. (Confirms the anchors did not shift — proves the fix only moved the divergent region.)

- [ ] **Step 2: Grep for any TS-only snapshot that pinned a pre-alignment scorer value**

Run:
```bash
grep -rnE "0\.(76|61|75)[0-9]{2}|jaro_winkler|toBeCloseTo" \
  packages/typescript/goldenmatch/tests \
  | grep -viE "scorer-rapidfuzz|scorer-ground-truth" | head -40
```
For each hit, judge whether it asserts a jaro/jaroWinkler/levenshtein value on a **non-BMP / sub-0.7-prefix / repeated-char** input. If so, regenerate it from the oracle and update; if it's a Python-derived parity fixture that now fails, re-emit it from Python (Python is rapidfuzz — re-derive, don't paper over). Most hits will be unrelated (PPRL/dice/jaccard, clustering thresholds). Document any change in the commit.

- [ ] **Step 3: Broader parity sanity (the suites most likely to consume scorer values)**

Run (CI or local):
```bash
npx vitest run tests/parity/config-optimizer.test.ts tests/parity/heavy-algorithms.parity.test.ts
```
Expected: PASS. These run dedupe/eval on margin-verified datasets (≥0.10 from any swept threshold per `goldenmatch/CLAUDE.md`), so 4dp scorer moves can't flip a trial. If one fails on a divergent pair, re-emit that fixture from Python.

- [ ] **Step 4: Commit any snapshot regen** (skip if Steps 1-3 were all clean)
```bash
git add -A && git commit -m "test(ts): regen downstream snapshots pinned to pre-alignment scorer values"
```

---

## Task 7: changelog + docs

**Files:**
- Modify: `packages/typescript/goldenmatch/CHANGELOG.md`

- [ ] **Step 1: Add an Unreleased entry**

Prepend under the existing Unreleased/top section of `CHANGELOG.md`:
```markdown
### Unreleased
- Scorer parity: `jaro`/`jaroWinkler`/`levenshtein`/`indel` now match rapidfuzz
  to 4 decimals on non-BMP (codepoint iteration), sub-0.7-prefix (Winkler boost
  gated on `jaro>0.7`), and repeated-char (floored transposition `t//2`) inputs.
  Existing canonical anchors (MARTHA, DIXON, …) are unchanged. New gate:
  `tests/parity/scorer-rapidfuzz.test.ts` (goldens from `rapidfuzz` 3.14.5).
```

- [ ] **Step 2: Typecheck + the gate one last time**

Run:
```bash
npx tsc --noEmit && npx vitest run tests/parity/scorer-rapidfuzz.test.ts tests/parity/scorer-ground-truth.test.ts
```
Expected: clean typecheck + all PASS.

- [ ] **Step 3: Commit**
```bash
git add packages/typescript/goldenmatch/CHANGELOG.md
git commit -m "docs(ts): changelog for scorer rapidfuzz parity"
```

---

## Done-when (PR A acceptance)

- `tests/parity/scorer-rapidfuzz.test.ts` passes in CI: 3 named divergence cases + the full rapidfuzz-sourced fixture, 4dp, incl. non-BMP/accented/sub-0.7/repeated-char.
- `scorer-ground-truth.test.ts` + `scorer.test.ts` unchanged and green (no anchor shifted).
- Downstream parity suites green (or re-emitted from Python with a documented commit).
- `npx tsc --noEmit` clean.
- PR opened off `feat/scorer-rapidfuzz-parity` (base **main**); merge-on-green per `feedback_branch_merge_sop`.

## Out of scope (PR B / later)

The opt-in WASM backend, the `score-wasm` crate, `enableWasm()`, token_sort WASM coverage, and the per-core slices — all in `2026-06-12-opt-in-wasm-rust-acceleration-design.md` and its slice plans.
```
