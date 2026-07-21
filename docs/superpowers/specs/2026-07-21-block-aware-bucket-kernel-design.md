# Block-aware bucket scoring for the bloom/hash scorers (dice / jaccard / phash)

**Date:** 2026-07-21
**Status:** Proposed (design / scoping). Follow-on to the `-core` scorer-kernel coverage work
(`2026-07-21-scorer-kernel-full-coverage-design.md`, which took the metric 5/19 → 9/19 and added
the `scorer_kernels` coverage floor). This scopes the next three fallbacks — `dice`, `jaccard`,
`phash` — which the coverage manifest currently marks `deferred` as "matrix-semantics-dependent."

## TL;DR

The "these need a block-aware kernel" framing was **half right**. After reading the actual code:

- **`dice` and `jaccard` do NOT need block-awareness.** Their per-pair functions (`_dice_score_single`
  / `_jaccard_score_single`) are bloom-hex with **integer popcounts** + a single float64 divide, and
  their coefficients are **popcount-based** so zero-padding is invariant. A Rust `score_one` kernel
  using `u.count_ones()` is **byte-exact** with the per-pair function. They already run on the bucket
  per-pair loop today, so a native id is a byte-exact speed-up with **zero output change**. → do these
  now, **9/19 → 11/19**, on the proven `score_one` template.
- **`phash` is the only one with a real block dependency** — its denominator is bit-*length*, so the
  matrix path pads to the block-**global** max. But phash **currently declines to the matrix path**
  (it's not bucket-eligible at all), so kernelizing it into the bucket path is a *new* path either
  way. A per-pair kernel matching `_phash_score_single` (pairwise pad, float64) is the cheap option;
  matching `_phash_score_matrix` exactly needs the block-aware mechanism.
- The **general block-aware bucket mechanism** is still worth designing — it's the right tool for any
  scorer whose per-pair value depends on block-global state (phash's global-max; future
  IDF/normalization scorers). It is designed here, with phash as its one concrete candidate consumer.

**Recommendation:** ship `dice` + `jaccard` per-pair now (Phase 1, clean); take `phash` per-pair with
a documented output characterization (Phase 2, one decision to confirm); build the block-aware
mechanism (Phase 3) only if byte-exact-with-`_phash_score_matrix` is required, or when a genuinely
block-global scorer (a future IDF-weighted comparator) lands.

## What the code actually does (corrected diagnosis)

The `scorer_kernels` metric reads the **bucket** map `_NATIVE_SCORER_IDS`
(`scripts/emit_python_surface.py::_scorer_kernels` → `backends.score_buckets`). A scorer counts iff
it's in that map. dice/jaccard/phash are absent, so adding them there (with a `score_one` id + a
byte-parity Rust kernel) is what makes them count. There are two native scoring surfaces:

| Surface | Rust fn | Shape | Ids | Counts toward metric? |
|---|---|---|---|---|
| **bucket** | `score_block_pairs` / `_arrow` | whole block columns in, **per-pair `score_one(id,a,b)`** inside, emits pairs ≥ threshold | `_NATIVE_SCORER_IDS` (0=jw … 8=alias) | **yes** |
| **field-matrix** | `score_field_matrix` | whole columns in, returns **NxM float32 matrix** | `_NATIVE_FIELD_SCORER_IDS` (0-4, soundex=4) | no |

`score_block_pairs_arrow` **already receives the whole block's columns** (`field_arrays_arrow`, one
Arrow array per field, plus `size_list` block run-lengths). The block data is present; the limitation
is purely that dispatch is per-pair `score_one`. `score_field_matrix` is the existing block-aware
pattern (column in → matrix out) to reuse.

### The per-pair functions are exact integer popcount (not float32, not bigram)

