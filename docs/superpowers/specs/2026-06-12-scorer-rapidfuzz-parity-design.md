# Pure-TS scorer ⇄ rapidfuzz parity (3 divergences, one change)

**Date:** 2026-06-12
**Status:** Draft (design)
**Author:** Ben Severn (with Claude)
**Sequencing:** PR A — the anchor that unblocks the opt-in WASM slice
(`2026-06-12-opt-in-wasm-rust-acceleration-design.md`, PR B) and every
later per-core slice.

## Problem

`packages/typescript/goldenmatch/src/core/scorer.ts` reimplements
Jaro / Jaro-Winkler / Indel / Levenshtein by hand. The Rust `score-core`
crate — which backs the Python native wheel, the DataFusion UDFs, and the
forthcoming `score-wasm` TS backend — uses **rapidfuzz**
(`jaro_winkler::normalized_similarity(a.chars(), b.chars())`). The hand-rolled
TS diverges from rapidfuzz in three ways. All three are **latent today**: the
parity corpus (`tests/parity/scorer-ground-truth.test.ts`) only probes ASCII,
jaro ≥ 0.7, low-repeat strings, so nothing exercises them.

This matters now because the WASM slice asserts **WASM ≈ pure-TS ≈ Python
goldens (4dp)** over a corpus that *will* include non-BMP and sub-0.7 cases.
With the divergences in place that gate cannot go green. Aligning pure-TS *to*
rapidfuzz simultaneously (a) closes latent gaps against the Python parity
contract — Python is rapidfuzz too — and (b) makes the WASM parity gate
achievable. One change, three divergences.

## The three divergences (empirically measured)

Measured by re-implementing the **current** pure-TS algorithm in Python and
diffing against `rapidfuzz` 3.14.5 over a 40k random-pair corpus
(`alphabet="abcde"`, lengths 2–7), plus targeted boundary probes.

1. **Boost threshold (Jaro-Winkler).** rapidfuzz applies the prefix bonus
   **only when `jaro > 0.7`**; the current `jaroWinkler` (scorer.ts:160-173)
   applies `jaro + prefix·0.1·(1−jaro)` **unconditionally**. → 5054/40000 pairs
   diverge. Example `'ad'/'abaed'`: jaro 0.5667, rapidfuzz_jw **0.5667**,
   pure-TS_jw **0.6100**. Effect: every low-similarity pair with a shared
   prefix is over-scored by pure-TS.

2. **Transposition / match-assignment (Jaro).** On inputs with repeated
   characters in the match window the greedy left-to-right assignment in
   `jaro` (scorer.ts:114-154) yields a **different transposition count** than
   rapidfuzz (rapidfuzz scores higher). → 766/40000 pairs diverge. Example
   `'dabaeb'/'dbea'`: both find 4 matches, but rapidfuzz counts t=2
   (**0.8056**) vs pure-TS t=3 (**0.7639**). This is not a one-line patch — it
   needs rapidfuzz's match-flagging + transposition semantics.

3. **Non-BMP / codepoint vs UTF-16 code unit.** rapidfuzz iterates Unicode
   **codepoints** (`a.chars()`); the TS impls index **UTF-16 code units**
   (`a[i]`, `a.length`, `charCodeAt`). A non-BMP char (e.g. 😀 = one codepoint,
   two code units) is silently split. Affects **every** char-indexed scorer:
   jaro, jaroWinkler, levenshtein, indel (→ token_sort). Example `'😀ab'/'😀ac'`:
   rapidfuzz jaro **0.7778** / indel **0.6667**; JS code-unit indexing gives
   jaro over a 4-unit string and indel **0.75**.

## Goals

- Pure-TS `jaro`, `jaroWinkler`, `levenshteinDistance/Similarity`,
  `indelDistance/Similarity` match rapidfuzz to **4 decimals** over a corpus
  that probes all three divergence classes.
- `tokenSortRatio` inherits the codepoint fix (it composes `indelSimilarity`
  over normalized tokens) and stays at parity.
- The parity corpus is **extended** and its goldens **regenerated from Python
  rapidfuzz** (the binding oracle), committed so the contract is locked.
- No regression in the existing scorer / downstream parity suites.

## Non-goals

- The WASM backend, the `score-wasm` crate, `enableWasm()` — that is **PR B**
  (the slice-1 plan). This PR is pure-TS only.
- `token_sort` *WASM coverage* (resolving `score-core`'s no-normalize
  asymmetry) — also PR B / item 2.
- Changing the Python or Rust sides. They are already rapidfuzz; TS moves
  to meet them.

## Approach

### Codepoint normalization (divergence 3)

