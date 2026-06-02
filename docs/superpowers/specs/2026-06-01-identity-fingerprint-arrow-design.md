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

## The parity crux (the core design risk)

`record_fingerprints_batch_arrow` hashes the frame's columns directly (supported
dtypes: Utf8/LargeUtf8/Int64/Float64/Boolean; null -> `FpValue::Null`; `__`-cols
dropped). But the per-row path hashes `_canonical_payload(payload)`, which
COERCES (resolve.py:89):

- non-finite floats (`nan`/`inf`/`-inf`) -> their `repr()` token STRING;
- temporals / other non-primitives -> `isoformat()` or `str()`;
- None/bool/int/float/str/bytes -> as-is.

So feeding the RAW records frame to the Arrow kernel would NOT match the per-row
hashes (a Date column hashes as a date, not its ISO string; a float `nan` hashes
as a float, not `"nan"`). Entity ids would silently change -- a durability
disaster (the identity graph keys stable `entity_id`s off these fingerprints).

**Therefore the core deliverable is a vectorized `_canonicalize_records_df`** that
reproduces `_canonical_payload`'s per-row coercion in Polars expressions BEFORE
the batch call, so the frame fed to the kernel yields byte-identical hashes:

- Temporal columns (Date/Datetime/Time/Duration) -> Utf8 via the SAME format
  `isoformat()` produces (verify exact format: Polars `dt.to_string` /
  `str()` of the python temporal -- the test gates this).
- Float columns -> replace non-finite cells with their token strings
  (`"nan"`/`"inf"`/`"-inf"`), which forces the column to Utf8 only if non-finite
  values are present (finite floats stay Float64 and hash identically).
- Non-primitive object columns (lists, structs, decimals, etc.) -> `str()` per
  cell (vectorized via `map_elements` only where unavoidable; these are the rare
  case `_canonical_payload`'s `else` branch handles).
- bytes columns -> the kernel dtype list omits Binary; either cast to the same
  representation the per-row path uses, OR route bytes-bearing rows to the
  per-row fallback (decide by test).
- Null cells -> `FpValue::Null` already matches the per-row `None`.

If a column or row cannot be canonicalized to a kernel-supported dtype with
provable parity, that row falls back to the per-row path (correctness over
speed). The legacy-fallback for un-fingerprintable rows (`:hash:` id) is preserved.

## Approach (vectorized canonicalize -> batch arrow -> per-record id logic)

1. In `resolve_clusters` (or a new helper), assemble the no-PK records as a
   Polars DataFrame (they already originate from the deduped frame; confirm the
   data shape at plan time -- if records are dict rows, reassemble a frame once).
2. `_canonicalize_records_df(df)` -> a frame whose `__`-stripped columns
   reproduce `_canonical_payload` per row (the parity crux above).
3. `record_fingerprints_batch_arrow(canonical_df)` -> `list[str]` hashes aligned
   to rows (it already falls back to the dict path when native is absent --
   correctness preserved off-native).
4. Build ids vectorized: `h1_id = f"{source}:h1:{hash[:12]}"`, apply the
   `h1`/`hash` scheme (`_id_scheme()`) and the legacy candidate ordering exactly
   as `_record_id_candidates` does today. PK records (`source:pk`) skip
   fingerprinting entirely.
5. The per-row `_record_id_candidates` stays as the fallback path (rows that
   can't be canonicalized; and the whole batch path is gated so it can be
   disabled).

### Rejected alternatives
- **Dict batch kernel (`record_fingerprints_batch`):** safe parity (uses the same
  `_canonical_payload`) but Stage 0 = 0.71x; the dict construction is the cost.
  Does not solve #663.
- **Feed the raw frame to the Arrow kernel without canonicalization:** fast but
  breaks parity on temporals / non-finite floats -> entity ids move. Unacceptable.

## Testing

- **Parity gate (HARD, the safety net):** an adversarial fixture frame covering
  every supported + coercion dtype -- Utf8, Int64, Float64 (incl. nan/inf/-inf),
  Boolean, Null, Date, Datetime, a list/struct object column, bytes. Assert the
  batched path (`_canonicalize_records_df` -> `record_fingerprints_batch_arrow`)
  returns BYTE-IDENTICAL hashes to `[record_fingerprint(_canonical_payload(r))
  for r in df.to_dicts()]`, row for row. Any mismatch fails. This is the entity-id
  durability guarantee.
- **Identity end-to-end parity:** run `resolve_clusters` on a fixture both ways
  (batch path on, per-row path) and assert the resulting `entity_id`s / record
  ids are identical.
- **Off-native correctness:** with `GOLDENMATCH_NATIVE=0`, the path degrades to
  the dict/per-row fallback and still produces identical ids.
- **Measure-first (gate on shipping default-on):** bench identity resolution
  end-to-end at 1M-5M deduped records (wall + peak RSS) batch vs per-row on
  `large-new-64GB`. Ship default-on only if it wins meaningfully; otherwise keep
  it gated and record the finding (per the repo perf-audit lesson -- measure
  wall-clock on the real shape before trusting the microbench).
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
