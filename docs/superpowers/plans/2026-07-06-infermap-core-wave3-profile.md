# InferMap Wave 3 — `profile` Scorer Cutover Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `ProfileScorer`'s scoring math into a pyo3-free `infermap-core::profile_score` kernel + `infermap-native` PyO3 shim, dispatched via `native_module()` with a byte-identical pure-Python fallback, float-parity gated against the reference.

**Architecture:** Scalars-only kernel — the host computes the two average-length floats and abstain check, passes 2 dtype strings + 8 floats to the kernel, which returns the raw pre-clamp score. Reasoning string stays host. Mirrors Wave 1 (`detect_domain`) and Wave 2 (name-scorers) verbatim. The pure-Python `_profile_score_pure` is the parity oracle.

**Tech Stack:** Rust (`infermap-core` pyo3-free crate, `infermap-native` abi3/maturin), Python 3 (`infermap.scorers.profile`), pytest parity fixtures, `dorny/paths-filter` CI gate.

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-core-wave3-profile-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Conventions

**Repo:** `D:\show_case\gg-local-llm` — branch `feat/infermap-core-wave3-profile` (already checked out off fresh `origin/main`, spec committed).

**Box-runnable (Python pure path only):**
```bash
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/infermap" POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0
```
Run pytest via `"$INTERP" -m pytest ...`. Run `"$INTERP" -m ruff check <touched .py>` on every touched Python file.

**CI-only (box CANNOT do these — do NOT attempt `cargo build`):**
- Rust compile / clippy / `cargo test` of `infermap-core` + `infermap-native`.
- The wheel-built native parity test (`test_profile_parity` under `INFERMAP_NATIVE=1`) — runs in the advisory `infermap_native` CI lane.

