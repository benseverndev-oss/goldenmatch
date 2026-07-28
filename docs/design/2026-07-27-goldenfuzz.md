# goldenfuzz: a cross-surface, byte-identical fuzzy-string kernel

**Status:** exploration (drilling started 2026-07-27, after #2159 landed the
bit-parallel `score-core::strsim`).
**TL;DR of the decision:** we already *own* rapidfuzz's core math in Rust and,
as of the single-word fast path (#2212), *beat* rapidfuzz per-pair on the
ER-common case (short strings), byte-identically. The differentiator for a
standalone product is **portability + provable cross-surface identity + domain
comparators**, not raw speed. Extract a named `goldenfuzz-core` crate now (cheap,
good hygiene); publish externally only once we commit to closing the two real
gaps (SIMD-batch `cdist`; a `process.extract`-style API).

---

## 1. Why this exists

rapidfuzz is excellent and battle-tested (10M+ downloads/mo, Python-first). We
did not set out to compete with it — we set out to *stop depending on it*, so the
"one authoritative semantic owner per capability" invariant holds for our scoring
math. #2159 did that: `score-core::strsim` is our own bit-parallel port of
jaro-winkler / levenshtein / indel / damerau, byte-identical to the rapidfuzz
crate (proven by an oracle fuzz).

Having owned it, the question Ben raised: *could this be a better product than
rapidfuzz?* This doc answers with measurements, not vibes.

## 2. Competitive position (measured)

`cargo run --release --example bench_vs_rapidfuzz` (score-core), goldenfuzz /
rapidfuzz 0.5, scalar per-pair, corrupted-pair corpus:

| workload            | jaro-winkler | levenshtein | indel |
|---------------------|-------------:|------------:|------:|
| names (~13 chars)   | **0.58x**    | **0.78x**   | **0.29x** |
| addresses (~35)     | **0.91x**    | **0.92x**   | **0.38x** |
| documents (~600)    | **0.91x**    | **1.00x**   | 1.61x |

`< 1.0` = goldenfuzz faster. **We now beat or match rapidfuzz on short fields AND
documents**, except document-indel (1.61x, an algorithm/SIMD gap, not allocation).

How the long-string gap was closed (it was NOT SIMD): the per-pair document cost
was dominated by the **multiword HashMap peq** — building the position bitmap for
a 600-char pattern cost **16.4us** (600 SipHash char-hashes + ~27 small `Vec`
allocs), a third of the 50us and more than the entire scan. Replacing it with a
byte-indexed *flat* multiword peq (~3us, no hashing/alloc) took document jaro
2.4x->0.91x and levenshtein 2.4x->1.00x, byte-identically. The residual
document-indel gap is the scan itself (SIMD territory). SIMD-batch `cdist` is a
*separate* one-vs-many axis.

How we got here:
- #2159: naive DP -> multiword bit-parallel. Big win on long strings, but
  allocation-bound on short ones (per-call HashMap peq + limb Vecs): a 13-char
  jaro was ~3600ns, **1.9x slower than the naive DP** and ~20x slower than
  rapidfuzz. A silent regression on the native FS field-scoring path.
- #2212: single-word fast path (ASCII `[u64;128]` on `&[u8]`, no `Vec<char>`;
  Latin-1 `[u64;256]` for accented names; multiword for CJK / len > 64). 3600ns
  -> 146ns; now faster than rapidfuzz. Byte-identical.

## 3. The real differentiator: one kernel, every surface

rapidfuzz is structurally Python-only. `score-core::strsim` is a pyo3-free Rust
crate already consumed, **byte-identically**, by:

| surface        | consumer                                   |
|----------------|--------------------------------------------|
| Python         | `goldenmatch._native` (pyo3)               |
| TypeScript/JS  | `score-wasm` (wasm)                        |
| SQL            | `datafusion-udf` (DuckDB / DataFusion)     |
| Rust cores     | `fs-core`, `goldengraph-core`, `goldenprofile-core`, `infermap-core` |
| pure-Python    | vendored `core/strsim.py` fallback (no native dep) |

