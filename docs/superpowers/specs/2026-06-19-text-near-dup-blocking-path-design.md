# Document/text near-dup blocking path — design

- **Issue:** [#1082](https://github.com/benseverndev-oss/goldenmatch/issues/1082) — Document/text near-dup blocking path
- **Epic:** [#1080](https://github.com/benseverndev-oss/goldenmatch/issues/1080) — Training-Data Dedup at Scale
- **Builds on:** #1081 (the MinHash/LSH `sketch-core` kernel + `MinHashLSHBlocker` + `strategy="lsh"`)
- **Date:** 2026-06-19
- **Status:** Approved (brainstorming) — pending spec review + plan

## Motivation

#1081 shipped the MinHash/LSH kernel and a `MinHashLSHBlocker`, but using it
requires a manual `BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(...))`. On a
bare text corpus, `dedupe_df(corpus)` does NOT pick it: auto-config routes a
long-text (`description`) column to `strategy="ann"` (FAISS embeddings, ≥100K
rows) or falls through to name-based multi-pass blocking — neither is right for
"a column of documents". #1082 makes the text path zero-config.

It also adds the **semantic** counterpart. The #1081 Quora-QQP bench surfaced
LSH's scope boundary honestly: lexical shingle/LSH recovers near-duplicates
(~0.98 on lexical edits) but only ~0.21 of QQP's *paraphrase* duplicates,
because MinHash measures lexical Jaccard, not meaning. A SimHash (random
hyperplane) LSH over embedding vectors closes that gap with a true LSH path
(cosine-similarity buckets) rather than FAISS.

## Goals

- Auto-config detects a text corpus and emits a near-dup blocking strategy with
  **no manual matchkey/blocking config** (the issue's done bar).
- **Lexical** path: reuse the #1081 shingle/MinHash/LSH kernel (`strategy="lsh"`).
- **Semantic** path: a new pyo3-free SimHash kernel in `sketch-core` over
  embedding vectors, exposed as `strategy="simhash"` + `SimHashLSHBlocker`.
- Routing: **lexical is the zero-config default**; **semantic is the escalation**
  when an embedder is already reachable.
- Cross-language parity for the SimHash kernel function (Rust/Python/TS golden
  vectors); the semantic *blocker* is Python-primary (embeddings live there).
- Recall validated: extend the QQP bench with a lexical-vs-semantic A/B.

## Non-goals (later epic phases)

- Sketch-then-verify execution plan (#1083).
- Distributed billion-scale (#1084).
- Corpus-dedup product surface / CLI (#1085).
- Numeric TS semantic blocking end-to-end (TS has no real embedder; the TS
  SimHash kernel ships for parity/completeness only).
- A new embedding provider — semantic routing uses the existing
  `get_embedder` / `inhouse_embedding_available` machinery.

## Architecture

Two near-dup paths behind one auto-config decision:

```
                       ┌─ exact-key (email/phone/id/zip-compound)  [highest precedence, unchanged]
auto-config build_blocking ─┤
                       ├─ TEXT CORPUS?  (description col, no exact key, no blockable name)
                       │     ├─ embedder reachable?  →  strategy="simhash"  (semantic, SimHash over embeddings)
                       │     └─ else                 →  strategy="lsh"      (lexical, #1081 shingle/MinHash)
                       └─ name / multi-pass fallback               [unchanged, for structured records]
```

Both strategies emit `BlockResult`s through the existing blocker contract.

**Controller guard (required).** The auto-config controller's refit rules CAN
swap a committed blocking strategy mid-iteration. `lsh`/`simhash` configs carry
**empty `.keys`** (the validator forbids `keys`/`passes`), so the rules that
early-return on `not current.blocking.keys` already self-skip them
(`rule_no_matches`, `rule_low_reduction_ratio`, `rule_blocking_field_null_heavy`,
`rule_cross_blocking_disagreement`). But several rules gate only on
`current.blocking is None` and would actively rewrite an `lsh` config — notably
`rule_blocking_singleton_trap` and `rule_uniform_heavy_blocking` (→ `static`) and
`rule_blocking_key_swap` (→ `static`/`first_token`). A near-dup corpus plausibly
hits both the "candidates compared, nothing matched" (`mass_above_threshold==0`)
and the "nothing compared" (`candidates_compared==0`, the singleton-trap) shapes,
so without a guard a committed `lsh`/`simhash` config gets silently swapped.

**Required change:** introduce a shared helper `_near_dup_locked(config) -> bool`
(true when `config.blocking.strategy in {"lsh","simhash"}`) and early-return
`None` from **every refit rule that emits a `blocking`-strategy/key update** when
it is true. The complete set in `DEFAULT_RULES` to guard:
`rule_blocking_singleton_trap`, `rule_blocking_too_coarse`,
`rule_blocking_key_swap`, `rule_uniform_heavy_blocking`,
`rule_blocking_field_null_heavy`, `rule_low_reduction_ratio`,
`rule_recall_gap_suspected`, `rule_cross_blocking_disagreement`,
`rule_blocking_adaptive_on_p99_outlier`. (Threshold/matchkey rules are NOT
guarded — the controller may still tune the score threshold on an `lsh` config.)
Add the guard to each rule body (explicit + greppable; a tenth blocking rule must
adopt the same first-line check). A **controller-survival** regression test drives
the controller on a text-corpus shape that trips BOTH `rule_blocking_singleton_trap`
(`candidates_compared==0`) and `rule_blocking_key_swap` (`mass_above_threshold==0`)
and asserts the committed `lsh`/`simhash` strategy survives every iteration.

## Phase A — lexical auto-enable

### Detection: `_is_text_corpus(profiles)`

Returns true when **all** of:
- at least one `description` column exists (`col_type == "description"`), and
- no scale-safe exact-key blocking fired earlier in `build_blocking` (no
  email/phone/identifier/zip-compound key), and
- there is **no blockable `name` column** — i.e. no `col_type == "name"` /
  `"multi_name"` column with `cardinality_ratio >= 0.1` (the text is the primary
  identity, not a description field on otherwise-structured records).

This targets true corpora (a column of documents / a 1-column text CSV) while
leaving structured-record routing untouched (a name column ⇒ name/multi-pass
blocking, with the description still feeding the `record_embedding` matchkey for
scoring). It supersedes the old `description → ann` fallback for corpora.

### Config emission: `_auto_build_lsh_config(profiles)`

Picks the longest description column (max `avg_len`); emits
`LSHKeyConfig(column=<that>, mode="word", k=2, num_perms=128, threshold=0.5,
seed=0)` — the #1081-validated config. Returns `BlockingConfig(strategy="lsh",
lsh=...)`.

### Routing hook

In `build_blocking()` (autoconfig.py), insert `if _is_text_corpus(profiles):
return _text_corpus_blocking(profiles, df)` after the exact-key +
bounded-compound attempts and **in place of the existing `_ann_eligible` block**
(autoconfig.py ~2224–2236), before the name/multi-pass fallback.
`_text_corpus_blocking` picks semantic (Phase B) when `_embedder_available()`,
else lexical.

**Interaction with the existing ANN auto-selection (pinned):** the current
`_ann_eligible` branch auto-selects `strategy="ann"` for a `description` column at
`rows >= GOLDENMATCH_ANN_MIN_ROWS` (default 100K). The text-corpus branch
**replaces it entirely** — ANN is **no longer auto-selected** for description
columns at any row count; semantic SimHash is the new auto embedding path
(`strategy="ann"` remains available via explicit config). This removes the
double-routing ambiguity (text-corpus and `_ann_eligible` would otherwise both be
eligible above 100K) and the row-count gate (LSH/SimHash auto-enables for text
corpora regardless of size). `_embedder_available()` = `inhouse_embedding_available()`
or a configured embedding provider on the resolved config.

## Phase B — semantic SimHash

### Kernel: `sketch-core/simhash.rs` (pyo3-free)

Reuses the #1081 hash family (`base_hash`, `splitmix64`). All floating-point is
`f64` (Rust `f64` / Python `float64` / TS `number`) so the function is
byte-identical across languages on identical input vectors.

`simhash_signature(vector: &[f64], num_planes: usize, seed: u64) -> Vec<u8>`
(each element `0` or `1`):

- The `num_planes × dim` projection matrix has **Rademacher ±1** entries drawn
  row-major from a splitmix64 bitstream seeded at `seed`. Bit-drawing is pinned:
  maintain `(state, bitbuf, bits_left)`; to draw one entry, if `bits_left == 0`
  then `(v, state) = splitmix64(state); bitbuf = v; bits_left = 64`; the entry is
  `+1.0 if (bitbuf & 1) == 1 else -1.0`; then `bitbuf >>= 1; bits_left -= 1`.
  Entries are drawn in order plane 0 col 0..dim, plane 1 col 0..dim, … (row-major).
- For plane `i`: `dot = Σ_j plane[i][j] * vector[j]` accumulated in `f64`, `j`
  ascending; `sig[i] = 1 if dot >= 0.0 else 0` (the `dot == 0` tie → `1`, pinned).
- Empty/all-zero vectors are allowed (every dot is `0.0` → all-ones signature);
  the blocker drops them (no content).

`simhash_band_hashes(sig: &[u8], num_bands: usize) -> Vec<u64>`:

- `num_planes = sig.len()` must be divisible by `num_bands`; `r = num_planes /
  num_bands`. For band `b`: `bucket = base_hash(le8(b) ++ sig[b*r .. (b+1)*r])`
  (the 8 little-endian bytes of the band index `u64`, then the `r` `0/1` bytes).
  Mixing `b` in keeps identical bit-runs in different bands separate. (One byte
  per plane-bit — no cross-word bit-packing — to keep the contract unambiguous
  across three languages; 256 bytes/record is negligible.)

Batch entry point `simhash_band_hashes_batch(vectors, num_planes, num_bands,
seed)` generates the projection matrix **once** (same seed/dim for the batch) and
reuses it across rows; rayon fan-out guarded by
`GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS` (shared with #1081).

Band selection reuses the existing `optimal_bands(num_planes, threshold)` helper
(host-side; `num_planes` plays the role of `num_perms`) — no new helper.

### Python binding + blocker

- pyo3 shims in `native` (`simhash_band_hashes_batch`), registered in `_native`;
  pure-Python reference/fallback in `core/sketch.py` (extends the module). Native
  gated like `"sketch"` (not default-on; reachable via `GOLDENMATCH_NATIVE=1`).
- `SimHashKeyConfig` (`column`, `num_planes=256`, `num_bands | threshold`, `seed=0`,
  `model: str | None`) + `strategy="simhash"` with a positive validator branch
  (mirrors `LSHKeyConfig`).
- `SimHashLSHBlocker` (`core/simhash_blocker.py`): embed the text column via
  `get_embedder(model)` → `np.asarray(..., dtype=float64)` → `simhash_band_hashes_batch`
  → group `(band, bucket)` into `BlockResult`s (same shape/contract as
  `MinHashLSHBlocker`; drops all-zero/empty rows). Wire `strategy="simhash"` into
  `build_blocks` dispatch.

### TS

`src/core/simhash.ts` (pure-TS, `number` math) for kernel parity (golden
vectors). No TS semantic blocker (no real embedder) — documented.

## Parity & correctness

- A SimHash golden-vector fixture (`sketch_simhash_golden.json`) of fixed input
  vectors + params → signature + band hashes, generated from the Python
  reference; checked by Rust + Python (+ TS for the function). `GOLDENMATCH_NATIVE=0/1`
  native↔python parity sweep.
- f64 everywhere removes the f32/f64 sign-boundary divergence risk; the `dot == 0`
  tie is pinned. Real near-zero dots over random planes are astronomically rare,
  but the contract is still exact for golden inputs.

## Tests

- **Phase A:** `_is_text_corpus` / routing unit tests — a text-corpus df →
  `strategy=="lsh"` on the right column; a structured df with a name + a
  description → still name/multi-pass (guards the false-positive); a corpus that
  also has a low-cardinality (`< 0.1`) name-classified column → still `lsh` (pins
  the guard threshold); a **controller-survival** test driving the
  `AutoConfigController` on a text-corpus shape that asserts the committed
  `lsh`/`simhash` strategy is not swapped to `static`/`multi_pass`; zero-config
  end-to-end `dedupe_df` on a tiny text corpus produces sensible clusters.
- **Phase B:** SimHash kernel golden + Rust↔Python parity (`test_native_simhash_parity.py`);
  `SimHashLSHBlocker` test with synthetic embeddings (known high-cosine pairs
  cluster, orthogonal ones don't); routing test (`_embedder_available()` patched
  true → `strategy=="simhash"`). SimHash recall vs cosine threshold on synthetic
  vectors as an always-on gate.
- **Recall A/B:** extend `scripts/bench_lsh_recall_qqp.py` with `--method
  {lexical,semantic}`; the bench workflow runs both and reports the pair — the
  honest payoff is semantic recovering materially more QQP paraphrases than
  lexical's 0.21.

## Docs / rollout

`blocking.mdx` (new `simhash` strategy + "auto-enabled for text corpora" note on
`lsh`), `configuration.mdx`, `tuning.mdx` (the `simhash` native component),
CHANGELOGs (py + ts), a context-network ADR, and a zero-config text-corpus
example. Swept via the rollout-docs-sweep skill.

## File manifest (new / modified)

**Phase A (new):** `tests/test_text_corpus_autoconfig.py`.
**Phase A (modified):** `core/autoconfig.py` (`_is_text_corpus`, `_auto_build_lsh_config`,
`_text_corpus_blocking`, `_embedder_available`, routing in `build_blocking` — replacing
the `_ann_eligible` block); `core/autoconfig_rules.py` (guard `rule_blocking_key_swap`,
`rule_cross_blocking_disagreement`, and the multi-pass promotion rules to early-return
`None` when `blocking.strategy in {"lsh","simhash"}`).

**Phase B (new):** `packages/rust/extensions/sketch-core/src/simhash.rs`;
`native/src/sketch.rs` (+ simhash fns) ; `core/simhash_blocker.py`;
`core/sketch.py` (+ SimHash reference/fallback); `scripts/gen_simhash_golden.py`
+ `tests/fixtures/sketch_simhash_golden.json`; `tests/test_simhash_reference.py`,
`tests/test_native_simhash_parity.py`, `tests/test_simhash_blocker.py`;
`packages/typescript/goldenmatch/src/core/simhash.ts` + golden test.
**Phase B (modified):** `sketch-core/src/lib.rs`, `native/src/lib.rs`,
`native/Cargo.toml`; `config/schemas.py` (`SimHashKeyConfig`, `strategy="simhash"`);
`core/blocker.py` (dispatch); `_native_loader.py` (note); `.github/workflows/ci.yml`
(already covers sketch-core); `scripts/bench_lsh_recall_qqp.py` (`--method`).

## Risks & mitigations

- **False-positive routing** (structured-with-description → LSH) → the
  no-blockable-name guard + the unit test that pins a name+description df to
  name-blocking.
- **SimHash f64 parity** → f64 everywhere + pinned tie + golden vectors; TS
  semantic path is explicitly out (no embedder).
- **Projection-matrix cost** → generated once per batch (not per row); rayon
  guard reused.
- **Embedder availability flapping routing** → routing reads
  `_embedder_available()` once at config time.
- **Controller swapping the near-dup strategy** → the refit rules that rewrite
  `blocking.strategy` (`rule_blocking_key_swap`, `rule_cross_blocking_disagreement`,
  multi-pass promotions) are guarded to skip `lsh`/`simhash` configs; a
  controller-survival regression test pins this.
- **Auto-config regression** → the text-corpus branch only fires on the narrow
  detected shape; the existing exact/name paths are untouched; guard with the
  structured-df test + the standard auto-config suite.
