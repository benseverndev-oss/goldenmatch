# Native matrix kernel for slow path — design

**Status:** Draft, 2026-05-29
**Tracks:** Opt #5 (post-v1.24.0 perf arc)
**Author:** Ben + Claude

## Problem

We have two parallel scoring backends in the Python layer:

1. **Bucket fast path** (`backends/score_buckets.py`)
   Calls `goldenmatch._native.score_block_pairs_arrow` — a Rust kernel that
   takes a flat row layout + `block_sizes`, scores all in-block pairs in
   parallel via rayon, applies weighted-sum aggregation, and emits
   `(row_a, row_b, score)` triples above threshold. Zero-copy Arrow handoff
   from Polars. Already shipped, default-on.

2. **Slow path** (`core/scorer.py::find_fuzzy_matches`)
   Builds per-field `NxN` matrices via `rapidfuzz.process.cdist`, combines
   them with weights, applies NE penalty math, match-mode filtering, and
   probabilistic scoring. Pure Python orchestration over `rapidfuzz` C
   primitives. Used when the bucket fast path declines (e.g. embedding
   scorers, ensemble that breaks bucket assumptions, callers without
   bucket layout).

The bucket backend has a Rust scorer dispatch (`score.rs::score_one`) covering
IDs 0–3: `jaro_winkler`, `levenshtein`, `token_sort`, `exact`. The slow path
goes through `rapidfuzz.cdist` directly for the same four scorers plus
`soundex_match`, `dice`, `jaccard`, `ensemble`, and embedding kernels.

**Two scorer surfaces, two implementations, two test matrices.** Per the
session strategic frame: "slowly port each feature in slow over to fast with
native Rust." Continuing to invest in the slow-path matrix orchestration is
debt; the goal is convergence on one kernel.

## Why not just route the slow path through `score_block_pairs_arrow`?

Incompatible aggregation API. `score_block_pairs_arrow` is a high-level
"weighted aggregate + threshold filter" primitive that emits pairs. The
slow path needs **per-field score matrices** held in Python land because:

- **Probabilistic matchkey** consumes per-field match/non-match comparison
  vectors, not a weighted aggregate.
- **NE penalty math** subtracts per-NE-field disagreement penalties from a
  composite that's already weighted on the in-matchkey fields — needs both
  surfaces visible at the Python layer.
- **Match-mode filtering** (the `_first_block_df` probabilistic fast path)
  reads per-field scores to decide acceptance independent of threshold.
- **Auto-config indicators** (`compute_column_priors`,
  `estimate_sparse_match_signal`) instrument per-field score distributions.

The two kernels are at different layers of abstraction, not duplicates.

## Proposal: `score_field_matrix` Rust kernel

Add a low-level cdist primitive to `goldenmatch_native`:

```rust
#[pyfunction]
pub fn score_field_matrix(
    py: Python<'_>,
    values_a: PyArrowType<ArrayData>,  // Utf8 or LargeUtf8
    values_b: PyArrowType<ArrayData>,  // Utf8 or LargeUtf8; may alias values_a
    scorer_id: u8,                     // 0=jw, 1=lev, 2=ts, 3=exact, 4=soundex, 5=dice, 6=jaccard
) -> PyResult<PyObject /* np.ndarray<f32> of shape (a.len, b.len) */>;
```

Behavior:

- **Zero-copy Arrow in.** Accepts both `Utf8` (pyarrow default, i32 offsets) and
  `LargeUtf8` (Polars default, i64 offsets) — pattern already in `StrCol` enum.
- **Symmetric self-cdist** when `values_a == values_b` by identity — skip the
  lower triangle in the parallel loop.
- **Output ndarray.** Use `pyo3-numpy` to return a `PyArray2<f32>`, owning
  buffer allocated in Rust, exposed zero-copy to NumPy.
- **Null handling.** Mirror `rapidfuzz.cdist`'s "None → empty string" convention
  already baked into the slow path callers (`_fuzzy_score_matrix:349`). Caller
  remains responsible for null masks.
- **Parallelism.** `py.allow_threads(|| {...})` + rayon `par_iter` on rows of
  the output matrix. Same threading shape as `score_block_pairs_arrow`.

Scorer dispatch shares the existing `score_one` function — extended with three
new IDs:

