# Bulk record_fingerprint kernel — design

**Status:** Draft, 2026-05-30
**Targets:** Identity Graph upsert path; gated on `config.identity.enabled`
**Lesson carried forward:** cluster-orchestration kernel bench (PR #610/#611)
showed the **dict-construction floor** kills Rust kernels that return Python
dicts. This kernel returns `list[str]` — no dict floor.

## Problem

`identity/resolve.py:233` iterates rows in a hot loop:

```python
for row in rows:
    ...
    primary_id, candidates = _record_id_candidates(row, source, source_pk_col)
    ...
```

`_record_id_candidates` (line 110-139) calls `record_fingerprint` (native
single-record kernel) for rows without a natural PK. At N records that's:

- N PyDict creations (the `_canonical_payload` step)
- N FFI hops into Rust (`record_fingerprint(record: dict)`)
- N internal pyo3 dict iterations
- N SHA-256 computations
- N hex-encodings
- N Python `str` returns

Per-record FFI overhead is ~100ns. At 10M records that's only ~1 s of pure
overhead — small. But the per-record Python loop also pays for:

- Python interpreter overhead between records (~10-20 us each at 10M records
  → ~100-200 s)
- Per-row `_canonical_payload` allocation
- Per-row `_hash_payload` (legacy json hash, single-record only)
- Sequential compute — no parallelism

The bulk variant amortizes Python interpreter cost across the batch AND
parallelizes the SHA-256 work across cores.

## Goal

Add `record_fingerprints_batch(records: list[dict]) -> list[str]` that:

1. Iterates records in Rust (no Python interpreter between iterations).
2. Parallelizes SHA-256 + hex via rayon (16-core boxes finish 10M records
   in ~2-3 s instead of 10-15 s).
3. Returns `Vec<String>` → `list[str]` — flat list output, **no dict
   construction**, so the cluster-kernel bench's dict-floor result doesn't
   apply.

## API design

```rust
#[pyfunction]
pub fn record_fingerprints_batch(
    py: Python<'_>,
    records: Vec<Bound<'_, PyDict>>,
) -> PyResult<Vec<String>>;
```

Behavior:
- Each input dict is processed identically to the existing single-record
  `record_fingerprint` — same canonicalization, same SHA-256, same hex
  output, same `__`-prefix-drop rule.
- Returns one hex string per input record, in order.
- Errors propagate from the FIRST failing record (matching Python
  `try`/`except` ordering would change a "stop on first error" contract
  to "continue and report all errors"; defer that to v2).
- The interior is `py.allow_threads(|| rayon::par_iter(...))` so the GIL
  is released for the bulk SHA-256 + hex work.

## What stays in Python

- `_canonical_payload` (Python-side temporal coercion). Could be ported
  but adds complexity for a single-digit-percent win; defer.
- `_hash_payload` (legacy JSON-based hash). Also called per-row but
  separate concern.
- The lookup-candidate construction (`legacy_id` + `h1_id` formatting,
  primary/candidates ordering).

The bulk kernel only covers the SHA-256 + hex pipeline. The wrapper
function in `_hashing.py` becomes:

```python
def record_fingerprints_batch(records: list[dict]) -> list[str]:
    if native_enabled("hashing"):
        return native_module().record_fingerprints_batch(records)
    return [record_fingerprint(r) for r in records]
```

And `identity/resolve.py` gets refactored to collect canonical payloads
first, call the bulk fingerprint once, then zip the IDs back into the
candidate-construction loop.

## Decision gate

Decision gate: **large-shape speedup ≥ 2×** at 1M records → ship the
wire-up PR. Below that the win is too small to justify the refactor in
`identity/resolve.py`.

The bench script (`scripts/bench_native_bulk_fingerprint.py`) measures
three shapes:

| shape | records | predicted Python | predicted bulk | predicted speedup |
|---|---|---|---|---|
| smoke | 10K | 0.5 s | 0.2 s | 2.5× |
| medium | 100K | 5 s | 1 s | 5× |
| large | 1M | 50 s | 5 s | 10× |

Predictions are based on rayon-parallel SHA-256 throughput (~1 GB/s
per core) on 16-core hardware; real numbers will differ.

## Parity testing

`tests/test_native_bulk_fingerprint_parity.py`:
- Single-record outputs match `record_fingerprint` for the same input.
- Bulk-of-1 equals single-record.
- Bulk-of-N preserves order.
- Error-on-bad-record raises at the right index.
- Cross-surface byte-equivalence: bulk output matches the existing
  C-ABI `gm_record_fingerprint` (DuckDB/Postgres surfaces).

## Wire-up scope (separate PR after decision gate passes)

`identity/resolve.py` refactor:
1. Collect all rows-without-PK and their canonical payloads in a pre-pass.
2. Call `record_fingerprints_batch(payloads)` once.
3. Zip the resulting hex strings back into `_record_id_candidates`'s
   primary/candidates output.

Backward compat: callers of `record_fingerprint(single_dict)` keep working
(single-record entry stays in place); only the loop site changes.

## Risk

- **The per-row `_canonical_payload` Python pre-pass may dominate.** If
  Python's dict-walking + value-coercion is the floor (not the SHA-256
  compute), the bulk kernel can't help past that. Bench measures the
  COMPLETE wrapped path, including the Python pre-pass, so the decision
  gate captures this honestly.
- **Per-record dict iteration inside Rust is still pyo3-bound.** Each
  `Bound<'_, PyDict>` iteration acquires the GIL implicitly. We release
  the GIL via `py.allow_threads()` only AFTER extracting field tuples;
  the extraction phase is sequential. If extraction is most of the wall,
  rayon parallelism doesn't help.

These are the same dict-floor risks the cluster kernel hit, surfaced
honestly in the bench shape so we don't waste a wire-up PR.

## Implementation steps

1. **Rust kernel** in `hash.rs`: add `record_fingerprints_batch`. Two
   phases — sequential pyo3 field extraction, then `py.allow_threads`
   over rayon for SHA-256 + hex.
2. **Python wrapper** in `_hashing.py`: thin native dispatch + Python
   fallback.
3. **Bench script** `scripts/bench_native_bulk_fingerprint.py`: same
   smoke/medium/large shape pattern as the cluster bench.
4. **Bench workflow** `.github/workflows/bench-native-bulk-fingerprint.yml`:
   workflow_dispatch only, posts the speedup table to step summary.
5. **Parity tests** `tests/test_native_bulk_fingerprint_parity.py`:
   lock down the contract before wiring.

## Out of scope for this PR

- `identity/resolve.py` refactor (separate PR after decision gate).
- C ABI `gm_record_fingerprints_batch` for the DuckDB/pgrx surfaces
  (separate; lower priority).
- v1.1 canonical payload spec (temporals etc. inline in Rust).
- Removing the legacy `_hash_payload` (json-based) call site — that
  hash has different bytes and is a separate decision.

## Decision needed

Approve scope → ship the prototype kernel + bench + parity tests. The
bench result picks the path: wire-up PR or document-and-pivot.
