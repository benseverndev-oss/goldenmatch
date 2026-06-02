# Arrow-native fingerprints in identity resolution (#663-A) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-row `record_fingerprint` loop in identity resolution with one vectorized `record_fingerprints_batch_arrow` call for the no-PK record subset, producing byte-identical entity ids.

**Architecture:** A new `batch_fingerprints(df) -> list[str | None]` returns per-row fingerprints (None = un-fingerprintable -> legacy id), using the Arrow batch kernel for fully-batchable rows and a per-row fallback for rows/columns that can't be reproduced columnarly. It is a drop-in for `[<per-row hash> for r in df.to_dicts()]`, so parity is one assertion. `resolve_clusters` batch-computes the no-PK hashes once and threads them into `_record_id_candidates`. The whole path is gated; default-on is decided by a measure-first bench.

**Tech Stack:** Python 3.11+, Polars, the `goldenmatch._native` Rust kernel (`record_fingerprints_batch_arrow`, already built + signed off as the `hashing` component). Tests via pytest.

**Spec:** `docs/superpowers/specs/2026-06-01-identity-fingerprint-arrow-design.md` — READ its "parity crux" routing table; this plan implements it. **Branch:** `perf/identity-fingerprint-arrow`.

**Run tests:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v`. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`. Native `_native.pyd` is built in-tree locally. Do NOT run the full suite (xdist OOMs); targeted files only.

---

## Background the implementer needs

- `identity/resolve.py::_record_id_candidates(row, source, source_pk_col)` (~line 110) is called per row from `resolve_clusters` (~line 227-238). For a row WITHOUT a usable source PK it computes `record_fingerprint(_canonical_payload(payload))[:12]` (`resolve.py:132`); with a PK it returns `f"{source}:{pk}"` and never hashes (`resolve.py:125-128`).
- `_canonical_payload` (`resolve.py:89`) coerces a payload dict to fingerprint-able primitives: non-finite float -> `repr(v)` string; temporals/objects -> `isoformat()`/`str()`; None/bool/int/float/str/bytes -> as-is.
- `core/_hashing.py::record_fingerprints_batch_arrow(records_df)` (line 112) takes a Polars DataFrame whose columns ARE the record fields (drops `__`-prefixed cols), returns `list[str]` (one 64-char hex per row). It falls back to the dict path off-native, BUT that fallback hashes RAW dicts and raises on raw temporals/non-finite — so the frame handed to it MUST be canonicalized first.
- **Parity is free for clean primitives:** both the single + batch kernels delegate to the same `fingerprint_fields` (`packages/rust/extensions/fingerprint-core/src/lib.rs:54`). The work is reproducing `_canonical_payload`'s coercions columnarly (and routing the un-reproducible cases to per-row). See the spec's routing table for the EXACT rules (temporal `%.6f`+strip, narrow-int/Float32 up-cast, bare-Null->Utf8, and per-row fallback for: mixed-finite-float rows, bytes/Duration/tz-aware/non-`us`-Datetime columns, UInt64>2**63 rows, un-reproducible objects).

---

## File Structure

- **Create** `packages/python/goldenmatch/goldenmatch/identity/fingerprint_batch.py` — owns `_canonical_payload` (MOVED here from resolve.py to break the import cycle), `canonicalize_records_df`, and `batch_fingerprints`. Single responsibility: turn a records frame into per-row fingerprints with canonicalization + fallback.
- **Modify** `packages/python/goldenmatch/goldenmatch/identity/resolve.py` — import `_canonical_payload` from the new module (back-compat); add `precomputed_h1` to `_record_id_candidates`; batch-compute no-PK hashes in `resolve_clusters`; add the gate flag.
- **Test** `packages/python/goldenmatch/tests/identity/test_fingerprint_batch.py` (create) — the parity gate + off-native parity.
- **Test** `packages/python/goldenmatch/tests/identity/test_resolve_batch_parity.py` (create) — end-to-end identity-id parity.
- **Create** `packages/python/goldenmatch/scripts/bench_identity_fingerprint.py` + `.github/workflows/bench-identity-fingerprint.yml` — the measure-first bench (Task 4).

---

## Task 1: `batch_fingerprints` + canonicalization (the core + parity gate)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/identity/fingerprint_batch.py`
- Modify: `packages/python/goldenmatch/goldenmatch/identity/resolve.py` (move `_canonical_payload` out, import it back)
- Test: `packages/python/goldenmatch/tests/identity/test_fingerprint_batch.py`

