# Full `-core` kernel coverage for the scorer surface (5/19 -> full)

**Date:** 2026-07-21
**Status:** Proposed (scoping / design). **Landing incrementally:** `qgram` (5/19 -> 6/19) and `soundex_match` (6/19 -> 7/19) landed in Wave 1; `initialism_match` (7/19 -> 8/19) and `alias_match` (8/19 -> 9/19) are the first Wave-2 cuts; the baseline figures below describe the pre-work starting point.
**Motivation:** The generated suite-matrix reports **"5 of 19 scorers are kernel-backed"**
(`docs-site/suite-matrix.mdx`, computed by `gen_suite_matrix.py::_substrate_lines` from the
`scorer_kernels` parity surface). This spec scopes what it takes to close that gap -- and argues
that a literal "19/19" is the wrong target.

## Problem

The Rust / Arrow-native `-core` kernels are the reference implementation for scoring; each language
surface either dispatches to the kernel (the fast path) or runs a byte-identical pure-language
fallback. At the pre-PR baseline only **5 scorers** had a kernel:

- **shared** (Python arrow bucket kernel + TS WASM): `exact`, `jaro_winkler`, `levenshtein`, `token_sort`
- **python-only** (arrow bucket kernel, TS falls back): `date`

The other **14 were fallback-only** at that baseline: `dice`, `jaccard`, `qgram`, `soundex_match`,
`ensemble`, `embedding`, `record_embedding`, `alias_match`, `audio_fp`, `initialism_match`, `phash`,
`radial`, `given_name_aliased_jw`, `name_freq_weighted_jw`.

**Update:** `qgram` and `soundex_match` (Wave 1) and `initialism_match` + `alias_match` (Wave 2
start) are now kernelized -- each has a `score-core` kernel on the Python arrow-bucket fast path, so
the current state is **9/19** with **10** fallbacks remaining. All read as `python_only` in the
`scorer_kernels` partition (no TS WASM port yet, like `date`). `soundex_match`'s kernel replicates
`jellyfish.soundex` byte-for-byte INCLUDING its Unicode step (NFKD normalize + `str.upper` via the
`unicode-normalization` crate), so native==pure on leading non-alpha / accented input too -- which
also closes the same latent gap in the field-matrix path (both now dispatch the shared
`score-core::soundex`). `initialism_match` ports `derive_initialism` + `_initialism_match_single`
byte-for-byte into `score-core` (`score_one` id 7); because a per-pair kernel can't take a legal-form
table argument without breaking the uniform `(id, a, b)` dispatch, the ~77-entry
`entity_form_variants()` set is installed once into a process-global `OnceLock` via a native
`set_legal_form_variants` shim, and the Python fast-path guard routes native only when BOTH the
`initialism_similarity` capability symbol is present AND the install succeeded (else it declines to
the pure `_initialism_match_single` mirror). `alias_match` (`score_one` id 8) ports the business +
given-name canonicalization (`refdata.business_aliases.canonical_company_form` +
`given_names.canonical_form` + `_alias_match_single`) byte-for-byte: the kernel rebuilds the
`strip_legal_form` trailing-suffix regex from a host-shipped variant list (via the `regex` crate) and
looks up two host-installed maps -- the business `surface->canonical` map and a PRE-RESOLVED given-name
`normalized -> min(canonical)` map (the lex-first resolution is done host-side, so the kernel needs no
alias graph). Same two-part guard (`set_business_aliases`/`set_given_name_canonicals` +
`alias_match_similarity` symbol) declining to the pure `_alias_match_single` mirror.

"Kernel-backed" in the metric means *a kernel exists in at least one language* (a union), so the
denominator (19) also counts scorers that live in only one language. The metric is emitted by
`scripts/emit_python_surface.py::_scorer_kernels` (= the bucket `_NATIVE_SCORER_IDS` keys) and
`scripts/emit_ts_surface.mjs` (= `WASM_COVERED_SCORERS`), partitioned in `parity/goldenmatch.yaml`
and gated by `api_parity`.

## Why "19/19" is the wrong literal target

Three of the 14 are not string-scorer-kernel candidates at all:

1. **`embedding`, `record_embedding` are model-backed, not string primitives.** They embed values
   (Vertex / torch / MiniLM) and cosine them; `record_embedding` is record-level (multi-column),
   not field-level. Their acceleration is the **`goldenembed-core` / `goldenembed-wasm`** subsystem,
   not the rapidfuzz-style scorer kernel. Writing a "scorer kernel" for them is a category error.
   -> **Reframe out of the denominator** (or report them as "covered by goldenembed").
