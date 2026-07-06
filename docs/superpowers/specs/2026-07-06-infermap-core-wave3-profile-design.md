# InferMap Wave 3 — `profile` scorer cutover (design)

**Date:** 2026-07-06
**Status:** Approved (design)
**Depends on:** Wave 1 (`detect_domain`, #1490), Wave 2 (name-scorers, #1492) — both merged to `main`.
**Branch (planned):** `feat/infermap-core-wave3-profile` off fresh `origin/main`.

## 1. Goal

Cut over `ProfileScorer`'s scoring math into a pyo3-free `infermap-core`
kernel (`profile_score`) plus an `infermap-native` pyo3 shim, dispatched via
`native_module()` with a pure-Python fallback, and float-parity gated
byte-for-byte against the Python reference. This is the fourth InferMap kernel
and completes the "clean" (non-regex, non-dictionary) scorer set.

It follows the Wave 1/2 pattern verbatim:

- **Kernel returns the score only; reasoning stays host.**
- **Pure-Python reference (`_profile_score_pure`) is the byte-parity oracle.**
- **`native_module()` dispatch with a pure fallback; gated under
  `INFERMAP_NATIVE=auto`.**
- **Advisory `infermap_native` CI lane** builds the wheel and runs the parity
  test un-skipped under `INFERMAP_NATIVE=1`.

## 2. Background — what `ProfileScorer` does today

`packages/python/infermap/infermap/scorers/profile.py`. `ProfileScorer.score`
compares two `FieldInfo` statistical profiles and returns a `ScorerResult`
(score + reasoning), or `None` (abstain) when either side has zero rows.

Weighted components (weights sum to 1.0):

| Component | Weight | Formula |
| --- | --- | --- |
| dtype match | 0.4 | `1.0 if src.dtype == tgt.dtype else 0.0` |
| null-rate similarity | 0.2 | `similarity(src.null_rate, tgt.null_rate)` |
| uniqueness similarity | 0.2 | `similarity(src.unique_rate, tgt.unique_rate)` |
| value-length similarity | 0.1 | `1.0 - abs(src_len - tgt_len) / max(src_len, tgt_len, 1.0)` |
| cardinality-ratio similarity | 0.1 | `1.0 - abs(src_card - tgt_card) / max(src_card, tgt_card, 1.0)` |

where:

- `similarity(a, b) = max(0.0, 1.0 - abs(a - b))`
- `src_len = _avg_value_length(src.sample_values)`, likewise `tgt_len`
- `src_card = src.unique_rate * src.value_count`, likewise `tgt_card`
- `_avg_value_length(samples)`: filter out `None` and blank
  (`str(s).strip() == ""`) values; return
  `sum(len(str(s)) for s in clean) / len(clean)`, or `0.0` if none remain.

`ScorerResult.__post_init__` clamps the final score into `[0, 1]` (host-side,
unchanged — the kernel returns the raw pre-clamp score exactly like Wave 2).

## 3. Kernel/host split (decided)

The kernel is **scalars-only** — the host computes the two average-length
floats from `sample_values` and passes them in. Rationale:

- `_avg_value_length` is a list reduction (I/O-ish), not scoring "muscle".
  Computing it host-side mirrors how `FuzzyNameScorer` normalizes names
  host-side for its reasoning string.
- Avoids marshaling two `Vec<String>` across the FFI boundary per call.
- **Sidesteps the code-point-length parity trap.** Python `len(str(s))` counts
  Unicode code points; a Rust port would need `.chars().count()` (not `.len()`,
  which counts UTF-8 bytes) to match — the exact class of hazard Wave 2 had to
  hand-handle in its tokenizer. Keeping the length reduction host-side removes
  that hazard from this wave entirely.

### Kernel signature (`infermap-core`)

```rust
/// Byte-parity reference: infermap.scorers.profile._profile_score_pure.
/// Returns the raw (pre-clamp) profile score. Caller handles abstain
/// (value_count == 0) and reasoning.
pub fn profile_score(
    src_dtype: &str,
    tgt_dtype: &str,
    src_null: f64,
    tgt_null: f64,
    src_uniq: f64,
    tgt_uniq: f64,
    src_val_count: f64,
    tgt_val_count: f64,
    src_avg_len: f64,
    tgt_avg_len: f64,
) -> f64
```

### Kernel body (verbatim translation)

```rust
fn similarity(a: f64, b: f64) -> f64 {
    (1.0 - (a - b).abs()).max(0.0)
}

pub fn profile_score(/* ...args... */) -> f64 {
    let mut total = 0.0_f64;

    // dtype match (0.4)
    let dtype_match = if src_dtype == tgt_dtype { 1.0 } else { 0.0 };
    total += 0.4 * dtype_match;

    // null-rate similarity (0.2)
    total += 0.2 * similarity(src_null, tgt_null);

    // uniqueness similarity (0.2)
    total += 0.2 * similarity(src_uniq, tgt_uniq);

    // value-length similarity (0.1)
    let max_len = src_avg_len.max(tgt_avg_len).max(1.0);
    total += 0.1 * (1.0 - (src_avg_len - tgt_avg_len).abs() / max_len);

    // cardinality-ratio similarity (0.1)
    let src_card = src_uniq * src_val_count;
    let tgt_card = tgt_uniq * tgt_val_count;
    let max_card = src_card.max(tgt_card).max(1.0);
    total += 0.1 * (1.0 - (src_card - tgt_card).abs() / max_card);

    total
}
```

**Float determinism.** Five fixed scalar `+=` adds in a fixed order — no loop,
so there is **no `iter().sum()` SIMD-reduction hazard** (the root cause of the
goldenanalysis `mean` summation-order advisory). The value-length and
cardinality sub-expressions are the same scalar op sequence as the Python
source. IEEE-754 with identical operation order is byte-identical, so parity is
exact, not approximate. `similarity` matches Python's `max(0.0, 1.0 - abs(...))`
argument order via `(1.0 - x.abs()).max(0.0)`.

> Note on `max` ordering: Python's `max(a, b, 1.0)` and Rust's
> `a.max(b).max(1.0)` agree for all finite non-NaN inputs. Inputs here are
> non-negative finite rates/lengths/counts, so NaN handling is not reachable;
> the plan's fixtures include the `max(...,1.0)` floor case to lock this.

## 4. Host dispatch (`profile.py`)

Mirror `fuzzy_name.py`:

- Import `native_enabled, native_module` from `infermap._native_loader`.
- Add `_profile_score(...)` dispatcher:

  ```python
  def _profile_score(src_dtype, tgt_dtype, src_null, tgt_null, src_uniq,
                     tgt_uniq, src_val_count, tgt_val_count, src_avg_len,
                     tgt_avg_len) -> float:
      if native_enabled("profile_score"):
          return native_module().profile_score(
              src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
              float(src_val_count), float(tgt_val_count), src_avg_len,
              tgt_avg_len)
      return _profile_score_pure(...)  # same args
  ```

- `_profile_score_pure(...)` holds the reference math (the current inline body
  of `ProfileScorer.score`, factored to take the same scalar args). This is the
  parity oracle imported by the test.
- `ProfileScorer.score` keeps ownership of:
  - the abstain check (`if source.value_count == 0 or target.value_count == 0:
    return None`) — evaluated **before** the kernel is called, so the kernel
    never sees a zero-row side;
  - `_avg_value_length(source.sample_values)` / `...(target.sample_values)`;
  - building the identical `reasoning` string (`dtype=`, `null_sim=`,
    `uniq_sim=`, `len_sim=`, `card_sim=` parts) — recomputing the sub-values
    host-side for the message, exactly as today.

**Public behavior is byte-identical in both paths** — same `ScorerResult.score`
(post-clamp) and same `reasoning`.

## 5. Native shim (`infermap-native`)

```rust
#[pyfunction]
fn profile_score(
    src_dtype: &str, tgt_dtype: &str,
    src_null: f64, tgt_null: f64,
    src_uniq: f64, tgt_uniq: f64,
    src_val_count: f64, tgt_val_count: f64,
    src_avg_len: f64, tgt_avg_len: f64,
) -> PyResult<f64> {
    Ok(infermap_core::profile_score(
        src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
        src_val_count, tgt_val_count, src_avg_len, tgt_avg_len))
}
```

> Returns `PyResult<f64>` with `Ok(...)` to match every existing shim in
> `infermap-native/src/lib.rs` (pyo3 accepts a bare `f64` too, but mirror the
> house pattern).

Register with `wrap_pyfunction!(self::profile_score, m)?;` (the `self::`
qualifier is required for `check_native_symbols._WRAP` to scan the export).
Return type is a plain `f64`, so there is **no `clippy::type_complexity`** issue
(unlike `detect_domain`'s 5-tuple).

## 6. Loader wiring (`_native_loader.py`)

Add `profile_score` to the gated component-symbol set. `_COMPONENT_SYMBOLS` is
the functional wiring (what `native_enabled` actually consults); `_GATED_ON` is
the sign-off/documentation list — add to both, following the Wave 2 symbols
which appear in each. So `native_enabled("profile_score")` is `True` under
`INFERMAP_NATIVE=auto` once the symbol is present, and cleanly falls back to
pure when the wheel lacks it (the #688 silent-fallback class is exactly what the
`check_native_symbols` gate guards against).

## 7. Parity gate + CI

### 7.1 `tests/test_native_parity.py`

Add a Wave 3 section:

```python
from infermap.scorers.profile import _profile_score_pure  # noqa: E402

_PROFILE_CASES = [ ... ]  # (see fixtures below)

@native_only
@pytest.mark.parametrize("args", _PROFILE_CASES)
def test_profile_parity(args):
    assert native_module().profile_score(*args) == _profile_score_pure(*args)
```

Exact `==` (not `pytest.approx`) — the whole point of the gate is byte-identity.

### 7.2 Fixtures (`_PROFILE_CASES`)

Each case is the 10-tuple `(src_dtype, tgt_dtype, src_null, tgt_null, src_uniq,
tgt_uniq, src_val_count, tgt_val_count, src_avg_len, tgt_avg_len)`. Cover:

1. **Identical profiles** → total `1.0` (same dtype, equal rates/lens/cards).
2. **dtype mismatch** (`"string"` vs `"int"`) → drops the 0.4 term only.
3. **`max(...,1.0)` length floor** — both `avg_len < 1.0` (e.g. `0.0, 0.0`) so
   the denominator floors to `1.0`; and one side `0.0` (empty samples).
4. **`max(...,1.0)` cardinality floor** — tiny cards (`uniq*count < 1.0`).
5. **Lopsided null** (`0.0` vs `1.0`) → `similarity` clamps to `0.0`.
6. **Lopsided uniqueness** (`1.0` vs `0.0`).
7. **Asymmetric lengths** (`3.0` vs `30.0`) — exercises the ratio numerator.
8. **Realistic mixed** — non-round rates (`0.13`, `0.87`) to catch any
   accidental rounding divergence in the float path.

### 7.3 `tests/test_scorers_dispatch.py`

Add box-runnable pure-path assertions (run under `INFERMAP_NATIVE=0`):

- `_profile_score_pure` on the identical-profile case returns `1.0`.
- `ProfileScorer().score(FieldInfo(...), FieldInfo(...))` returns `None` when a
  side has `value_count == 0` (abstain path stays host).
- A populated pair returns a `ScorerResult` whose `reasoning` still contains the
  five `*_sim=` / `dtype=` parts (message unchanged).

### 7.4 `scripts/check_native_symbols.py`

The `infermap` REGISTRY entry already exists (added in Wave 1). No registry
change needed: the gate will see `profile_score` as both a kernel export
(`wrap_pyfunction!(self::profile_score`) and a host reference
(`native_module().profile_score`) and reconcile it. `parity/native_symbols/
infermap.allow` stays empty. Run `python scripts/check_native_symbols.py
infermap` locally to confirm `0 missing`.

### 7.5 CI path filter

The advisory `infermap_native` lane already exists. Its `dorny/paths-filter`
entry gains `packages/python/infermap/infermap/scorers/profile.py` so a
profile-only change triggers the parity build. (`infermap-core/**` and
`infermap-native/**` are already in the filter from Wave 1.)

## 8. Out of scope

- `alias`, `pattern_type`, `llm` scorers — deferred per the Wave 2 roadmap
  (`pattern_type` needs a regex fixture-drift gate; `alias` is dictionary/host;
  `llm` is a host-side external call).
- The WASM/TS `infermap` surface — consciously deferred (Wave 1b-style); there
  is no TS `infermap` package and `infermap-core` has no WASM binding yet.
- Any change to `ProfileScorer`'s weights, formula, reasoning text, or abstain
  semantics. This is a pure cutover: same output, new backend.

## 9. Risk assessment

Lowest of any InferMap wave to date:

- **No list marshaling** (scalars-only kernel).
- **No regex** (`pattern_type`'s hazard, deferred).
- **No summation reduction** (fixed five-add order → no SIMD-vectorization
  divergence like the goldenanalysis `mean` advisory).
- **No code-point-length trap** (length reduction stays host).

The only parity discipline is matching the operation order of the five weighted
adds and the two ratio sub-expressions, which the verbatim translation
guarantees and the `==` fixtures lock.

## 10. Build environment constraints

- **Python pure path is box-runnable:** `_profile_score_pure`, the dispatch
  tests, and `check_native_symbols.py infermap` run locally with
  `PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1
  INFERMAP_NATIVE=0` using `D:/show_case/goldenmatch/.venv/Scripts/python.exe`.
  Run `ruff check` on touched Python.
- **Rust build + native parity are CI-only** (box cannot `cargo build`; the
  wheel-built `test_profile_parity` runs in the `infermap_native` lane under
  `INFERMAP_NATIVE=1`).
- **Merge-queue repo:** `gh pr merge --auto --squash` without
  `--delete-branch`; benzsevern gh account.