- [ ] **Step 1: Move `_canonical_payload` to the new module, re-import in resolve.py**

Cut `_canonical_payload` (resolve.py:89-107) + its `math` import need into `identity/fingerprint_batch.py`. In `resolve.py` add `from goldenmatch.identity.fingerprint_batch import _canonical_payload`. Run the existing identity tests to confirm no breakage:
`../../../.venv/Scripts/python.exe -m pytest tests/identity/ -q` — expect all pass (pure move).
Commit: `refactor(identity): move _canonical_payload to fingerprint_batch module`.

- [ ] **Step 2: Write the failing parity test**

`tests/identity/test_fingerprint_batch.py`. The reference is the exact per-row computation (hash, or None when `record_fingerprint` raises -> legacy):

```python
import datetime as dt
from decimal import Decimal
import polars as pl
import pytest
from goldenmatch.core._hashing import record_fingerprint
from goldenmatch.identity.fingerprint_batch import _canonical_payload, batch_fingerprints


def _perrow(row: dict) -> str | None:
    try:
        return record_fingerprint(_canonical_payload(row))
    except (TypeError, ValueError):
        return None


def _adversarial_df() -> pl.DataFrame:
    # Every routing-table case from the spec. Columns are record fields.
    return pl.DataFrame({
        "s": ["alice", "bob", None, "x"],
        "i64": [1, 2, 3, 4],
        "f_finite": [1.5, 2.0, 3.25, 0.1],
        "f_mixed": [1.5, float("nan"), float("inf"), -2.0],   # row-level fallback
        "b": [True, False, None, True],
        "d": [dt.date(2020, 1, 2), dt.date(1999, 12, 31), None, dt.date(2000, 2, 29)],
        "dt_us": [dt.datetime(2020, 1, 2, 3, 4, 5, 123000),    # .123000 -> catches %.f bug
                  dt.datetime(2020, 1, 2, 3, 4, 5, 500000),
                  dt.datetime(2020, 1, 2, 3, 4, 5, 0),         # usec==0
                  dt.datetime(2020, 1, 2, 3, 4, 5, 123456)],
        "i32": pl.Series([10, 20, 30, 40], dtype=pl.Int32),
        "f32": pl.Series([0.1, 0.2, 0.3, 0.4], dtype=pl.Float32),
        "allnull": pl.Series([None, None, None, None]),        # bare Null dtype
        "dec": [Decimal("2.00"), Decimal("3.5"), None, Decimal("0")],
    })


def test_batch_fingerprints_parity():
    df = _adversarial_df()
    assert batch_fingerprints(df) == [_perrow(r) for r in df.to_dicts()]
```

Add separate focused fixtures + tests for the column-level fallback dtypes that need special construction: a `Duration` column, a `Time`-with-microseconds column, a tz-aware Datetime, a non-`us` (`ms`) Datetime, a `bytes` column, a `UInt64` column with a value `> 2**63`. Each asserts `batch_fingerprints(df) == [_perrow(r) for r in df.to_dicts()]`.

- [ ] **Step 3: Run it — verify FAIL**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/identity/test_fingerprint_batch.py -v`
Expected: FAIL (`batch_fingerprints` not defined / not imported).

- [ ] **Step 4: Implement `canonicalize_records_df` + `batch_fingerprints`**

In `fingerprint_batch.py`, implement per the spec routing table. Suggested shape:

```python
def canonicalize_records_df(df):
    """Return (batch_df, fallback_mask): a frame safe for the Arrow kernel over
    the batchable rows, plus a bool list marking rows that must go per-row.
    Drops __-prefixed columns (the kernel and per-row payload both exclude them).
    Column-level un-batchable dtypes (bytes/Duration/tz-aware or non-us Datetime/
    un-reproducible object) force the WHOLE frame to fallback (the column is part
    of every row's hash and has no parity-preserving cast). Row-level cases
    (non-finite float, UInt64>2**63) mask only those rows."""
    # 1. drop __-cols. 2. classify each column. 3. if any column-level un-batchable
    #    -> return (None, [True]*height). 4. else canonicalize batchable columns
    #    (temporal %.6f+strip, narrow-int/Float32 upcast, bare-Null->Utf8); build
    #    row mask over non-finite-float + UInt64-overflow; return (batch_df, mask).

