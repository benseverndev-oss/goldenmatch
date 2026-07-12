# GoldenCheck W6 — semantic classification (decline + regex reuse) — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program; /goal "all Ws implemented"). Pending spec review.
Program: `...-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. **W6 — semantic (the final content wave before the Flip).**
Base: fresh `origin/main` (W0-land…W3 merged; W4 #1685 enqueuing). **Independent of W4/W5** — W6 reuses only the `regex` kernel (`str_contains_count`), on main since S2.2. Branch off current main; no W4 rebase needed.

## Goal

Resolve the semantic layer's place in the Rust-source-of-truth program. Recon (source-verified) splits it cleanly:

- **`baseline/semantic.py` `_infer_with_embeddings` — DECLINED (documented, stays Python/ML).** Semantic-type inference via `sentence_transformers` embeddings + cosine similarity is an ML model: non-deterministic across versions/hardware, not a fused-kernel candidate. This is exactly the program design's "W6 semantic stays ML fallback." The keyword fallback (`_infer_with_keywords`/`_match_column_keywords`) is pure-Python dict/substring matching — no scipy, no heavy compute, no Rust value.
- **`semantic/classifier.py` `_check_format_match` (classifier.py:197-209) — REUSE the `regex` kernel.** It does `non_null.str.contains(r"...", literal=False).sum()` for email/phone/date shape detection — EXACTLY what the existing `str_contains_count(values, pattern)` kernel computes (both Polars `str.contains` and the kernel use the Rust `regex` crate → byte-identical match count). Shadow-wire it.
- The other `_check_value_signals` (classifier.py:156-195: `min_unique_pct`, `max_unique`, `mixed_case`, `avg_length_min`, `numeric`, `short_strings`) are pure-Python/Polars primitives over already-covered quantities (n_unique, str-len, dtype) — no scipy, low value, out of scope.

So W6 = **shadow-wire `_check_format_match` to `str_contains_count`** + **formally record the embeddings decline** (the program's semantic-ML boundary). It closes the content waves: after W6, every scipy/Polars statistic on the scan path is either Rust-authoritative-in-shadow or an explicitly-registered decline (embeddings, scipy `.fit()`/kstest, the neutral dtype-string). That decline inventory is the input to the Flip gate.

## Wiring (shadow — mirrors W1–W5)
- `_check_format_match(non_null, format_type)`: after computing `matches = non_null.str.contains(pattern, literal=False).sum()`, when `native_enabled("regex")`, ALSO compute `native_module().str_contains_count(non_null.to_list(), pattern)` (or the Arrow-in form the kernel exposes — CHECK the `str_contains_count` signature: S2.2 built it as `&[Option<String>]`/list-based; pass `non_null.to_list()`) in shadow (try/except **BaseException**); discard. The authoritative `matches`/return stays Polars. Map the three patterns (`@.*\.`, `\d{3}.*\d{3}.*\d{4}`, `\d{4}-\d{2}-\d{2}`) through unchanged.
- If the kernel's list vs Polars `str.contains` count could differ on nulls: `non_null` is already null-dropped, so both count over the same non-null values — confirm in the shadow test.

## The decline inventory (record for the Flip gate)
W6 writes/updates a short decline registry (in the spec + a code comment) enumerating what STAYS Python and why — the Flip's §8b gate consumes this:
1. **Embeddings semantic inference** (`_infer_with_embeddings`) — ML model, non-deterministic. Fallback: keyword matching (deterministic, pure-Python).
2. **scipy distribution `.fit()` + `kstest`** (`baseline/statistical.py` `_fit_distribution`, `drift/detector.py` `_check_distribution_drift`) — numerical MLE + Kolmogorov p-value, not byte-reproducible (W4/W5 decline).
3. **`inferred_type` = `str(pl.dtype)`** — the neutral dtype-string divergence (W1), a registered Flip-gate bucket.
4. **`df.sample(seed=42)`** sampling PRNG + scipy KS/chi2/pearsonr p-value last-digits — the owned-contract epsilon classes (program design §OWNED CONTRACT).

## Parity / contract
- No NEW kernel, no NEW parity contract — `str_contains_count` (regex) is already parity-locked (S2.2). W6 adds only a shadow test asserting the kernel's count matches Polars `str.contains(...).sum()` on the three format patterns.
- Authoritative semantic classification UNCHANGED (shadow); embeddings path untouched; `import goldencheck` zero polars (semantic lazy-imports its deps); existing semantic/classifier tests UNEDITED.

## Testing
- Python: existing `semantic`/`classifier` tests UNEDITED green. A shadow test `tests/engine/test_w6_semantic_shadow.py`: for email/phone/date-shaped string columns, assert `str_contains_count(non_null.to_list(), pattern) == int(non_null.str.contains(pattern, literal=False).sum())` for the three patterns. `skipif` on `native_enabled("regex")`.
- `import goldencheck` zero polars; ruff clean. NO Rust changes (regex kernel already built) → no cargo/clippy/wasm.

## Risks
- **regex-count parity on the three patterns** — Polars `str.contains(literal=False)` and the Rust `regex` crate should be byte-identical (same engine), but VERIFY the `.*` / anchoring behavior matches on the shadow corpus (esp. the phone `\d{3}.*\d{3}.*\d{4}` with `.*` greediness — match COUNT is a presence test, so greediness doesn't change presence). Register nothing unless a real divergence appears.
- **thin wave** — like W5, the value is closing the decline inventory + one reuse-wiring, not new kernels. That's the correct end state: the semantic ML layer SHOULD stay Python.
- **`str_contains_count` input form** — confirm whether it takes a Python list or an Arrow array; pass the matching shape.

## Non-goals
- No embedding/ML reproduction in Rust (declined — the whole point). No kerneling the pure-Python value-signal primitives. No changing user-visible output (shadow). No polars-free wiring (Flip). No new kernels.