2. **`ensemble` is a meta-scorer** -- `max(jaro_winkler, token_sort, soundex_match * 0.8)`
   (`core/scorer.py:565`). It has no primitive of its own; once its three components are
   kernel-backed it is **kernel-backed by construction**. Nearly free, no new kernel.

So the real algorithmic surface to kernelize is **11 scorers**, and the honest end state is

> **"16 of 16 algorithmic scorers kernel-backed; the embedding pair delegated to goldenembed."**

Because the suite-matrix line is *generated*, it updates itself once the manifest changes -- no prose
edit needed. (Independently, the current wording should say "union across languages (4 in both)" so
the "5" is not misread as "5 in both languages" -- see Non-goals.)

## The three kernel families (not one effort)

### Family A -- clean string scorers (copy the `jaro_winkler` template)

String-in / float-out / NxN-matrixable, same shape as the 5 already kernel-backed. Highest value:
each replaces an O(N^2) pure-Python double loop.

| Scorer | Impl (file:line) | What it computes | Extra work vs template |
|---|---|---|---|
| `qgram` | `core/scorer.py:1019` (`_qgram_score_matrix:1036`) | char-trigram Jaccard on raw strings (`#`-padded n-gram sets) | none -- pure string primitive |
| `soundex_match` | `core/scorer.py:701`; field-map `id 4` at `:484` | `1.0` if `jellyfish.soundex(a)==soundex(b)` | **half-done** -- already in `_NATIVE_FIELD_SCORER_IDS`, not the bucket path; finish the wiring |
| `initialism_match` ✅ | `core/scorer.py:101`, `:716` | `1.0` if one string is the other's initialism (`derive_initialism`) | **landed** -- `score_one` id 7; needed the legal-form table in-kernel (installed once via a global `OnceLock` + `set_legal_form_variants` shim, keeping the `(id, a, b)` dispatch uniform) |
| `given_name_aliased_jw` | `refdata/scorer.py:177` | `max(jw, 1.0 if known given-name alias)` (William<->Bill) | needs the alias table in-kernel |
| `name_freq_weighted_jw` | `refdata/scorer.py:69` | `jw * (floor + (1-floor)*mean_rarity)` from census/`tf_freqs` | needs the freq table in-kernel; TS port is static-only (declared delta) |
| `alias_match` ✅ | `core/scorer.py:125`, `:741` | `1.0` if same business/given-name canonical | **landed** -- `score_one` id 8; ships the business alias + strip-legal-form variant + pre-resolved given-name maps in-kernel (the lex-first resolution done host-side), rebuilding the strip regex via the `regex` crate |

The in-kernel-table mechanism already exists: `native/src/score.rs::set_name_reference_data`
(`:56`) / `has_name_reference_data` (`:72`). `name_freq_weighted_jw` already threads a `tf_freqs=`
kwarg through `_fuzzy_score_matrix` and the plugin protocol.

### Family B -- bit / hex vectors (a different kernel, mostly wiring)

Not rapidfuzz string scorers; these consume hex-decoded bit vectors. In several cases the kernel
already exists elsewhere and only needs routing into the scorer path.

| Scorer | Impl (file:line) | What it computes | Note |
|---|---|---|---|
| `phash` | `core/scorer.py:889`, `:901` | `1 - hamming(hexA,hexB)/bits` on perceptual image hashes | a Rust `perceptual-core` phash/hamming kernel **already exists** (SQL surface) -- wire it into the scorer path |
| `dice` | `core/scorer.py:796`, `:812` | PPRL: `2*popcount(A&B)/(popA+popB)` over hex bloom filters | reuse the existing native bloom kernel (`bloom.rs`); mind the documented TS char-bigram `dice` divergence |
| `jaccard` | `core/scorer.py:804`, `:851` | PPRL: `popcount(A&B)/popcount(A|B)` over hex blooms | same as dice |

### Family C -- bespoke perceptual, low ROI

| Scorer | Impl (file:line) | Why deferred |
|---|---|---|
| `audio_fp` | `core/scorer.py:937`, `:944` | best-offset-aligned BER on hex audio fingerprints; **alignment search, not NxN-vectorizable**, symmetric pairwise loop |
| `radial` | `core/scorer.py:967`, `:976` | rotation-aligned Pearson on radial profiles; angular alignment search, same shape |

