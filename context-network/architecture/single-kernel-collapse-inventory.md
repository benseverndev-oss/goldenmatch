# Single-Kernel-Collapse — R0 Duplication Inventory

**Status:** Spike (R0) • **Compiled:** 2026-06-14 • **Decision:** [../decisions/0016-single-kernel-collapse-spike.md](../decisions/0016-single-kernel-collapse-spike.md)

Read-only inventory of every algorithm that exists in **two or more**
implementations across the suite. This is the evidence base for whether to
collapse the N duplicated implementations toward one shared Rust `*-core` kernel.
Zero behavior change — counts come from `wc -l`, nothing here is imported by any
default path.

## How to read the table

- **Rust core crate** — the pyo3-free `*-core` crate that is (or could be) the
  single source of truth. Several already exist (`score-core`, `graph-core`,
  `fingerprint-core`, `analysis-core`, `goldencheck-core`); where one exists the
  Python/TS/SQL surfaces are *candidates to collapse onto it*, where one is
  blank it would have to be created.
- **tag** — `kernelizable-hot` = a tight numeric/string inner loop that is the
  same math everywhere and is a perf hot path (high collapse ROI, low semantic
  risk). `orchestration-glue` = pipeline wiring / config / I/O that differs
  legitimately per language and is NOT a collapse target (low ROI, high risk).
- **rank** — ROI × inverse-risk, 1 = collapse first. Driven by: does a `*-core`
  crate already exist (lower risk), is parity already asserted (lower risk), is
  it a measured hot path (higher ROI), how many surfaces duplicate it (higher
  ROI).

## The duplication table

| # | Algorithm | Rust core crate | pure-Python site (LOC) | pure-TS site (LOC) | SQL / FFI site | tag | rank |
|---|-----------|-----------------|------------------------|--------------------|----------------|-----|------|
| 1 | **String scorers** (jaro_winkler / levenshtein / token_sort / exact) | `score-core/src/lib.rs` (137) ✅ | `core/scorer.py::score_field` (1694) — via rapidfuzz | `core/scorer.ts` jaro/lev/tokenSort (1042) — hand-rolled | `native/src/score.rs` shims; `datafusion-udf`; `score-wasm`; pg `kernels.rs` | kernelizable-hot | **1** |
| 2 | **Record fingerprint / hash** (type-tagged, key-sorted SHA-256) | `fingerprint-core/src/lib.rs` (178) ✅ | `core/_hashing.py` (161) | `core/record-fingerprint.ts` (80) | `native/src/hash.rs`; pg `goldenmatch_record_fingerprint` | kernelizable-hot | **2** |
| 3 | **Clustering / graph primitives** (connected-components, union-find, MST split) | `graph-core` (504) ✅ | `core/cluster.py` (1981) | `core/cluster.ts` (753) | `native/src/cluster.rs`; pg `goldenmatch_connected_components`; distributed WCC | kernelizable-hot | **3** |
| 4 | **Transforms / standardizers** (lower/strip, ngram, bloom_filter CLK, soundex) | none (candidate: `transform-core`) | `utils/transforms.py` (224) + `core/standardize.py` | `core/transforms.ts` (599) | `native/src/bloom.rs` (CLK only); `goldenflow_*` UDFs | kernelizable-hot (mixed) | **4** |
| 5 | **PPRL bloom / dice / jaccard** | partial — `native/src/bloom.rs` (CLK hash) | `pprl/protocol.py` (328) + `utils/transforms.py` bloom | `core/pprl/protocol.ts` (373) | bloom CLK in `native`; no SQL PPRL (deferred-by-design) | kernelizable-hot | **5** |
| 6 | **Fellegi-Sunter comparison-vector / weight math** (m/u, log-likelihood, levels) | none (candidate: `fs-core`) | `core/probabilistic.py` (1998) | `core/probabilistic.ts` (819) | `native/src/score.rs::score_block_pairs_fs` (partial) | kernelizable-hot | **6** |
| 7 | **Char-n-gram featurization** (in-house embedder feature hashing) | inside `native/src/featurize.rs` (candidate: extract `featurize-core`) | `core/embedder` CharNGramFeaturizer | (not ported — no torch in TS) | `native::char_ngram_features` | kernelizable-hot | 7 |
| 8 | **Pair canonicalization / dedup** ((min,max), max-score reduce, histogram) | `native/src/pairs.rs` (candidate: pyo3-free split) | `core/cluster.py` / `chunked.py` pair handling | `core/cluster.ts` pair handling | `native::canonicalize_pairs`, `dedup_pairs_max_score` | kernelizable-hot | 8 |
| 9 | **Analysis aggregates** (histogram / quantile) | `analysis-core/src/lib.rs` (118) ✅ | goldenanalysis Python aggregate | `goldenanalysis/src/core/aggregate.ts` | `analysis-native`; `analysis-wasm` | kernelizable-hot | 9 |
| 10 | **GoldenCheck profilers / cell-quality** | `goldencheck-core` (760) ✅ | `goldencheck/cell_quality.py` (120) + profilers | (goldencheck TS port, partial) | `goldencheck-native` | kernelizable-hot (mixed) | 10 |
| 11 | Pipeline orchestration (ingest→block→score→cluster→golden) | n/a | `core/pipeline.py` | `src/index.ts` / pipeline | n/a | orchestration-glue | — (do NOT collapse) |
| 12 | Auto-config controller / planner rules | n/a | `core/autoconfig*.py` | `core/config-optimizer.ts` etc. | n/a | orchestration-glue | — (do NOT collapse) |

