# Arrow-native fingerprints in identity resolution (#663 Sub-project A) — design

**Date:** 2026-06-01
**Status:** design (approved, pre-plan)
**Decision context:** Issue #663 ("route dict-I/O kernels through Arrow arrays
like score_block_pairs_arrow"). The Arrow-native roadmap Phase 3 (#625) built two
remaining dict-floor kernels' Arrow variants but never wired them in. #663 is
decomposed into Sub-project A (fingerprints, THIS spec — clear leverage) and
Sub-project B (build_clusters_arrow — deferred, marginal 1.09x dict-floor).

## Problem

The identity-resolution path fingerprints records ONE AT A TIME in a Python loop.
`identity/resolve.py::_record_id_candidates` (called per row from
`resolve_clusters`) computes
`record_fingerprint(_canonical_payload(payload))[:12]` for each no-PK record.
This per-row call scales with record count -- at 1M-5M identity resolution it is
a real cost.

The Arrow batch kernel that eliminates this already EXISTS but is unused:
`core/_hashing.py::record_fingerprints_batch_arrow(records_df)` ->
`packages/rust/extensions/native/src/hash.rs::record_fingerprints_batch_arrow`.
It reads the DataFrame's columns as zero-copy Arrow arrays, hashes each row in
Rust (rayon-parallel SHA-256, no per-record Python dict construction, no per-cell
pyo3 marshalling), and returns one hex string per row. The `hashing` component is
signed off (`_native_loader._GATED_ON`). The dict-shaped batch kernel
(`record_fingerprints_batch`) is NOT the answer: Stage 0 benched it at 0.71x
(slower than Python) -- the dict construction IS the cost. Only the Arrow path
removes the boundary tax.

## Goal

Wire `record_fingerprints_batch_arrow` into identity resolution so no-PK record
ids are computed in one vectorized batch instead of a per-row loop, with
**byte-identical** fingerprints (entity ids must not move) and an end-to-end
measured win.

## The parity crux

**Confirmed by spec review (de-risks the design):** the single-record kernel
(`record_fingerprint`, hash.rs:64) and the batch-arrow kernel
(`record_fingerprints_batch_arrow`, hash.rs:148) BOTH build `Vec<(String,
FpValue)>` and call the SAME `goldenmatch_fingerprint_core::fingerprint_fields`
(fingerprint-core/src/lib.rs:54) -- shared field ordering, type tags
(`n/b/i/f/s/y`), separators, null handling, `-0.0`->`0.0` collapse. So a CLEAN
all-primitive frame (Int64 / finite Float64 / Bool / Utf8 / Null) is byte-identical
to the per-row path FOR FREE. There is no kernel-level canonicalization mismatch.

The REAL risk is narrower: `record_fingerprints_batch_arrow` reads columns
directly (Utf8/LargeUtf8/Int64/Float64/Boolean; null -> `FpValue::Null`; `__`-cols
dropped), but the per-row path hashes `_canonical_payload(payload)`, which COERCES
(resolve.py:89) -- and the coercions do NOT all have a clean columnar equivalent.
A vectorized `_canonicalize_records_df` reproduces the coercible ones; rows that
can't be reproduced columnarly ROUTE TO THE PER-ROW FALLBACK (correctness over
speed). Three concrete cases the review pinned down:

- **Temporals (Date/Datetime/Time):** per-row uses Python `isoformat()`. Polars
  `dt.to_string()` / `cast(Utf8)` does NOT match: Datetime uses a SPACE separator
  (`2020-01-02 03:04:05`) vs isoformat's `T`; Time `cast` DROPS microseconds. The
  canonicalize step MUST reproduce `isoformat()` exactly -- Datetime via
  `dt.to_string("%Y-%m-%dT%H:%M:%S%.f")` AND handle the microsecond==0 case
  (Python `isoformat()` omits the fractional part entirely when usec==0; `%.f`
  must be verified/trimmed to match). Date already matches both ways (`2020-01-02`).
  The parity fixture gates this with both sub-second and zero-microsecond values.
- **Mixed finite/non-finite float columns: NO columnar equivalent -> per-row
  fallback.** A Polars column is single-dtype. If any cell is non-finite, casting
  the column to Utf8 also turns the FINITE cells into strings -> they'd hash as
  `FpValue::Str("1.5")` (tag `s`) instead of `FpValue::Float` (tag `f`) -> diverge.
  The per-row path is per-cell (finite stays float, non-finite -> `repr` string)
  and cannot be reproduced columnarly. Therefore: any row containing a non-finite
  float in any float column routes to the per-row fallback. (A float column with
  ONLY finite values stays Float64 and hashes identically -- no fallback needed.)
- **bytes columns: NO columnar equivalent -> per-row fallback.** The kernel
  rejects Binary (hash.rs ArrayKind::from_data); the per-row path hashes bytes as
  `FpValue::Bytes` (tag `y`). Casting to Utf8 changes the tag `y`->`s` and breaks
  parity. Bytes-bearing rows route to the per-row fallback.
- Non-primitive object columns (lists/structs/decimals) -> `str()` per cell,
  matching `_canonical_payload`'s `else` branch; if not provably reproducible,
  fall back.
- Null cells -> `FpValue::Null` already matches per-row `None`.

The legacy-fallback for un-fingerprintable rows (`:hash:` id) is preserved.

## Approach (vectorized canonicalize -> batch arrow -> per-record id logic)

`resolve_clusters` already receives `df: pl.DataFrame` and does `rows =
df.to_dicts()` (resolve.py:218) then loops per row (`:227`), so the frame already
exists -- "assemble a frame" is trivial.