Nobody offers "the same fuzzy-match bits in your notebook, your JS frontend, and
your SQL warehouse, provably identical, with a no-native-dep fallback." That is
the product wedge, and it is on-thesis for the golden suite. Plus domain
comparators rapidfuzz lacks: date / geo-haversine / q-gram / numeric-band /
array-jaccard / cosine.

## 4. Coverage gap vs rapidfuzz (to clear the bar as a *product*)

| capability                        | goldenfuzz | rapidfuzz |
|-----------------------------------|:----------:|:---------:|
| jaro-winkler / lev / indel / damerau | yes (byte-identical) | yes |
| token-sort / q-gram               | yes        | yes       |
| **SIMD-batch `cdist`** (one-vs-many) | **no**  | yes (the long-string win) |
| **`process.extract` / top-k API** | **no**     | yes       |
| `score_cutoff` early-exit pruning | partial    | yes       |
| cross-surface (Py/JS/SQL/wasm)    | **yes**    | no        |
| no-native-dep fallback            | **yes**    | no        |
| domain comparators (date/geo/...) | **yes**    | no        |

Gaps for an external "beats rapidfuzz everywhere" claim, in priority order:
1. **Multiword allocation-free peq** (measured lever) — the per-pair long-string
   gap is the HashMap peq (16.4us of 50us). Extend the byte/Latin-1 flat peq to
   the multiword tier (no HashMap, no `Vec<char>`). Tractable; likely closes the
   scalar document gap outright.
2. **`process.extract` / top-k** — the ergonomic one-vs-many entry everyone uses.
3. **SIMD-batch `cdist`** — a *separate* one-vs-many amortisation + SIMD-lane
   axis. Hardest; only needed to beat rapidfuzz's batch path, not its scalar one.

## 5. Roadmap + decision gates

- **Done** — own the math (#2159), beat rapidfuzz per-pair on short (#2212).
- **Next (cheap, low-risk): extract `goldenfuzz-core`.** Carve `strsim` into its
  own named crate; `score-core` depends on and re-exports it (zero behaviour
  change, zero fixture churn — byte-identical). Establishes the boundary and the
  name without committing to publish.
- **Done: multiword allocation-free (flat) peq.** Replaced the 16.4us HashMap
  peq; document jaro/levenshtein now beat/match rapidfuzz, byte-identically.
  Remaining per-pair gap is document-indel (1.61x, scan-bound → SIMD).
- **Gate to publish externally** (PyPI + npm + crates.io): only after a
  `process.extract` / top-k API exists (SIMD-batch `cdist` and the document-indel
  scan are optional — needed only to beat rapidfuzz's *batch* path / last scalar
  gap). Until then a public "rapidfuzz alternative" lacking top-k is not
  compelling. The pitch: *portability + provable identity + domain-awareness +
  now genuinely faster on the ER-common case*.
- **Not now:** a Python `goldenfuzz` wheel is a separate packaging/publish/triage
  burden on top of the 7-package suite. The internal crate keeps the option open
  at ~zero cost.

## 6. Effort / risk

- Crate extraction: ~1 focused PR, mechanical, low risk (pure move + re-export).
- Multiword allocation-free peq: ~1 PR, same technique as the short path,
  byte-identical; measured to remove the dominant long-string cost.
- `process.extract`: moderate; mostly API + `score_cutoff` plumbing.
- SIMD-batch `cdist`: real work (block-based bit-parallel across lanes), the
  hardest item; needed only for the external "beat the batch path too" claim.
- Publishing: not a code problem — semver, docs, benchmarks, issue triage.

The honest summary: goldenfuzz is already a *better fit* than rapidfuzz for the
golden suite's needs (cross-surface, no-native-dep, domain-aware, and now faster
on short strings). It is not yet a *better general-purpose product* — that needs
the two coverage gaps closed. Extract the crate; revisit publishing when we
decide to invest in `cdist` + `extract`.