```python
# core/scorer.py  — the per-pair references (bucket path uses these)
def _dice_score_single(a, b):
    ba, bb = _pad_to_equal_length(_hex_to_bits(a), _hex_to_bits(b))   # PAIRWISE pad
    inter = np.unpackbits(np.bitwise_and(ba, bb)).sum()               # INTEGER popcount
    total = np.unpackbits(ba).sum() + np.unpackbits(bb).sum()         # INTEGER popcount
    return float(2.0 * inter / total) if total > 0 else 0.0           # one float64 divide

def _jaccard_score_single(a, b):   # ... union = popcount(a|b); inter/union
def _phash_score_single(a, b):     # dist = popcount(a^b); 1 - dist/(bits_a.size*8)  (PAIRWISE)
```

- The comment in `_resolve_score_pair_callable` calling `_dice_score_single` a "bigram set Dice
  coefficient" is **stale** — it's bloom-hex (hex-decode + bit ops), identical in *kind* to
  `_dice_score_matrix`.
- **Dice/Jaccard are padding-invariant:** the denominators are popcounts of `a`, `b`, and `a&b`/`a|b`
  — trailing zero bytes change none of them. So pairwise-pad and global-max-pad give the *same value*.
- **The only single-vs-matrix divergence for dice/jaccard is float32:** `_dice_score_matrix` builds a
  `float32` bit-matrix and computes the intersection with a float32 matmul (`bit_matrix @ bit_matrix.T`),
  so it rounds to ~1e-7. Measured: `_dice_score_single('ab12','cd34')=0.5333333333` vs
  `_dice_score_matrix=0.5333333611` — a float32 artifact, nothing else. A Rust `count_ones()` kernel
  has **no float32 error** (integer popcount + float64 divide) → byte-exact with the *single*.

### phash is genuinely block-dependent — and currently off the bucket path

```python
def _phash_score_matrix(values):
    # ... valid mask (try/except ValueError -> 0), then:
    max_len = max(len(b) for b in bit_arrays)         # block-GLOBAL max bit length
    bit_matrix = np.zeros((n, max_len), dtype=np.float32)   # float32
    hamming = popcounts[:,None] + popcounts[None,:] - 2.0*intersection
    sim = 1.0 - hamming / max_len                     # denominator = GLOBAL max
    sim = np.where(valid[:,None] & valid[None,:], sim, 0.0)   # invalid hex -> 0
```

Three ways `_phash_score_single` (pairwise, float64, raises on bad hex) differs from
`_phash_score_matrix` (global-max, float32, bad hex → 0):

1. **Padding**: `1 - dist/(pairwise bits)` vs `1 - dist/(block-global bits)`. Identical when all
   block hashes are equal length (the normal 64-bit pHash case); diverges only on **≥3-element
   mixed-length blocks** (a 2-element block's pairwise max == its global max, which is why the
   divergence is invisible in a naive two-value test).
2. **float32 vs float64** (as with dice/jaccard).
3. **Invalid hex**: single raises (would crash the bucket per-pair loop); matrix scores 0.

And critically: `_resolve_score_pair_callable` has **no `phash` branch**, so phash resolves to `None`
→ the matchkey declines the fast path entirely → phash runs on the slow `find_fuzzy_matches` **matrix**
path. So phash's *current* output IS `_phash_score_matrix` (global-max/float32/bad-hex→0). Any
bucket kernel for phash is a **new code path**, and the design choice is which reference it matches.

## Phase 1 — `dice` + `jaccard` as `score_one` ids (clean, do now)

The exact qgram/soundex template. **9/19 → 11/19.**