1. **Partition rows into the no-PK subset** (the only rows that fingerprint).
   When `source_pk_col` is set and present+non-null, `_record_id_candidates`
   returns `source:pk` WITHOUT hashing (resolve.py:125-128). The batch path
   computes fingerprints ONLY for the no-PK subset; PK rows keep their cheap path.
2. **`_canonicalize_records_df(df)`** -> a frame whose `__`-stripped columns
   reproduce `_canonical_payload` per row (the parity crux above). Rows that hit
   the no-columnar-equivalent cases (non-finite float, bytes, un-reproducible
   object) are MARKED for the per-row fallback rather than canonicalized.
   `_canonicalize_records_df` MUST run before the wrapper in ALL modes (native
   on AND off): the off-native fallback inside the wrapper calls
   `record_fingerprint` on raw dicts, which RAISES on raw temporals / non-finite
   floats (`_hashing.py:139-141` does NOT apply `_canonical_payload`). Feeding it
   the canonicalized frame is what keeps the off-native path correct.
3. `record_fingerprints_batch_arrow(canonical_df)` -> `list[str]` hashes aligned
   to the no-PK rows.
4. Build ids: `h1_id = f"{src}:h1:{hash[:12]}"` where `src` is **per-row**
   (`str(row.get("__source__", "dataframe"))`, resolve.py:232) -- zip the hashes
   with the per-row source, NOT a scalar. Apply the `h1`/`hash` scheme
   (`_id_scheme()`) and the legacy candidate ordering exactly as
   `_record_id_candidates` does today.
5. The per-row `_record_id_candidates` stays as the fallback path (the marked
   rows from step 2; and the whole batch path is gated so it can be disabled).

### Rejected alternatives
- **Dict batch kernel (`record_fingerprints_batch`):** safe parity (uses the same
  `_canonical_payload`) but Stage 0 = 0.71x; the dict construction is the cost.
  Does not solve #663.
- **Feed the raw frame to the Arrow kernel without canonicalization:** fast but
  breaks parity on temporals / non-finite floats -> entity ids move. Unacceptable.

## Testing

- **Parity gate (HARD, the safety net):** an adversarial fixture frame that MUST
  include the cases the review pinned -- Utf8, Int64, Float64 finite, a float
  column with mixed finite + nan/inf/-inf, Boolean, Null, Date, Datetime with
  sub-second AND with usec==0, Time with microseconds, a list/struct object
  column, bytes. Assert the batched path (`_canonicalize_records_df` -> batch,
  with marked rows routed per-row) returns BYTE-IDENTICAL hashes to
  `[record_fingerprint(_canonical_payload(r)) for r in df.to_dicts()]`, row for
  row. Any mismatch fails -- this is the entity-id durability guarantee. The
  datetime separator (`T` vs space), Time microseconds, mixed-float fallback, and
  bytes fallback are the specific things this fixture catches.
- **Off-native parity (run the SAME fixture with `GOLDENMATCH_NATIVE=0`):** the
  canonicalized frame must produce identical ids on the dict/per-row fallback too.
  Critically, this confirms `_canonicalize_records_df` runs before the wrapper in
  off-native mode (else the raw-dict fallback raises on temporals/non-finite).
- **Identity end-to-end parity:** run `resolve_clusters` on a fixture both ways
  (batch path on, per-row path) and assert the resulting `entity_id`s / record
  ids are identical, including a no-PK + PK mix.
- **Measure-first (gate on shipping default-on):** bench identity resolution
  end-to-end at 1M-5M deduped records (wall + peak RSS) batch vs per-row on
  `large-new-64GB`. The fixture must be **no-PK-heavy** -- PK rows bypass
  fingerprinting entirely, so a PK-backed frame under-reads the kernel's value.
  Ship default-on only if it wins meaningfully; otherwise keep it gated and record
  the finding (repo perf-audit lesson -- measure wall-clock on the real shape).
- **Legacy-fallback preserved:** a row whose payload can't be fingerprinted still
  yields the `:hash:` legacy id + candidate ordering unchanged.

## Scope boundary (YAGNI)

- ONLY wire fingerprints in `identity/resolve.py`. Do NOT touch build_clusters
  (Sub-project B), the dedup path (deprioritized), the native kernels (they
  exist), or the canonical-fingerprint spec itself.
- Do NOT change the `h1`/`hash` scheme, the legacy-fallback semantics, or the
  `GOLDENMATCH_IDENTITY_ID_SCHEME` kill-switch.
- A new gate (env var or `config.identity` flag) toggles the batch path so the
  per-row path remains available; default-on decided by the measure-first bench.

## References

- Issue #663; roadmap `docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md`
  (Phase 3); bulk-fingerprint kernel spec
  `docs/superpowers/specs/2026-05-30-bulk-record-fingerprint-kernel-spec.md`.
- Python: `core/_hashing.py` (`record_fingerprint`, `record_fingerprints_batch`,
  `record_fingerprints_batch_arrow`); `identity/resolve.py`
  (`_record_id_candidates`, `_canonical_payload`, `_id_scheme`, `resolve_clusters`).
- Rust: `packages/rust/extensions/native/src/hash.rs::record_fingerprints_batch_arrow`.
- Proven pattern: `score_block_pairs_arrow` (the wired, default Arrow kernel).
- Durability invariant: identity-graph design
  (`docs/superpowers/specs/2026-05-12-identity-graph-design.md`); record-hash
  stability (`[[project_stable_record_fingerprint]]`).