Blocks for these are small and the primitive is a per-pair alignment search, so the perf upside is
minimal. Recommend **defer or explicitly decline**.

## Waves

| Wave | Scorers | Risk | Rationale |
|---|---|---|---|
| **1** | `qgram` ✅, `soundex_match` ✅ (both landed) | low | pure strings on the proven template; `soundex_match` reused the existing Rust kernel, upgraded to full jellyfish Unicode parity |
| **2** | `initialism_match` ✅, `alias_match` ✅ (both landed), `given_name_aliased_jw`, `name_freq_weighted_jw` | medium | string base + refdata table shipped in-kernel (mechanism exists); table fidelity is the risk. `initialism_match` proved the in-kernel table mechanism (the ~77-entry `entity_form_variants()` set installed once into a `score-core` `OnceLock` via a native `set_legal_form_variants` shim, keeping `score_one(id, a, b)` uniform); `alias_match` extended it to two maps + a rebuilt-in-kernel strip regex |
| **3** | `phash`, `dice`, `jaccard` | **blocked** | NOT "just wiring" -- all three are matrix-semantics-dependent (see the note below), so the per-pair `score_one` pattern can't reach byte-parity; each needs a block-aware kernel or a resolved-semantics decision first |
| **free** | `ensemble` | trivial | composes Wave-1 kernels; kernel-backed by construction |
| **4 (defer/decline)** | `audio_fp`, `radial` | -- | bespoke alignment search, low perf upside |
| **n/a** | `embedding`, `record_embedding` | -- | model-backed; reframe under goldenembed, exclude from denominator |

Landing Waves 1-3 + `ensemble`, and reframing the embedding pair, takes the metric to full
algorithmic coverage.

> **NOTE — `ensemble` is NOT the "free" win this table implies.** A per-pair reimpl measurably
> regressed Febrl3 recall (0.922 → 0.782); it's deliberately *declined* from the bucket/fast path
> and stays on the float32 matrix ensemble (the source of truth). It's now marked `declined` in the
> coverage floor below; kernelizing it means re-opening that measurement. Likewise `dice`/`jaccard`
> are dual-semantics (bigram-set per-pair vs PPRL bloom-filter hex in the matrix path) and must be
> disambiguated first, and `phash` is matrix-semantics-dependent — `_phash_score_matrix` pads all
> block hashes to the block-GLOBAL max bit-length (`sim = 1 - dist/max_len`), which a per-pair
> `score_one` kernel can't replicate (it can only pad pairwise), so it diverges on mixed-length
> blocks. **The clean per-pair string primitives (qgram, soundex, initialism, alias) are exactly the
> ones that fit the `score_one` pattern; the rest need a block-aware kernel path or a semantics
> decision, which is why the pragmatic frontier lands at 9/19 + the coverage floor, not a forced
> 19/19.**

## Coverage floor (the gate that keeps the metric honest)

The reason `5 of 19` sat unaddressed for so long: the metric was **descriptive, not prescriptive**.
`api_parity` enforced that the `scorer_kernels` manifest *matched reality* (no Python↔TS drift) but
never that coverage was *high* — a scorer sitting fallback-only forever was perfectly "in agreement,"
so nothing went red. The fallback path is also byte-identical pure Python, so there was no
correctness bug forcing attention.