1. **`score-core`** (`lib.rs`): `dice_similarity(a,b)` / `jaccard_similarity(a,b)` + `score_one`
   ids 9, 10. Decode hex → bytes; pairwise-pad (or, equivalently since padding-invariant, compare
   over `min(len)` bytes + count the tail of the longer as pure popcount — either is exact); integer
   `count_ones()`:
   ```rust
   // dice: 2*|a&b| / (|a| + |b|)
   fn dice_similarity(a: &str, b: &str) -> f64 {
       let (pa, pb) = match (decode_hex(a), decode_hex(b)) { (Some(x), Some(y)) => (x, y), _ => return 0.0 };
       let (inter, total) = popcounts(&pa, &pb);   // inter = Σ (pa[i] & pb[i]).count_ones(); total = Σ pa.count_ones() + Σ pb.count_ones()
       if total == 0 { 0.0 } else { 2.0 * inter as f64 / total as f64 }
   }
   ```
   Pure integer arithmetic → **byte-exact** with `_dice_score_single` on valid hex. No `regex`, no
   `unicode-normalization`, no table — this is a pure primitive, so it stays in the default feature
   set (unlike alias's `regex` gate).
2. **`native`**: `dice_similarity` / `jaccard_similarity` `#[pyfunction]` capability markers +
   registration.
3. **`score_buckets.py`**: `_NATIVE_SCORER_IDS += {"dice": 9, "jaccard": 10}` + a wheel-skew guard
   (`hasattr(_mod, "dice_similarity")`) alongside date/qgram/soundex — a stale wheel declines to the
   existing per-pair `_dice_score_single` mirror (which the bucket path already uses). **No install
   step** (no table), so the guard is one-part (symbol only), simpler than initialism/alias.
4. **Also make them `_VEC_SUPPORTED`** *(optional, perf)*: add exact matrix forms in
   `_vec_field_matrix` that use **integer** popcount + float64 (not the float32 `_dice_score_matrix`),
   so the vectorized lane can batch them byte-identically to the per-pair loop. Small numpy change
   (`np.uint64` view + `np.bitwise_count`, or keep the per-pair loop for these). Not required for the
   kernel; do it only if a dice/jaccard workload shows the per-pair loop is hot.
5. Manifest: **move `dice`, `jaccard` from `scorer_kernels_deferred` → `scorer_kernels.python_only`**
   (the coverage gate enforces this — leaving them in `deferred` after they gain a kernel now fails
   with `stale_deferral`). Regenerate suite-matrix (11/19) + agent-codemap; cross-surface id-map;
   `test_native_dice_jaccard_parity.py` (native == `_*_single` over a hex-CLK corpus + the id 9/10
   bucket dispatch); spec/CLAUDE.md.

**The one edge to pin: invalid hex.** The single functions raise; a `score_one` kernel returning f64
can't. Decision: **the kernel treats unparseable hex as "no bits" → 0.0** (matches the phash matrix's
bad-hex→0 intent and never crashes the loop). dice/jaccard are PPRL/CLK scorers whose inputs are
always valid fixed-width hex, so this edge doesn't arise in practice; the parity test asserts it
explicitly and the mirror (`_dice_score_single`) is documented as the valid-hex reference. (If we
want the bucket per-pair mirror itself to stop raising, wrap it in the `_resolve_score_pair_callable`
branch — a one-line `try/except → 0.0` — so kernel and mirror agree on the edge too.)

## Phase 2 — `phash` (one decision to confirm)

phash isn't bucket-eligible today (declines to the matrix path). Two ways to make it kernel-backed:

**Option A — per-pair `score_one` id 11, matching `_phash_score_single` (pairwise/float64).** Cheap,
same template as Phase 1. Consequence: routing phash onto the bucket path changes its output vs the
current matrix path on (i) ≥3-element mixed-length blocks, (ii) float32→float64, (iii) invalid hex.
For **fixed-length pHashes with valid hex** — the real-world case (image pHash is always 16 hex / 64
bits) — only float32→float64 differs, i.e. a *precision improvement*, and pairwise==global. So Option
A is byte-exact-in-practice and arguably more correct. **Risk:** if a deployment feeds mixed-length
or non-64-bit pHashes, the bucket and matrix backends would disagree. Mitigate by adding a per-pair
`phash` branch to `_resolve_score_pair_callable` (so the bucket path itself is defined) + a parity
test on fixed-length hashes + a documented note that mixed-length phash is backend-sensitive.

**Option B — block-aware kernel matching `_phash_score_matrix` exactly** (global-max/float32/bad-hex→0).
Preserves the current output bit-for-bit, at the cost of the Phase-3 mechanism + replicating float32
matmul semantics in Rust (a `f32` accumulate to match numpy — fiddly but doable). Only worth it if
byte-exact-with-today's-output is a hard requirement.