| id | scorer        | Rust impl                                                   |
|----|---------------|-------------------------------------------------------------|
| 0  | jaro_winkler  | `rapidfuzz::distance::jaro_winkler::normalized_similarity`  |
| 1  | levenshtein   | `rapidfuzz::distance::levenshtein::normalized_similarity`   |
| 2  | token_sort    | existing `token_sort_string` + ratio                        |
| 3  | exact         | `a == b ? 1.0 : 0.0`                                        |
| 4  | soundex_match | port `jellyfish.soundex` — pure-string, no external dep     |
| 5  | dice          | bigram-set Dice; cache per-string bigrams via `OnceCell`    |
| 6  | jaccard       | bigram-set Jaccard; share bigram cache with dice            |

`ensemble` is composable in Python (max of three matrix calls) rather than a
dedicated ID — keeps the kernel ID space minimal.

## Python integration

In `core/scorer.py`:

```python
def _fuzzy_score_matrix(values, scorer, weights=None):
    if _NATIVE is not None and scorer in _NATIVE_FIELD_SCORERS:
        return _NATIVE.score_field_matrix(_to_arrow(values), _to_arrow(values), _SCORER_ID[scorer])
    # existing rapidfuzz.cdist fallback unchanged
```

The native call is **strictly additive** — when `[native]` is absent, the
rapidfuzz path runs unmodified. No behavior change beyond perf.

Soundex / dice / jaccard get the same dispatcher entry, replacing their
hand-rolled Python matrices (`_soundex_score_matrix`, `_dice_score_matrix`,
`_jaccard_score_matrix`).

## Parity tests

Mirror the pattern in `tests/test_native_parity.py`:

- Per scorer: 200 string pairs spanning ASCII / Unicode / null / empty;
  assert `score_field_matrix` output equals `rapidfuzz.cdist` output within
  `1e-6` (rapidfuzz's own f64-to-f32 rounding band).
- Self-cdist symmetry: `M[i,j] == M[j,i]`.
- Diagonal: `M[i,i] == 1.0` for all scorers on non-null values.
- Null propagation: empty-string mapping matches caller convention.

## Bench targets

10M-bucket-realistic isn't the right measurement target — bucket already uses
the higher-level kernel and would not benefit. The slow-path benchmark surfaces
where this kernel pays back:

- **Bench A:** DBLP-ACM via `find_fuzzy_matches` (auto-config picks slow path).
  Per-field rapidfuzz cdist vs native cdist. Expected: 2-4x per-field
  speedup on jw/lev/ts (rapidfuzz is already C; we're amortizing FFI
  overhead). 5-10x on soundex/dice/jaccard (replacing Python matrices).
- **Bench B:** Probabilistic matchkey on a 100K synthetic. Today runs the
  pure-Python `_soundex_score_matrix` for the soundex column. Expected: this
  becomes the dominant lift.

## Out of scope

- **Embedding / record_embedding kernels.** Still model-backed; orthogonal
  port.
- **Bucket-backend changes.** `score_block_pairs_arrow` keeps its weighted-pair
  surface. This spec only adds a sibling primitive.
- **Slow-path retirement.** Not retiring the slow path — only narrowing the
  gap between its scorer kernel and the bucket's.

## Open questions

1. **Bigram caching across calls.** Dice/Jaccard build bigram sets per string;
   in a self-cdist over N values, each string's bigrams are computed once and
   reused for N comparisons. Bigram cache lives inside the kernel call, not
   across calls — keeps the API stateless. Cross-call cache (e.g. attached to
   the prepared-record store) is a follow-up.
2. **Numpy version.** `pyo3-numpy` pins to a numpy major. The native package
   already depends on numpy via the existing `to_pyarray` paths — confirm
   versions align before adding `score_field_matrix`.
3. **GIL release granularity.** Inside `allow_threads`, rayon spawns OS
   threads. Test under `pytest -n auto` to confirm no regression vs the
   current scorer.

## Build / ship

- Land kernel in `packages/rust/extensions/native/src/score.rs` alongside
  `score_block_pairs_arrow`.
- Bump `goldenmatch-native` to `0.2.0`. (Goldenmatch python depends on
  `goldenmatch-native>=0.1.0` — bump the floor to `0.2.0` in the `[native]`
  extra once the kernel ships.)
- Python loader stays unchanged — `_native_loader.py` discovers
  `goldenmatch_native._native` and exposes whichever attributes are present.
- One PR per scorer family is too granular; bundle all 7 IDs in one PR so
  the parity test matrix lands together.

## Decision needed

Approve scope → implement. Otherwise return with scope edits.