def batch_fingerprints(df) -> list:
    """Per-row fingerprints aligned to df rows; None where un-fingerprintable."""
    from goldenmatch.core._hashing import record_fingerprint, record_fingerprints_batch_arrow
    out: list = [None] * df.height
    batch_df, mask = canonicalize_records_df(df)
    if batch_df is not None and batch_df.height:
        hashes = record_fingerprints_batch_arrow(batch_df)
        # scatter hashes back to the non-masked original row positions (order
        # preserved: batch_df rows are the ~mask rows in original order).
        bi = 0
        for i in range(df.height):
            if not mask[i]:
                out[i] = hashes[bi]; bi += 1
    # per-row fallback for masked rows
    rows = df.to_dicts()
    for i in range(df.height):
        if mask[i]:
            try:
                out[i] = record_fingerprint(_canonical_payload({k: v for k, v in rows[i].items() if not k.startswith("__")}))
            except (TypeError, ValueError):
                out[i] = None
    return out
```

Key correctness points to get right (cite the spec): temporal recipe
`dt.to_string("%Y-%m-%dT%H:%M:%S%.6f").str.replace(r"\.000000$", "")` (Date via
`%Y-%m-%d`); `Float32->Float64`, `Int8/16/32`+`UInt8/16/32 -> Int64`; bare-`Null`
dtype `cast(Utf8)`; row mask over EVERY float col's `is_finite` + UInt64>2**63;
column-level fallback (return `(None, all-True)`) for bytes/Duration/tz/non-`us`
Datetime/un-reproducible object. The `batch_df` rows MUST stay in original order so
the scatter-back is a simple sequential fill.

- [ ] **Step 5: Run the parity tests — verify PASS**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/identity/test_fingerprint_batch.py -v`
Expected: all pass. If any dtype mismatches, PRINT `batch_fingerprints(df)` vs the per-row list side by side and fix the canonicalization for that dtype — do NOT weaken the assertion.

- [ ] **Step 6: Off-native parity test**