**Recommendation: Option A**, gated on confirming that phash inputs are fixed-length pHashes in the
workloads we care about (they are, by construction). It makes phash kernel-backed (**11/19 → 12/19**)
on the proven template, with pairwise/float64 as the honest, more-correct reference; the coverage
manifest's phash entry moves from `deferred` to `scorer_kernels.python_only` with a note that its
reference is `_phash_score_single`, not `_phash_score_matrix`.

## Phase 3 — the general block-aware bucket mechanism (design; build when needed)

The capability the "block-aware kernel" name refers to. Design it now; build it only for Option B or
a future genuinely-block-global scorer (an IDF/normalization comparator). The infrastructure already
exists — `score_block_pairs_arrow` holds the whole block, and `score_field_matrix` already turns a
column into an NxN matrix.

**Mechanism.** Introduce a second class of bucket scorer — **matrix scorers** — alongside the per-pair
`score_one` ids:

- A new map `_NATIVE_BLOCK_MATRIX_SCORER_IDS` (or a tagged union in the existing map) marks a field's
  scorer as block-aware. Its native id routes not to `score_one(id,a,b)` per pair but to a
  per-(block, field) call that returns the block's NxN contribution matrix (the `score_field_matrix`
  shape, extended to the semantics that scorer needs — e.g. phash's global-max hamming).
- `score_block_pairs_arrow` gains a branch: for a matrix-scorer field, compute its NxN block matrix
  once (block-global state available: max length, IDF over the block, etc.); for a `score_one` field,
  the existing per-pair loop. Then the **combine** step is unchanged — weighted sum of per-field
  contributions ÷ observed weight, threshold-emit the upper triangle. (This is exactly what the
  Python `_score_block_vec` lane already does — per-field matrix, weighted combine, triu emit — so
  the Python fallback for block-aware scorers is `_score_block_vec` with the scorer's matrix fn.)
- Parity reference for a matrix scorer is its `_<scorer>_score_matrix` (block-aware by definition),
  so byte-parity is achievable where the per-pair `score_one` path structurally cannot reach it.

**Why not just always use matrix scorers?** Per-pair `score_one` is cheaper for the common string
scorers (no N² matrix materialization when the block is large and sparse in emitted pairs) and is the
established path. Matrix scorers are the escape hatch for the minority whose value needs block context.

**Candidate consumers:** `phash` (Option B), and later a block/dataset-normalized comparator — note
`name_freq_weighted_jw` needs *dataset*-global TF, not *block*-global, so it's a related-but-distinct
"install a table" case (like alias's `set_legal_forms`), not this block-matrix mechanism.

## Non-goals / out of scope

- **Reconciling the bucket-vs-matrix backend divergence for phash** (global-max/float32 vs
  pairwise/float64). That's a pre-existing inconsistency between the `bucket` and `polars-direct`
  backends; this spec picks the bucket reference and documents it, but does not change the
  `find_fuzzy_matches` matrix path.
- **`ensemble`** stays `declined` (measured Febrl3 recall regression; separate matter).
- **The ts_only name scorers** (`given_name_aliased_jw` / `name_freq_weighted_jw`) are a TS-WASM
  track, unrelated to this mechanism.

## Rollout

1. **Phase 1 PR**: dice + jaccard `score_one` ids 9/10 (+ optional exact vec-matrix), byte-parity
   tests, manifest move deferred→kernels, 11/19. Low risk, no output change.
2. **Phase 2 PR**: phash per-pair id 11 (Option A) after confirming fixed-length-pHash assumption;
   12/19. One documented behavior characterization.
3. **Phase 3**: build the matrix-scorer mechanism only if Option B is required or a block-global
   scorer lands. Design captured here so it isn't re-derived.

Each phase re-runs the byte-parity discipline (kernel == the per-pair reference over an adversarial
hex corpus) and the `scorer_kernels` coverage gate (which now *forces* the deferred→kernels move).