## Top findings (rank order)

1. **String scorers (#1) are the cleanest, highest-ROI collapse and the right
   tracer.** `score-core` already exists and is ALREADY the single source of
   truth for the Python `native` wheel, the DataFusion FFI UDFs, and the
   `score-wasm` TS backend — by construction (the shims delegate; `lib.rs`
   header says "parity is structural, not asserted after the fact"). The
   *duplication that remains* is the pure-Python `scorer.py` (rapidfuzz) and the
   **hand-rolled** pure-TS `scorer.ts`, which are kept in sync only by parity
   harnesses, not by sharing code. This is exactly where the spike puts its
   tracer (levenshtein). **Already measured this spike:** pure==kernel at 4dp
   (Python: bit-identical; see §evidence in the decision record).
2. **Record fingerprint (#2) is the next-safest.** `fingerprint-core` exists,
   the Python loader already gates `hashing` ON, and parity is asserted
   **byte-for-byte** with pinned golden vectors (`_native_loader.py` comments).
   Integer/SHA-256 only — no float tolerance, no threshold-crossing risk. A
   collapse here can't change a record id.
3. **Clustering/graph (#3) has the crate (`graph-core`) and is gated ON**
   (`clustering`, `pairs` in `_GATED_ON`) but the pure-Python `cluster.py` is
   the largest single duplicated file (1981 LOC) and carries real orchestration
   glue (oversized-split policy, confidence scoring) tangled with the hot loop —
   so only the *primitives* are kernelizable; the policy stays per-language.
4. **The biggest LOC duplication is Fellegi-Sunter (#6): 1998 (Py) + 819 (TS)
   with NO shared core crate.** High ROI by line count, but higher risk: the
   EM/weight math has float sensitivity and the per-language code is entangled
   with config. Deferred to a later stage (R3) behind the proven scorer template.
5. **Transforms (#4) and PPRL (#5) are partially kernelized** (only the bloom
   CLK hash loop is in Rust today). The bulk of `transforms.py` (224) /
   `transforms.ts` (599) is still duplicated pure code with no `transform-core`.
   Medium ROI; the TS `transforms.ts` is notably larger (it carries the
   edge-safe Web-Crypto SHA-256/HMAC), a sign the two are NOT trivially the same.
6. **Five `*-core` crates already exist** (`score-core`, `graph-core`,
   `fingerprint-core`, `analysis-core`, `goldencheck-core`), so the collapse is
   less "build the kernel" and more "retire the duplicated pure copies onto
   crates that already ship." That materially lowers the risk profile of the
   first three ranks.

## What is explicitly NOT a collapse target

Rows 11–12 (and the broader pipeline/controller/CLI/connector layer) are
`orchestration-glue`: the Python and TS implementations differ for sound reasons
(edge-safety vs Polars/Ray, REST/web UI Python-only — see the TS package
CLAUDE.md "Python-only by design" + the SQL "deferred-by-design" boundary).
Collapsing these would couple unrelated runtimes and is out of scope at every
stage. The kernel collapse is about the **algorithm math**, not the wiring.