At the top of each string scorer, convert once to a codepoint array
(`const ca = Array.from(a)` — spread/`Array.from` iterate by codepoint), then
index `ca`/`ca.length` instead of `a`/`a.length`/`charCodeAt`. This is the
single mechanical change that fixes non-BMP across jaro, jaroWinkler,
levenshtein, indel. Compare codepoints by string equality (`ca[i] === cb[j]`),
dropping `charCodeAt`. Cost: one `Array.from` per call (the matrix path already
amortizes over NxN; acceptable — the WASM slice is where perf is chased).

### Boost threshold (divergence 1)

`jaroWinkler`: compute `jaro` first; apply the prefix bonus **only if
`jaro > 0.7`** (strict — rapidfuzz's `JARO_WINKLER` boost threshold). Below the
threshold return the raw jaro. Prefix cap stays at 4, weight 0.1. The
exact boundary behavior at `jaro == 0.7` is whatever the regenerated goldens
capture (rapidfuzz is the oracle); the corpus includes a jaro≈0.7 case.

### Transposition (divergence 2)

Replace the greedy `jaro` matching+transposition with a faithful port of
rapidfuzz-cpp's `jaro_similarity` flagging algorithm: build per-string match
flags within the window, then count transpositions by walking the two flagged
subsequences in order. The match **count** is unchanged from the formal Jaro
definition; only the assignment/transposition count is corrected. Validate by
4dp parity over repeated-char inputs — the binding test is the corpus, not a
hand proof.

### Scope of files

Only `scorer.ts`'s metric functions change. `scoreField`'s dispatch, the
0.95/0.7 short-circuit at scorer.ts:436 (an unrelated **scoreField-level**
early-exit, not the Winkler boost threshold — keep it), PPRL/bloom, soundex,
hashing — all untouched.

## Goldens & corpus

- A Python emitter (`scripts/emit_scorer_parity_fixtures.py` under the Python
  goldenmatch package, alongside the other `emit_*` scripts) writes a JSON
  fixture of `[scorer, a, b, expected]` rows generated **directly from
  rapidfuzz** (`Jaro`, `JaroWinkler`, `Indel`, `Levenshtein` normalized
  similarity; token_sort via the Python scorer). Deterministic (seeded).
- Corpus **must** include, per class: sub-0.7-with-prefix (boost threshold),
  repeated-char (transposition), non-BMP (😀, surrogate pairs) and combining /
  accented (café) inputs, plus the existing MARTHA/DIXON/DWAYNE anchors and
  empty/identical edge cases.
- The inline `CASES` in `scorer-ground-truth.test.ts` and the direct `jaro`
  goldens (`describe("jaro parity …")`, scorer-ground-truth.test.ts:93) are
  updated to the regenerated values. **Some currently-committed values will
  shift** (that is the point — they encode the divergent behavior); the new
  values come from rapidfuzz.

## Parity / testing

- Extend `tests/parity/scorer-ground-truth.test.ts` (or add
  `tests/parity/scorer-rapidfuzz.test.ts`) to load the regenerated fixture and
  assert 4dp. Keep the existing inline anchors.
- TDD order per the plan: write the failing fixture-backed test first (current
  pure-TS fails the new non-BMP / sub-0.7 / repeat rows), then fix each
  divergence until green.
- Run **single-file** vitest locally (box OOMs on the full suite —
  `feedback_box_memory_oom_ts`); the full suite is authoritative in CI.

## Risks

- **Downstream fixtures that pinned pre-alignment scorer values.** Python-
  derived parity fixtures (clustering, autoconfig, golden, optimizer) were
  generated from rapidfuzz, so aligning TS moves it *toward* them — they should
  still pass. The real risk is **TS-only snapshots** that captured the old
  divergent numbers. The plan must grep for and regenerate any such snapshot;
  if a downstream Python-parity fixture *breaks*, that is a latent corpus that
  happened to hit a divergent pair — re-derive it from Python, don't paper over.
- **Performance.** `Array.from` per call adds allocation. The pure-TS path is
  the fallback, not the perf path (that is WASM); acceptable. If a hot loop
  regresses materially, hoist the codepoint arrays in the matrix builder. Not
  expected to matter at this stage.
- **rapidfuzz version drift.** Goldens are pinned to rapidfuzz 3.14.5; the
  emitter records the version in a header comment. The algorithms here
  (Jaro/JaroWinkler/Indel/Levenshtein) are stable across rapidfuzz 3.x.

## Done-when

- New rapidfuzz-generated fixture committed; `scorer-ground-truth` +
  jaro-direct goldens updated to rapidfuzz values.
- jaro / jaroWinkler / levenshtein / indel / token_sort match rapidfuzz to 4dp
  over the extended corpus (non-BMP, sub-0.7-prefix, repeated-char included).
- Existing scorer + downstream parity suites green in CI (single-file locally).
- PR opened off `feat/scorer-rapidfuzz-parity`; merge-on-green per
  `feedback_branch_merge_sop`.