The fix (shipped alongside Wave 2): **`check_scorer_coverage`** in `scripts/check_api_parity.py`
requires every scorer in the `scorers` surface to be EITHER kernel-backed (in `scorer_kernels`) OR
listed in a new `scorer_kernels_deferred:` map (scorer → reason) in `parity/goldenmatch.yaml`. An
uncovered/unclassified scorer FAILS the gate; a kernel that *regresses* to a fallback (removed from
`scorer_kernels` without a deferral) also fails; a stale deferral (scorer that gained a kernel) fails.
So a new scorer, or a coverage regression, can no longer sit silent — deferral is a conscious act
with a rationale (`deferred --` will kernelize / `declined --` won't / `n/a --` not a string kernel),
not the absence of one. This is the durable close on the root cause, complementing the per-scorer
cutovers above.

## Per-scorer work unit (the template)

Each scorer follows the same six steps. **The `api_parity` gate only checks the id-map is present
and the manifest agrees -- it does NOT assert numeric parity.** Proving `kernel == pure-Python` is
on us, and step 4 is the real work.

1. **`score-core`** (`packages/rust/extensions/score-core/src/lib.rs`): add the primitive
   (`pub fn <scorer>_similarity` + a `score_one` id, or a dedicated fn). Exports today:
   `jaro_winkler_similarity`, `levenshtein_similarity`, `token_sort_ratio`,
   `token_sort_normalized_ratio`, `date_similarity`, `qgram_similarity`, `soundex`,
   `set_legal_forms` + `initialism_match`, `set_business_aliases` + `set_given_name_canonicals` +
   `alias_match`, `score_one(id: 0..8)` (id 5 = qgram, id 6 = soundex_match, id 7 = initialism_match
   against the installed legal-form set, id 8 = alias_match against the installed alias tables).
2. **`native` wheel** (`packages/rust/extensions/native/src/score.rs`): add a `#[pyfunction]` shim,
   register it in `native/src/lib.rs` (`m.add_function(...)` -- required for the `native_symbols`
   gate), and wire the id into `score_field_matrix` (`:1220`) and/or `score_block_pairs*`.
3. **Python id maps:** add `"<scorer>": <id>` to `backends/score_buckets.py::_NATIVE_SCORER_IDS`
   (`:214`, bucket path) and/or `core/scorer.py::_NATIVE_FIELD_SCORER_IDS` (`:479`, field path),
   **capability-guarded** like `date` so a stale published wheel declines to the Python mirror
   (the #688 silent-slow-fallback class). NB the two id maps are distinct namespaces --
   bucket `id 4 = date`, field `id 4 = soundex_match` -- do not collide.
4. **Pure-Python mirror + parity test:** keep the existing `_<scorer>_score_matrix` byte/4dp
   identical to the kernel; add a `tests/test_native_*_parity.py` case asserting `pure == kernel`.
5. **`score-wasm`** (`packages/rust/extensions/score-wasm/src/lib.rs`): add the id to
   `score_matrix_impl`; **TS backend** (`src/core/wasm/backend.ts`): add to `SCORER_ID`
   (auto-joins `WASM_COVERED_SCORERS`); regenerate the committed wasm fixture (the `fixture_drift`
   gate re-checks it).
6. **Manifest:** update the `scorer_kernels:` partition in `parity/goldenmatch.yaml`
   (shared / python_only / ts_only) in the **same PR**, or `api_parity` reddens.

## Risks / parity edges

- **Reference fidelity.** A Rust reimplementation must match the Python reference *exactly*:
  `soundex_match` must equal `jellyfish.soundex`; the name scorers must use the exact census-2010 /
  alias / given-name tables the refdata ships. A drifting reimpl silently changes match output.
- **Table shipping.** Waves 2 kernels need refdata tables loaded into the kernel via
  `set_name_reference_data`; the load path must be deterministic and the table content pinned so
  `native == pure`.
- **`dice`/`jaccard` cross-language divergence.** TS `diceCoefficient` is a char-bigram set variant;
  Python is bloom popcount. Pick the reference explicitly rather than paper over it.
- **Wheel skew (#688).** Every new kernel symbol must be capability-guarded AND the published wheel
  republished in the same change, or every `pip install goldenmatch[native]` env silently keeps
  hitting the slow fallback. `scripts/check_native_wheel.py` is the advisory.
- **`native_symbols` gate.** A new `wrap_pyfunction!` must be registered with a `::`-qualified path
  and reflected in the host reference scan, or the gate flags referenced-but-not-registered.

## Non-goals

- **Waves 4** (`audio_fp`, `radial`) and the **embedding pair** are explicitly out of scope for a
  scorer kernel here.
- **Independent doc fix (do regardless):** change the generated wording in
  `gen_suite_matrix.py::_substrate_lines` from "N of M scorers are kernel-backed" to make clear the
  count is a **union across languages** (e.g. "5 have a kernel in >=1 language; 4 in both"), so the
  ratio is not misread. This does not depend on any kernel work landing.

## Recommendation

Ship **Waves 1-2** first (the six clean string scorers -- real perf, lowest risk, proven template),
then **Wave 3** (cheap wiring of existing kernels), take `ensemble` for free, **defer Wave 4**, and
**reframe the embedding pair** under goldenembed. End state: full algorithmic kernel coverage with an
honest, self-updating suite-matrix line.