Add to the same file, with `monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")`: the SAME `_adversarial_df` must yield `batch_fingerprints(df) == [_perrow(r) for r in df.to_dicts()]` (the wrapper's dict fallback runs on the canonicalized frame). Confirms canonicalize-before-wrapper in off-native mode.

- [ ] **Step 7: ruff + commit**

ruff check both files. Commit: `feat(identity): batch_fingerprints with vectorized canonicalization + per-row fallback`.

---

## Task 2: Wire `batch_fingerprints` into `resolve_clusters` (gated)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/identity/resolve.py` (`_record_id_candidates` ~110, `resolve_clusters` loop ~227-238)
- Test: `packages/python/goldenmatch/tests/identity/test_resolve_batch_parity.py`

- [ ] **Step 1: Add `precomputed_h1` to `_record_id_candidates`**

Signature: `_record_id_candidates(row, source, source_pk_col, *, precomputed_h1=_NOT_BATCHED)` where `_NOT_BATCHED` is a module sentinel. Behavior:
- PK path unchanged (returns `source:pk`, never uses precomputed_h1).
- No-PK path: if `precomputed_h1 is _NOT_BATCHED` -> compute as today (per-row `record_fingerprint`). If `precomputed_h1` is a `str` -> use it as the h1 hash (build `h1_id = f"{source}:h1:{precomputed_h1[:12]}"`), skipping the per-row call. If `precomputed_h1 is None` -> the batch determined the row un-fingerprintable -> take the legacy-only path (same as today's `except` branch: `return legacy_id, [legacy_id]`).
The candidate ordering + `_id_scheme()` logic is otherwise unchanged.

- [ ] **Step 2: Write the failing end-to-end parity test**

`tests/identity/test_resolve_batch_parity.py`: run `resolve_clusters` on a fixture (mix of no-PK rows incl. the adversarial dtypes AND some PK rows) twice — once with the batch path ON, once OFF (per-row) — and assert the resulting `_rowid_primary` / `_rowid_candidates` (or the stored entity ids) are IDENTICAL. Reuse an existing identity test fixture/store pattern from `tests/identity/`. Verify it FAILS first (batch path not wired).

- [ ] **Step 3: Batch-compute no-PK hashes in `resolve_clusters` + thread in**

Behind the gate flag (Step 4), BEFORE the `for row in rows:` loop: build the no-PK subset (rows where `source_pk_col` is unset or the pk cell is null), call `batch_fingerprints` on `df` (or the no-PK sub-frame — keep row_id alignment), producing `h1_by_rowid: dict[int, str | None]`. In the loop, pass `precomputed_h1=h1_by_rowid.get(irid, _NOT_BATCHED)` (PK rows aren't in the dict -> `_NOT_BATCHED` -> unchanged path). When the gate is off, pass nothing (per-row path, byte-identical to today).

NOTE on alignment: `batch_fingerprints(df)` returns a list aligned to `df` rows in order; map it back to `__row_id__` via the same `rows` iteration so `h1_by_rowid[irid] = hashes[row_index]`. Only populate the dict for no-PK rows.

- [ ] **Step 4: Add the gate flag**

Default OFF until the bench (Task 4) justifies default-on. Use an env gate `GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT` (default `0`) read in `resolve_clusters` (mirrors the `GOLDENMATCH_IDENTITY_ID_SCHEME` env pattern at `resolve.py:84`). Document it next to that one.

- [ ] **Step 5: Run the parity test (gate ON) — verify PASS**

Run with `GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT=1`: `../../../.venv/Scripts/python.exe -m pytest tests/identity/test_resolve_batch_parity.py -v` — expect identical ids both ways. Also run the full `tests/identity/` dir to confirm no regression (gate defaults off, so the suite is unaffected unless a test opts in).

- [ ] **Step 6: ruff + commit**

Commit: `feat(identity): wire batch_fingerprints into resolve_clusters behind GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT`.

---

## Task 3: Bench harness (measure-first) — CREATE, run is an orchestrator step

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_identity_fingerprint.py`
- Create: `.github/workflows/bench-identity-fingerprint.yml`

- [ ] **Step 1: Bench script**

`bench_identity_fingerprint.py`: generate a no-PK-heavy person-shaped frame at `--ns 1000000,5000000` (reuse `tests/generate_synthetic.py` / `_person_df` helpers); time `resolve_clusters` (or just the fingerprint step) with the gate ON vs OFF; report wall + peak RSS per N, plus the no-PK fraction. Print a markdown table to `$GITHUB_STEP_SUMMARY`. ASCII only.

- [ ] **Step 2: Workflow**

`bench-identity-fingerprint.yml`: `workflow_dispatch`, `runs-on: large-new-64GB`, build native (`scripts/build_native.py`), `uv sync --all-packages`, run the bench, upload the JSON artifact. Model it on `.github/workflows/bench-fs-stages.yml`.

- [ ] **Step 3: Commit**

Commit: `bench(identity): measure-first fingerprint batch vs per-row harness`.

- [ ] **Step 4 (orchestrator, NOT a subagent step): dispatch the bench, decide default-on**

The controller dispatches `bench-identity-fingerprint` at 1M/5M, reads wall + RSS, and decides whether to flip the gate default-on (a one-line change + a note in the spec). Per "parity is enough / measure-first": ship default-on only if it wins meaningfully; else leave gated and record the finding. Surface the numbers + the flip decision to the user.

---

## Final validation (orchestrator step)

1. Run `tests/identity/` (gate off, default) — no regression; then `GOLDENMATCH_IDENTITY_BATCH_FINGERPRINT=1 pytest tests/identity/` — all green.
2. Open the PR; CI runs the goldenmatch lane (the identity tests are in it). The bench is `workflow_dispatch` only.
3. Dispatch the bench; fold numbers into the spec; decide the gate default.

## Notes for the implementer

- **The durability invariant is non-negotiable:** the parity tests gate byte-identical hashes. If a dtype can't be made parity-correct columnarly, ROUTE IT TO PER-ROW FALLBACK — never ship a "close enough" hash. Entity ids must not move.
- **DRY/YAGNI:** do NOT touch build_clusters (Sub-project B), the dedup path, the native kernels (they exist), or the canonical-fingerprint spec. Only the identity fingerprint wiring.
- **Column-level vs row-level fallback** is the subtle bit: a bytes/Duration/tz column forces the WHOLE frame to per-row (the column is in every row's hash, no parity cast); non-finite-float / UInt64-overflow are per-row masks. Get this from the spec's routing table.
- **Skill:** follow @superpowers:test-driven-development per task (failing parity test first).