**Git:** benzsevern gh account (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue repo — `gh pr merge --auto --squash` WITHOUT `--delete-branch`. Commit trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

**Ordering note (TDD caveat for the Rust/native tasks):** the native parity test can only actually *run* in CI. On the box, "failing test" for Rust means the symbol doesn't exist yet (import/AttributeError), and "passing" is verified by CI. Write the pure reference + pure tests FIRST (box-runnable, real red→green), then the kernel + shim + native fixtures (CI-verified). Task order below reflects this.

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `packages/python/infermap/infermap/scorers/profile.py` | Host scorer; add `_profile_score_pure` (oracle) + `_profile_score` (dispatch); `ProfileScorer.score` calls dispatch, keeps abstain + avg-len + reasoning | Modify |
| `packages/python/infermap/infermap/_native_loader.py` | Add `profile_score` to `_GATED_ON` + `_COMPONENT_SYMBOLS` | Modify |
| `packages/rust/extensions/infermap-core/src/lib.rs` | Add `pub fn profile_score` + `fn similarity` helper + `#[cfg(test)]` unit tests | Modify |
| `packages/rust/extensions/infermap-native/src/lib.rs` | Add `#[pyfunction] profile_score` shim + register `wrap_pyfunction!(self::profile_score, m)` | Modify |
| `packages/python/infermap/tests/test_native_parity.py` | Add Wave 3 `_PROFILE_CASES` + `test_profile_parity` | Modify |
| `packages/python/infermap/tests/test_scorers_dispatch.py` | Add box-runnable pure/dispatch/abstain tests | Modify |
| `.github/workflows/ci.yml` | Add `profile.py` to the `infermap_native` path filter | Modify |

No new files. `parity/native_symbols/infermap.allow` stays empty (no change).

---

## Task 1: Pure reference + dispatch in `profile.py` (box-runnable TDD)

Refactor the inline math in `ProfileScorer.score` into a scalar-arg `_profile_score_pure` (the parity oracle) and a `_profile_score` dispatcher, WITHOUT changing any public output.

**Files:**
- Modify: `packages/python/infermap/infermap/scorers/profile.py`
- Test: `packages/python/infermap/tests/test_scorers_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append to `test_scorers_dispatch.py`)

```python
# --- Wave 3: profile scorer ---
from infermap.scorers.profile import _profile_score_pure, ProfileScorer  # noqa: E402


def test_profile_pure_identical_profiles_is_one():
    # same dtype, equal null/uniq, equal lens, equal cards -> all 5 terms = 1.0
    s = _profile_score_pure("string", "string", 0.1, 0.1, 0.5, 0.5,
                            100.0, 100.0, 8.0, 8.0)
    assert s == 1.0


def test_profile_pure_dtype_mismatch_drops_point_four():
    # identical except dtype -> 1.0 - 0.4 = 0.6
    s = _profile_score_pure("string", "int", 0.1, 0.1, 0.5, 0.5,
                            100.0, 100.0, 8.0, 8.0)
    assert s == 0.6


def test_profile_scorer_abstains_on_zero_rows():
    src = FieldInfo(name="a", value_count=0)
    tgt = FieldInfo(name="b", value_count=10)
    assert ProfileScorer().score(src, tgt) is None


def test_profile_scorer_reasoning_unchanged():
    src = FieldInfo(name="a", dtype="string", null_rate=0.1, unique_rate=0.5,
                    value_count=100, sample_values=["abcd", "efgh"])
    tgt = FieldInfo(name="b", dtype="string", null_rate=0.1, unique_rate=0.5,
                    value_count=100, sample_values=["abcd", "efgh"])
    r = ProfileScorer().score(src, tgt)
    assert r is not None
    for part in ("dtype=match", "null_sim=", "uniq_sim=", "len_sim=", "card_sim="):
        assert part in r.reasoning
    assert r.reasoning.startswith("Profile comparison: ")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k profile
```
Expected: FAIL — `ImportError: cannot import name '_profile_score_pure'`.

- [ ] **Step 3: Refactor `profile.py`** — add the imports, `_profile_score_pure`, `_profile_score`, and rewire `ProfileScorer.score`.

At the top of the file, after the existing imports, add:
```python
from infermap._native_loader import native_enabled, native_module
```

Keep `_avg_value_length` and `_similarity` exactly as-is. Add, after `_similarity`:
```python
def _profile_score_pure(
    src_dtype: str,
    tgt_dtype: str,
    src_null: float,
    tgt_null: float,
    src_uniq: float,
    tgt_uniq: float,
    src_val_count: float,
    tgt_val_count: float,
    src_avg_len: float,
    tgt_avg_len: float,
) -> float:
    """Byte-parity reference for ``infermap-core::profile_score``.

    Returns the raw (pre-clamp) profile score. The caller owns the abstain
    check (value_count == 0), average-length computation, and reasoning.
    """
    total = 0.0
    total += 0.4 * (1.0 if src_dtype == tgt_dtype else 0.0)
    total += 0.2 * _similarity(src_null, tgt_null)
    total += 0.2 * _similarity(src_uniq, tgt_uniq)
    max_len = max(src_avg_len, tgt_avg_len, 1.0)
    total += 0.1 * (1.0 - abs(src_avg_len - tgt_avg_len) / max_len)
    src_card = src_uniq * src_val_count
    tgt_card = tgt_uniq * tgt_val_count
    max_card = max(src_card, tgt_card, 1.0)
    total += 0.1 * (1.0 - abs(src_card - tgt_card) / max_card)
    return total


def _profile_score(
    src_dtype: str,
    tgt_dtype: str,
    src_null: float,
    tgt_null: float,
    src_uniq: float,
    tgt_uniq: float,
    src_val_count: float,
    tgt_val_count: float,
    src_avg_len: float,
    tgt_avg_len: float,
) -> float:
    if native_enabled("profile_score"):
        return native_module().profile_score(
            src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
            float(src_val_count), float(tgt_val_count), src_avg_len, tgt_avg_len)
    return _profile_score_pure(
        src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
        float(src_val_count), float(tgt_val_count), src_avg_len, tgt_avg_len)
```

Then rewrite the body of `ProfileScorer.score`. Keep the abstain check and the reasoning exactly as they are today; route the *score* through `_profile_score`. Replace the current method body with:
```python
    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        # Abstain if either side has zero rows (stays host — kernel never sees a
        # zero-row side).
        if source.value_count == 0 or target.value_count == 0:
            return None

        # Average-length reduction stays host (avoids marshaling sample lists +
        # the code-point-length parity trap).
        src_len = _avg_value_length(source.sample_values)
        tgt_len = _avg_value_length(target.sample_values)

        total_score = _profile_score(
            source.dtype, target.dtype,
            source.null_rate, target.null_rate,
            source.unique_rate, target.unique_rate,
            source.value_count, target.value_count,
            src_len, tgt_len,
        )

        # Reasoning stays host: recompute the sub-values for the message (idempotent,
        # no scoring muscle) so the string is byte-identical to the pre-cutover output.
        dtype_match = 1.0 if source.dtype == target.dtype else 0.0
        null_sim = _similarity(source.null_rate, target.null_rate)
        uniq_sim = _similarity(source.unique_rate, target.unique_rate)
        max_len = max(src_len, tgt_len, 1.0)
        len_sim = 1.0 - abs(src_len - tgt_len) / max_len
        src_card = source.unique_rate * source.value_count
        tgt_card = target.unique_rate * target.value_count
        max_card = max(src_card, tgt_card, 1.0)
        card_sim = 1.0 - abs(src_card - tgt_card) / max_card
        parts = [
            f"dtype={'match' if dtype_match else 'mismatch'}",
            f"null_sim={null_sim:.2f}",
            f"uniq_sim={uniq_sim:.2f}",
            f"len_sim={len_sim:.2f}",
            f"card_sim={card_sim:.2f}",
        ]
        return ScorerResult(
            score=total_score,
            reasoning="Profile comparison: " + ", ".join(parts),
        )
```

> DRY note: the reasoning block recomputes sub-values that `_profile_score_pure`
> also computes. This is deliberate and matches the Wave 2 `fuzzy_name` pattern
> (kernel returns the score; host recomputes cheap sub-values for the message).
> Do NOT try to return the parts from the kernel — reasoning is explicitly host-owned.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k profile
```
Expected: PASS (4 profile tests). Under `INFERMAP_NATIVE=0`, `_profile_score` takes the pure branch.

- [ ] **Step 5: Regression-check the whole dispatch file + ruff**

Run:
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q
"$INTERP" -m ruff check packages/python/infermap/infermap/scorers/profile.py packages/python/infermap/tests/test_scorers_dispatch.py
```
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add packages/python/infermap/infermap/scorers/profile.py packages/python/infermap/tests/test_scorers_dispatch.py
git commit -m "feat(infermap): profile scorer dispatch + pure reference (Wave 3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

## Task 2: Loader wiring (`_native_loader.py`)

Register `profile_score` so `native_enabled("profile_score")` returns `True` once the kernel symbol is present.

**Files:**
- Modify: `packages/python/infermap/infermap/_native_loader.py`
- Test: `packages/python/infermap/tests/test_scorers_dispatch.py`

- [ ] **Step 1: Write the failing test** (append to `test_scorers_dispatch.py`)

```python
def test_profile_score_registered_in_loader():
    from infermap._native_loader import _COMPONENT_SYMBOLS, _GATED_ON
    assert _COMPONENT_SYMBOLS.get("profile_score") == "profile_score"
    assert "profile_score" in _GATED_ON
```

- [ ] **Step 2: Run to verify it fails**

Run: `"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k registered`
Expected: FAIL (`AssertionError` — key missing).

- [ ] **Step 3: Add `profile_score` to both sets in `_native_loader.py`**

In `_GATED_ON`:
```python
_GATED_ON: frozenset[str] = frozenset(
    {"detect_domain", "exact_score", "fuzzy_name_score", "initialism_score",
     "profile_score"}
)
```

In `_COMPONENT_SYMBOLS` (add the line):
```python
    "initialism_score": "initialism_score",
    "profile_score": "profile_score",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k registered`
Expected: PASS.

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/infermap/infermap/_native_loader.py
git add packages/python/infermap/infermap/_native_loader.py packages/python/infermap/tests/test_scorers_dispatch.py
git commit -m "feat(infermap): gate profile_score in native loader (Wave 3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

## Task 3: The kernel (`infermap-core::profile_score`)

Add the pyo3-free scalar kernel + a Rust unit test mirroring the Python oracle. **Rust is CI-only — do NOT run `cargo` on the box.** Write carefully against the spec; CI compiles + tests.

**Files:**
- Modify: `packages/rust/extensions/infermap-core/src/lib.rs`

- [ ] **Step 1: Add `similarity` helper + `profile_score`** (insert after the `initialism_score` fn, before `#[cfg(test)] mod tests`)

```rust
/// max(0, 1 - |a-b|) -- matches Python `max(0.0, 1.0 - abs(a - b))` arg order.
fn similarity(a: f64, b: f64) -> f64 {
    (1.0 - (a - b).abs()).max(0.0)
}

/// Byte-parity reference: infermap.scorers.profile._profile_score_pure.
/// Returns the raw (pre-clamp) profile score. The caller owns the abstain check
/// (value_count == 0), average-length reduction, and reasoning string.
///
/// Fixed five-add order (no loop / no iter().sum() SIMD-reduction) -> byte-identical
/// to the Python source under IEEE-754.
#[allow(clippy::too_many_arguments)]
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
) -> f64 {
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

> `#[allow(clippy::too_many_arguments)]` is required — the kernel has 10 params and
> `-D warnings` would otherwise fail clippy. This is the deliberate scalars-only
> signature (spec §3), not accidental.

- [ ] **Step 2: Add a Rust unit test** (inside the existing `#[cfg(test)] mod tests`, add a fn)

```rust
    #[test]
    fn profile_identical_and_dtype_mismatch() {
        // identical profiles -> all 5 terms = 1.0
        let s = profile_score("string", "string", 0.1, 0.1, 0.5, 0.5,
                              100.0, 100.0, 8.0, 8.0);
        assert_eq!(s, 1.0);
        // dtype mismatch only -> 1.0 - 0.4 = 0.6
        let s2 = profile_score("string", "int", 0.1, 0.1, 0.5, 0.5,
                               100.0, 100.0, 8.0, 8.0);
        assert_eq!(s2, 0.6);
        // avg_len 0/0 floors denom to 1.0 -> len term stays 1.0 (no div-by-zero)
        let s3 = profile_score("string", "string", 0.0, 0.0, 0.0, 0.0,
                               1.0, 1.0, 0.0, 0.0);
        assert_eq!(s3, 1.0);
    }
```

- [ ] **Step 3: Do NOT run cargo (box can't build).** Re-read the diff against spec §3 — verify the five `+=` order, `.max(...).max(1.0)` floors, `similarity` arg order, and that `src_card = src_uniq * src_val_count` matches the Python `unique_rate * value_count`. CI will compile + run the unit test.

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/infermap-core/src/lib.rs
git commit -m "feat(infermap-core): profile_score kernel (Wave 3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

## Task 4: The native shim (`infermap-native`)

Expose the kernel over PyO3 and register it. **CI-only build.**

**Files:**
- Modify: `packages/rust/extensions/infermap-native/src/lib.rs`

- [ ] **Step 1: Add the `#[pyfunction]`** (after the `initialism_score` shim, before `#[pymodule]`)

```rust
/// Wave 3 profile scorer: scalars-only (host computes avg-lengths + abstain).
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn profile_score(
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
) -> PyResult<f64> {
    Ok(infermap_core::profile_score(
        src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
        src_val_count, tgt_val_count, src_avg_len, tgt_avg_len))
}
```

- [ ] **Step 2: Register in `#[pymodule]`** (add after the `initialism_score` registration line)

```rust
    m.add_function(wrap_pyfunction!(self::profile_score, m)?)?;
```

> `self::` qualification is REQUIRED — `check_native_symbols._WRAP` only scans the
> `wrap_pyfunction!(self::X` form. The bare form would silently red the gate.

- [ ] **Step 3: Do NOT run cargo.** Verify by eye: the `#[pyfunction]` arg list matches the core fn arity/order, return is `PyResult<f64>` with `Ok(...)`, and the registration uses `self::profile_score`. CI compiles.

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/infermap-native/src/lib.rs
git commit -m "feat(infermap-native): profile_score PyO3 shim (Wave 3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

## Task 5: Native parity fixtures (`test_native_parity.py`)

Add the Wave 3 parity section. `test_profile_parity` only runs un-skipped in the `infermap_native` CI lane (needs the built wheel); on the box it skips cleanly via `@native_only`.

**Files:**
- Modify: `packages/python/infermap/tests/test_native_parity.py`

- [ ] **Step 1: Add the Wave 3 fixtures + test** (append at end of file)

```python
# ---------------------------------------------------------------------------
# Wave 3: profile scorer parity (scalars-only kernel)
# ---------------------------------------------------------------------------

from infermap.scorers.profile import _profile_score_pure  # noqa: E402

# 10-tuple: (src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
#            src_val_count, tgt_val_count, src_avg_len, tgt_avg_len)
_PROFILE_CASES = [
    # identical profiles -> 1.0
    ("string", "string", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0),
    # dtype mismatch -> drops 0.4
    ("string", "int", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0),
    # avg_len floor: both 0.0 -> denom floors to 1.0
    ("string", "string", 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0),
    # one empty-sample side -> len_sim = 1 - 8/8 = 0.0
    ("string", "string", 0.0, 0.0, 0.5, 0.5, 100.0, 100.0, 0.0, 8.0),
    # cardinality floor: tiny cards (uniq*count < 1.0)
    ("string", "string", 0.0, 0.0, 0.01, 0.02, 10.0, 10.0, 4.0, 4.0),
    # lopsided null -> similarity clamps to 0.0
    ("string", "string", 0.0, 1.0, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0),
    # lopsided uniqueness
    ("string", "string", 0.1, 0.1, 1.0, 0.0, 100.0, 100.0, 8.0, 8.0),
    # asymmetric lengths
    ("string", "string", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 3.0, 30.0),
    # realistic mixed (non-round rates -> catches float-path rounding divergence)
    ("string", "int", 0.13, 0.87, 0.42, 0.58, 250.0, 90.0, 12.5, 7.25),
]


@native_only
@pytest.mark.parametrize("args", _PROFILE_CASES)
def test_profile_parity(args):
    # exact byte-equality (not approx) -- the whole point of the gate.
    assert native_module().profile_score(*args) == _profile_score_pure(*args)
```

- [ ] **Step 2: Verify it collects + skips cleanly on the box**

Run:
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_native_parity.py -q -k profile
```
Expected: 9 items, all **skipped** (`native_only` — wheel not built on box). No collection errors, no import errors. The `_profile_score_pure` import must resolve.

- [ ] **Step 3: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/infermap/tests/test_native_parity.py
git add packages/python/infermap/tests/test_native_parity.py
git commit -m "test(infermap): profile_score native parity fixtures (Wave 3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

## Task 6: Symbol-gate reconcile + CI path filter

Confirm `check_native_symbols.py` reconciles the new symbol, and add `profile.py` to the CI filter so a profile-only change triggers the native lane.

**Files:**
- Modify: `.github/workflows/ci.yml` (path filter only)
- Verify: `scripts/check_native_symbols.py infermap` (no code change expected)

- [ ] **Step 1: Run the symbol gate on the box**

Run:
```bash
"$INTERP" scripts/check_native_symbols.py infermap
```
Expected: exit 0, `infermap: 5 registered, N referenced`, `native-symbol reconciliation OK`, and `profile_score` NOT in a MISSING list. (It's registered via `wrap_pyfunction!(self::profile_score` in the native crate AND referenced via `native_module().profile_score` in `profile.py`.) If it reports `profile_score` MISSING, the native `wrap_pyfunction!` line or the `profile.py` reference is wrong — fix before proceeding.

- [ ] **Step 2: Add `profile.py` to the `infermap_native` path filter in `ci.yml`**

Locate the `infermap_native:` filter block (the `- 'packages/python/infermap/infermap/scorers/initialism.py'` line, ~line 603). Add immediately after it:
```yaml
              - 'packages/python/infermap/infermap/scorers/profile.py'
```

- [ ] **Step 3: Validate the YAML edit didn't break `ci.yml`**

Run:
```bash
"$INTERP" -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml YAML OK')"
```
Expected: `ci.yml YAML OK`. (A broken `ci.yml` = zero jobs = required gate never reports; see the `feedback_ci_yaml_startup_failure` lesson.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(infermap): trigger native lane on profile.py changes (Wave 3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

## Task 7: Full local regression + push + PR + arm auto-merge

**Files:** none (integration).

- [ ] **Step 1: Run the full box-runnable infermap test surface**

Run:
```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py packages/python/infermap/tests/test_native_parity.py -q
```
Expected: dispatch tests PASS; native-parity tests SKIP (box has no wheel). Zero failures, zero errors.

- [ ] **Step 2: Final ruff on every touched Python file**

Run:
```bash
"$INTERP" -m ruff check packages/python/infermap/infermap/scorers/profile.py packages/python/infermap/infermap/_native_loader.py packages/python/infermap/tests/test_scorers_dispatch.py packages/python/infermap/tests/test_native_parity.py
```
Expected: `All checks passed!`.

- [ ] **Step 3: Push the branch**

```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/infermap-core-wave3-profile
```

- [ ] **Step 4: Open the PR** (title + body; use REST fallback if GraphQL is rate-limited)

```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(infermap): Wave 3 — profile scorer Rust cutover" \
  --body "$(cat <<'EOF'
## What

Wave 3 of the InferMap Rust cutover: moves `ProfileScorer`'s scoring math into a pyo3-free `infermap-core::profile_score` kernel + `infermap-native` PyO3 shim, dispatched via `native_module()` with a byte-identical pure-Python fallback.

Scalars-only kernel (host computes the two avg-length floats + the abstain check; reasoning stays host), mirroring Wave 1 (`detect_domain`) and Wave 2 (name-scorers).

## Parity

`test_profile_parity` asserts exact `==` between the native kernel and `_profile_score_pure` across 9 fixtures (identical/mismatch/floors/lopsided/asymmetric/mixed). Fixed five-add order -> no `iter().sum()` SIMD-reduction hazard; IEEE-754 byte-identical.

## Scope

Pure cutover — same output (score + reasoning), new backend. `alias`/`pattern_type`/`llm` scorers + WASM/TS surface remain deferred.

Spec: `docs/superpowers/specs/2026-07-06-infermap-core-wave3-profile-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```
If GraphQL is rate-limited: `GH_TOKEN=$(gh auth token --user benzsevern)` explicitly, or fall back to `gh api repos/benseverndev-oss/goldenmatch/pulls -f ...`.

- [ ] **Step 5: Arm auto-merge and STOP**

```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
Do NOT poll CI. Do NOT `--delete-branch`. The merge queue lands it on green. Report the PR number and STOP.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Pure score math correct | `_profile_score_pure` unit tests (identical=1.0, mismatch=0.6) | Box (Task 1) |
| Abstain stays host | `ProfileScorer.score` returns None on 0 rows | Box (Task 1) |
| Reasoning unchanged | reasoning-string assertion | Box (Task 1) |
| Loader gates symbol | `_COMPONENT_SYMBOLS`/`_GATED_ON` assertion | Box (Task 2) |
| Kernel math correct | Rust `#[cfg(test)]` unit test | CI (Task 3) |
| Native byte-parity | `test_profile_parity` exact `==` × 9 | CI `infermap_native` lane (Task 5) |
| No silent fallback | `check_native_symbols.py infermap` reconciles `profile_score` | Box (Task 6) |
| CI triggers on profile.py | path-filter entry + YAML valid | Box (Task 6) |
| No regressions | full infermap test surface + ruff | Box (Task 7) |
