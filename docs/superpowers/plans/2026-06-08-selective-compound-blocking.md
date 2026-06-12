# Selective Compound Blocking (Probabilistic Path) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the historical_50k candidate flood (8.84M pairs, 3.4% precision ceiling) by replacing `_build_probabilistic_blocking`'s broad single-key union with a candidate-budget-driven, coverage-greedy selection of selective compound passes -- so the now-correct per-rule F-S scoring can clear the gate (historical_50k F1 >> 0.655, P>=~0.8 @ R>=~0.7) without regressing febrl3/synthetic.

**Architecture:** Add an optional per-field transform mechanism to `BlockingKeyConfig` (so a compound can soundex the name AND year-coarsen the date). Generate a POOL of candidate passes (transform-rich name passes + per-field-transformed compounds + self-selective orthogonals), estimate each pass's exact candidate count + (capped) record-pair coverage, then greedily select a union maximizing marginal coverage-per-candidate under a `K*N` budget, always keeping a name-bearing recall anchor. The emitted `BlockingConfig(strategy="multi_pass", ...)` is the exact shape the pipeline + per-rule EM already consume.

**Tech Stack:** Python 3.11+, Polars, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-selective-compound-blocking-design.md`

---

## Conventions for the implementing engineer

- Run tests (Windows PowerShell): `$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest <path> -v`. **Do NOT run the full suite** (OOMs this box). Run only the touched files. If python hangs > 2 min, kill zombies: `powershell.exe -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"`, then retry.
- Branch `feat/probabilistic-splink-parity` (continue on it; do NOT branch, do NOT push). Commit code+tests only; NEVER `git add docs/`. ASCII-only commit messages.
- This builds on already-shipped work on this branch (sigmoid, TF, union-aware EM exclusion, multi-pass blocking, per-rule EM). Do not regress them.

## Background: current shape (READ before starting)

- `core/blocker.py::_build_block_key_expr(key_config)` (~line 99): loops over `key_config.fields`, applies the SAME `key_config.transforms` list to EVERY field via `_try_native_chain` (native) or `map_elements(apply_transforms(...))` (python e.g. soundex), then `pl.concat_str(..., separator="||")`. There is NO per-field transform support today -- Task 1 adds it.
- `config/schemas.py::BlockingKeyConfig` (~line 343): `fields: list[str]`, `transforms: list[str] = Field(default_factory=list)`, with a `@model_validator(mode="after")` requiring non-empty `fields`.
- `core/autoconfig.py::_build_probabilistic_blocking(profiles, df, max_extra_passes=5)` (~line 2804): AUGMENTS `build_blocking(profiles, df)`'s transform-rich name passes with orthogonal single-key passes (high-card) or `[orthogonal, anchor]` compounds (moderate-card), capped at `max_extra_passes`. Returns `BlockingConfig(strategy="multi_pass", passes=...)`. Its inline `_orthogonal(p)` test: `p.name not in covered_fields and col_type != "description" and col_type != "numeric" and null_rate <= 0.20 and 0.02 <= cardinality_ratio < 1.0`. This is the function Task 6 rewrites.
- `ColumnProfile` has `.name`, `.col_type` (date columns are `"date"`), `.cardinality_ratio`. `compute_column_priors(df)` (from `core/indicators`) gives per-column `identity_score`.
- The probabilistic pipeline branches consume the emitted config via `build_blocks(combined_lf, config.blocking)` (scoring) and `_build_blocks_per_pass(combined_lf, config.blocking)` (per-rule EM).

## File Structure

- **Modify** `config/schemas.py`: add `BlockingKeyConfig.field_transforms: list[list[str]] | None = None` + a length validator.
- **Modify** `core/blocker.py`: `_build_block_key_expr` uses per-field transforms when `field_transforms` is set (default None => current behavior, byte-identical).
- **Modify** `core/autoconfig.py`: add `_blocking_candidate_budget_k()`, `_candidate_blocking_passes(profiles, df)`, `_estimate_pass_stats(pass_cfg, df)`, `_select_passes_within_budget(pool, budget)`; rewrite `_build_probabilistic_blocking` to wire them together with a degenerate fallback to plain `build_blocking`.
- **New** `tests/test_autoconfig_selective_blocking.py` (autoconfig helpers). Per-field-transform unit test lives there too (or `tests/test_blocker.py`).

---

## Task 1: Per-field transforms in BlockingKeyConfig (schema + blocker)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (`BlockingKeyConfig`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/blocker.py` (`_build_block_key_expr`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing tests**

```python
import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingKeyConfig
from goldenmatch.core.blocker import _build_block_key_expr

def _key(df, cfg):
    return df.lazy().with_columns(_build_block_key_expr(cfg)).collect()["__block_key__"].to_list()

def test_field_transforms_per_field():
    df = pl.DataFrame({"surname": ["SMITH", "Jones"], "dob": ["1990-05-01", "1985-12-30"]})
    cfg = BlockingKeyConfig(
        fields=["surname", "dob"],
        field_transforms=[["lowercase"], ["substring:0:4"]],
    )
    # surname lowercased, dob year-truncated, concatenated with ||
    assert _key(df, cfg) == ["smith||1990", "jones||1985"]

def test_field_transforms_none_matches_shared_transforms():
    df = pl.DataFrame({"surname": ["SMITH", "Jones"], "dob": ["1990-05-01", "1985-12-30"]})
    shared = BlockingKeyConfig(fields=["surname", "dob"], transforms=["lowercase"])
    # field_transforms is None -> shared transforms apply to every field (today's behavior)
    assert _key(df, shared) == ["smith||1990-05-01", "jones||1985-12-30"]

def test_field_transforms_length_must_match_fields():
    with pytest.raises(ValueError):
        BlockingKeyConfig(fields=["surname", "dob"], field_transforms=[["lowercase"]])
```

- [ ] **Step 2: Run, verify fail** (`TypeError: unexpected keyword 'field_transforms'` / no validation).
Run: `$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py -v`

- [ ] **Step 3: Implement.**
In `schemas.py`, add the field + validator to `BlockingKeyConfig` (the model already imports `Field`, `model_validator`):
```python
class BlockingKeyConfig(BaseModel):
    fields: list[str]
    transforms: list[str] = Field(default_factory=list)
    # Optional per-field transform chains, aligned 1:1 with `fields`. When set,
    # field_transforms[i] applies to fields[i] (overriding `transforms`). Default
    # None preserves the shared-`transforms` behavior. Lets a compound key soundex
    # the name component AND year-coarsen the date component in one pass.
    field_transforms: list[list[str]] | None = None

    @model_validator(mode="after")
    def _validate_fields_nonempty(self) -> BlockingKeyConfig:
        if not self.fields:
            raise ValueError("Blocking key must have at least one field.")
        if self.field_transforms is not None and len(self.field_transforms) != len(self.fields):
            raise ValueError(
                "field_transforms must align 1:1 with fields "
                f"({len(self.field_transforms)} != {len(self.fields)})."
            )
        return self
```
In `blocker.py::_build_block_key_expr`, select the per-field transform list inside the loop:
```python
    field_exprs: list[pl.Expr] = []
    for i, field_name in enumerate(key_config.fields):
        if key_config.field_transforms is not None:
            transforms = key_config.field_transforms[i] or []
        else:
            transforms = key_config.transforms or []
        native = _try_native_chain(field_name, transforms) if transforms else None
        if native is not None:
            field_exprs.append(native)
        elif transforms:
            field_exprs.append(
                pl.col(field_name).map_elements(
                    lambda val, transforms=transforms: apply_transforms(val, transforms),
                    return_dtype=pl.Utf8,
                )
            )
        else:
            field_exprs.append(pl.col(field_name).cast(pl.Utf8))
    ...
```
(The rest of the function -- single-field alias vs `concat_str` -- is unchanged.)

- [ ] **Step 4: Run the new test + the blocker suite -- all green.**
`$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py packages/python/goldenmatch/tests/test_blocker.py packages/python/goldenmatch/tests/test_config.py -q`
Expected: PASS (default-None path is byte-identical; existing blocker/config tests unaffected).

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/goldenmatch/core/blocker.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "feat(blocker): optional per-field transforms in BlockingKeyConfig (default-None byte-identical)"
```

---

## Task 1b: Honor `field_transforms` in the other two block-key builders

**Why:** `_build_block_key_expr` is not the only place that turns a `BlockingKeyConfig`
into a block key. Two other consumers read `key_config.transforms` directly and would
SILENTLY ignore `field_transforms` (producing wrong block keys once Task 6 emits
compounds under those paths). Fix both now so the mechanism is complete before any
auto-config emits it. (Latent today: default-None is byte-identical, so nothing breaks
until emission.)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/chunked.py` (`_block_key_column`, ~line 223)
- Modify: `packages/python/goldenmatch/goldenmatch/db/blocking.py` (`build_blocking_query`, ~line 31-45)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from goldenmatch.db.blocking import build_blocking_query

def test_db_blocking_query_honors_field_transforms():
    cfg = BlockingConfig(keys=[BlockingKeyConfig(
        fields=["surname", "dob"],
        field_transforms=[["soundex"], ["substring:0:4"]],
    )])
    sql = build_blocking_query("people", {"surname": "Smith", "dob": "1990-05-01"}, cfg)
    # surname uses soundex(), dob uses substring(col,1,4); NOT lower() on dob
    assert "soundex(" in sql.lower()
    assert "substring(" in sql.lower()

def test_chunked_block_key_honors_field_transforms():
    # the chunked inline builder must match _build_block_key_expr for a field_transforms config
    import polars as pl
    from goldenmatch.config.schemas import BlockingKeyConfig
    from goldenmatch.core.blocker import _build_block_key_expr
    df = pl.DataFrame({"surname": ["SMITH", "Jones"], "dob": ["1990-05-01", "1985-12-30"]})
    cfg = BlockingKeyConfig(fields=["surname", "dob"], field_transforms=[["lowercase"], ["substring:0:4"]])
    expected = df.lazy().with_columns(_build_block_key_expr(cfg)).collect()["__block_key__"].to_list()
    from goldenmatch.core.chunked import <ChunkedClass>   # implementer: find the class owning _block_key_column
    inst = <construct minimally>
    got = inst._block_key_column(df, cfg)["__block_key__"].to_list()
    assert got == expected == ["smith||1990", "jones||1985"]
```
(Implementer: locate the class that owns `_block_key_column` (grep `def _block_key_column`), and construct a minimal instance; if construction is heavy, instead assert the delegation by calling the method on a minimally-built instance, or refactor `_block_key_column` to a thin wrapper that's trivially testable. The DB test needs NO database -- `build_blocking_query` is pure string generation.)

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.**
(a) `chunked.py::_block_key_column` -- DELEGATE to the shared builder (DRY; also gains the native fast path it currently lacks, and field_transforms support for free):
```python
    def _block_key_column(self, df: pl.DataFrame, key_config: Any) -> pl.DataFrame:
        """Add a ``__block_key__`` column to a DataFrame by delegating to
        ``blocker._build_block_key_expr`` (single source of truth: native fast path,
        shared AND per-field transforms)."""
        from goldenmatch.core.blocker import _build_block_key_expr
        return df.with_columns(_build_block_key_expr(key_config))
```
(Remove the inline `apply_transforms` duplication + the stale "Mirrors ... kept inline" docstring.)
(b) `db/blocking.py::build_blocking_query` -- select transforms per field:
```python
        for i, field_name in enumerate(key_config.fields):
            value = record.get(field_name)
            if value is None:
                continue
            col_expr = _quote_ident(field_name)
            val_expr = _escape_value(str(value))
            if key_config.field_transforms is not None:
                field_xf = key_config.field_transforms[i]
            else:
                field_xf = key_config.transforms
            for transform in field_xf:
                col_expr, val_expr = _apply_sql_transform(col_expr, val_expr, transform)
            field_conditions.append(f"{col_expr} = {val_expr}")
```

- [ ] **Step 4: Run the tests + chunked/db regression -- all green.**
`$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py packages/python/goldenmatch/tests/test_chunked.py -q` (run `test_db.py` only if Postgres-free; `build_blocking_query` tests need no DB). ruff clean on the two changed files.

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/chunked.py packages/python/goldenmatch/goldenmatch/db/blocking.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "fix(blocker): honor field_transforms in chunked + db/blocking key builders (DRY chunked via _build_block_key_expr)"
```

---

## Task 2: Candidate budget constant + env read

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing test** (append)

```python
import os
from goldenmatch.core.autoconfig import _blocking_candidate_budget_k

def test_blocking_candidate_budget_k_default_and_override():
    assert _blocking_candidate_budget_k() == 25
    for raw, expected in [("10", 10), ("100", 100), ("0", 25), ("-5", 25), ("junk", 25)]:
        os.environ["GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K"] = raw
        try:
            assert _blocking_candidate_budget_k() == expected
        finally:
            os.environ.pop("GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K", None)
```

- [ ] **Step 2: Run, verify fail** (ImportError). Same pytest command on the test file.

- [ ] **Step 3: Implement.** Add near the other env helpers in `autoconfig.py` (`os` is already imported):
```python
_BLOCKING_CANDIDATE_BUDGET_K_DEFAULT = 25

def _blocking_candidate_budget_k() -> int:
    """Candidate-pair budget per record for selective probabilistic blocking
    (budget = K * N deduped-union candidate pairs). Override via
    GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K (the dump PR-curve sweep calibrates it).
    Invalid / non-positive values fall back to the default."""
    raw = os.environ.get("GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K")
    if raw is None:
        return _BLOCKING_CANDIDATE_BUDGET_K_DEFAULT
    try:
        v = int(raw.strip())
    except (ValueError, AttributeError):
        return _BLOCKING_CANDIDATE_BUDGET_K_DEFAULT
    return v if v > 0 else _BLOCKING_CANDIDATE_BUDGET_K_DEFAULT
```

- [ ] **Step 4: Run the test -- green.**

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "feat(autoconfig): selective-blocking candidate budget constant + env override"
```

---

## Task 3: `_candidate_blocking_passes` (build the candidate pool)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing test** (append). Uses real `profile_columns` + a person-shaped df so the pool reflects production profiling.

```python
import polars as pl
from goldenmatch.core.autoconfig import _candidate_blocking_passes, profile_columns

def _person_df():
    return pl.DataFrame({
        "first_name": ["ann", "ann", "bob", "bob", "cara", "dan", "eve", "fay"],
        "surname":    ["lee", "lee", "kim", "kim", "ng", "ono", "poe", "qua"],
        "dob":        ["1990-01-02", "1990-01-02", "1985-03-04", "1985-03-04",
                       "1972-07-08", "1965-09-10", "1959-11-12", "1944-02-14"],
        "postcode":   ["AA1", "AA1", "BB2", "BB2", "CC3", "DD4", "EE5", "FF6"],
    })

def test_candidate_pool_has_name_passes_and_compounds():
    df = _person_df()
    profiles = profile_columns(df)
    pool = _candidate_blocking_passes(profiles, df)
    field_sets = [tuple(p.fields) for p in pool]
    # at least one single-field name pass present (the recall floor candidate)
    assert any(len(fs) == 1 for fs in field_sets)
    # at least one COMPOUND pass (name + orthogonal) present
    compounds = [p for p in pool if len(p.fields) == 2]
    assert compounds, "expected compound (name x orthogonal) passes in the pool"
    # a compound that includes the date column carries per-field transforms with a
    # year-coarsen (substring:0:4) on the date component, NOT on the name component
    date_compounds = [p for p in compounds if "dob" in p.fields]
    assert date_compounds
    dc = date_compounds[0]
    assert dc.field_transforms is not None and len(dc.field_transforms) == 2
    dob_i = dc.fields.index("dob")
    assert any("substring:0:4" in t for t in dc.field_transforms[dob_i])
    # the non-date component's transforms are NOT the year-coarsen
    other_i = 1 - dob_i
    assert all("substring:0:4" not in t for t in dc.field_transforms[other_i])
```

- [ ] **Step 2: Run, verify fail** (ImportError).

- [ ] **Step 3: Implement.** Add to `autoconfig.py`. Reuse `build_blocking`, `compute_column_priors`, and the existing orthogonal-eligibility shape:
```python
def _candidate_blocking_passes(profiles, df):
    """Build the POOL of candidate blocking passes for selective probabilistic
    blocking: build_blocking's transform-rich name passes (recall floor) + per-field
    transformed compounds (name x orthogonal, date coarsened to year) + self-selective
    orthogonal single-keys. Returns list[BlockingKeyConfig] (not yet budget-selected)."""
    base = build_blocking(profiles, df)
    base_passes = list(base.passes) if base.passes else list(base.keys or [])
    name_fields = {f for p in base_passes for f in p.fields}

    def _null_rate(name):
        return df[name].null_count() / df.height if df.height else 1.0

    by_name = {p.name: p for p in profiles}

    def _orthogonal(p):
        return (
            p.name not in name_fields
            and p.col_type not in ("description", "numeric")
            and _null_rate(p.name) <= 0.20
            and 0.02 <= p.cardinality_ratio < 1.0
        )

    orthogonals = [p for p in profiles if _orthogonal(p)]

    pool: list[BlockingKeyConfig] = []
    seen: set[tuple] = set()

    def _add(fields, field_transforms):
        # canonical key incl. transforms so distinct-transform variants are distinct
        ft = tuple(tuple(t) for t in field_transforms) if field_transforms else None
        key = (tuple(fields), ft)
        if key in seen:
            return
        seen.add(key)
        if field_transforms is None:
            pool.append(BlockingKeyConfig(fields=list(fields)))
        else:
            pool.append(BlockingKeyConfig(fields=list(fields), field_transforms=field_transforms))

    # 1. Name / recall-floor passes verbatim (keep their shared transforms).
    for p in base_passes:
        # express the base pass as per-field transforms so it composes uniformly
        ft = [list(p.transforms or []) for _ in p.fields]
        _add(list(p.fields), ft if any(ft) else None)

    def _coarsen(o_profile):
        # date orthogonals -> year via substring:0:4 (ISO leading-year; GoldenFlow
        # date_iso8601 runs before autoconfig). Other orthogonals: no transform.
        return ["substring:0:4"] if o_profile.col_type == "date" else []

    # 2. Compounds: each base name pass's PRIMARY field x each orthogonal.
    for p in base_passes:
        if not p.fields:
            continue
        name_field = p.fields[0]
        name_xf = list(p.transforms or [])
        for o in orthogonals:
            if o.name == name_field:
                continue
            _add([name_field, o.name], [name_xf, _coarsen(o)])

    # 3. Self-selective orthogonal single-keys (high cardinality only).
    for o in orthogonals:
        if o.cardinality_ratio >= 0.30:
            _add([o.name], [_coarsen(o)] if _coarsen(o) else None)

    return pool
```
NOTE: `_add`'s `field_transforms=None` branch builds a bare key; the name-pass branch passes per-field transforms (so the single recall pass and its compound share the same name transform). The dedup `seen` keys on (fields, transforms) so a name pass and its bare orthogonal single-key don't collide.

- [ ] **Step 4: Run the test -- green.** Also run `test_autoconfig_probabilistic_entry.py` to confirm `build_blocking`/`profile_columns` integration is intact.

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "feat(autoconfig): _candidate_blocking_passes -- name + per-field-transformed compound + orthogonal pool"
```

---

## Task 4: `_estimate_pass_stats` (exact candidate count + capped coverage)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing test** (append). Hand-computable on a tiny df.

```python
from goldenmatch.config.schemas import BlockingKeyConfig
from goldenmatch.core.autoconfig import _estimate_pass_stats

def test_estimate_pass_stats_exact_count_and_coverage():
    # surname blocks: {smith: rows 0,1,2} (3 -> C(3,2)=3 pairs), {jones: rows 3,4} (1 pair)
    df = pl.DataFrame({
        "surname": ["smith", "smith", "smith", "jones", "jones", "lee"],
        "dob":     ["x", "x", "x", "y", "y", "z"],
    })
    cfg = BlockingKeyConfig(fields=["surname"])
    count, coverage = _estimate_pass_stats(cfg, df)
    assert count == 3 + 1  # 4 candidate pairs, lee is a singleton (0)
    # coverage pair-ids canonical (min*N+max), N=6: smith pairs (0,1),(0,2),(1,2) + jones (3,4)
    N = 6
    expected = {0*N+1, 0*N+2, 1*N+2, 3*N+4}
    assert coverage == expected
```

- [ ] **Step 2: Run, verify fail** (ImportError).

- [ ] **Step 3: Implement.** Add to `autoconfig.py`:
```python
# Per-block row-id cap for the coverage signal: pathologically large blocks are
# subsampled (deterministic, sorted-first-K) so coverage enumeration stays bounded.
# candidate_count stays EXACT (computed from full block sizes), only the coverage
# pair-set is capped. 150 -> at most C(150,2)=11175 pairs per oversized block.
_COVERAGE_BLOCK_CAP = 150

def _estimate_pass_stats(pass_cfg, df):
    """Return (candidate_count, coverage) for a candidate pass.

    candidate_count: EXACT sum over blocks of C(size, 2) (the budget is enforced on
    this). coverage: set of canonical record-pair ids (min*N + max) the pass blocks
    together, with per-block row-ids capped at _COVERAGE_BLOCK_CAP for boundedness.
    Pair-ids are global (independent of the pass) so set ops across passes compose."""
    from goldenmatch.core.blocker import _build_block_key_expr

    n = df.height
    if n < 2:
        return 0, set()
    key_expr = _build_block_key_expr(pass_cfg)
    grouped = (
        df.lazy()
        .with_row_index("__rid__")
        .with_columns(key_expr)
        .filter(
            pl.col("__block_key__").is_not_null()
            & (pl.col("__block_key__").str.strip_chars() != "")
        )
        .group_by("__block_key__")
        .agg(pl.col("__rid__"))
        .collect()
    )
    candidate_count = 0
    coverage: set[int] = set()
    for rids in grouped["__rid__"].to_list():
        size = len(rids)
        if size < 2:
            continue
        candidate_count += size * (size - 1) // 2
        sample = sorted(rids)[:_COVERAGE_BLOCK_CAP]
        for i in range(len(sample)):
            a = sample[i]
            for j in range(i + 1, len(sample)):
                b = sample[j]
                lo, hi = (a, b) if a < b else (b, a)
                coverage.add(lo * n + hi)
    return candidate_count, coverage
```

- [ ] **Step 4: Run the test -- green.**

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "feat(autoconfig): _estimate_pass_stats -- exact candidate count + capped pair coverage"
```

---

## Task 5: `_select_passes_within_budget` (coverage-greedy set-cover)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing tests** (append). Synthetic stats so the greedy/budget/anchor behavior is asserted without real data.

```python
from goldenmatch.core.autoconfig import _select_passes_within_budget

def _mkpass(fields):
    return BlockingKeyConfig(fields=list(fields))

def test_select_respects_budget_and_is_coverage_greedy():
    # pool entries: (pass, candidate_count, coverage_set)
    name = _mkpass(["surname"])                 # broad, expensive, covers {1,2,3,4}
    cA   = _mkpass(["surname", "dob"])           # tight, covers {1,2}
    cB   = _mkpass(["surname", "postcode"])      # tight, covers {3,4}
    cDup = _mkpass(["surname", "city"])          # tight but REDUNDANT, covers {1,2}
    pool = [
        (name, 100, {1, 2, 3, 4}),
        (cA, 10, {1, 2}),
        (cB, 10, {3, 4}),
        (cDup, 10, {1, 2}),
    ]
    selected = _select_passes_within_budget(pool, budget=25)
    fsets = {tuple(p.fields) for p in selected}
    # within budget 25: the broad name (100) does NOT fit; cA + cB (20) cover everything,
    # cDup adds zero NEW coverage so it is NOT chosen over the complementary cB.
    assert ("surname", "dob") in fsets and ("surname", "postcode") in fsets
    assert ("surname",) not in fsets          # too expensive for the budget
    assert ("surname", "city") not in fsets   # redundant, no marginal coverage
    assert sum({"surname": 100, ("surname","dob"): 10, ("surname","postcode"): 10,
                ("surname","city"): 10}[tuple(p.fields) if len(p.fields)>1 else p.fields[0]]
               for p in selected) <= 25

def test_select_always_emits_a_name_bearing_pass():
    # budget too tight for even the cheapest name-bearing pass -> anchor override
    name = _mkpass(["surname"])
    pool = [(name, 1000, {1, 2, 3})]
    selected = _select_passes_within_budget(pool, budget=10)
    assert selected, "must never return an empty config"
    assert any("surname" in p.fields for p in selected)
```

- [ ] **Step 2: Run, verify fail** (ImportError).

- [ ] **Step 3: Implement.** Add to `autoconfig.py`. A pass is "name-bearing" if any of its fields is in the name-field set; to keep this helper self-contained, treat a pass as name-bearing when the caller marks it -- simplest is: the caller passes the name-field set. Use this signature:
```python
def _select_passes_within_budget(pool, budget, name_fields=None):
    """Greedy coverage-per-candidate set-cover under a candidate budget.

    pool: list of (BlockingKeyConfig, candidate_count, coverage_set).
    budget: max total candidate_count (K * N) of the selected union.
    name_fields: set of field names considered recall-bearing; the result is
    guaranteed to include >= 1 name-bearing pass (recall anchor), overriding the
    budget if necessary (a name-less probabilistic config is never acceptable).

    Returns list[BlockingKeyConfig]. Stops when no remaining pass fits the budget or
    the best marginal coverage is zero (saturated)."""
    name_fields = name_fields or set()
    remaining = list(pool)
    covered: set[int] = set()
    spent = 0
    selected: list[BlockingKeyConfig] = []

    def _is_name(cfg):
        return any(f in name_fields for f in cfg.fields)

    while remaining:
        best = None  # (ratio, idx)
        for idx, (cfg, count, cov) in enumerate(remaining):
            if count <= 0 or spent + count > budget:
                continue
            new = len(cov - covered)
            if new <= 0:
                continue
            ratio = new / count
            if best is None or ratio > best[0]:
                best = (ratio, idx)
        if best is None:
            break
        cfg, count, cov = remaining.pop(best[1])
        selected.append(cfg)
        covered |= cov
        spent += count

    # Recall anchor: guarantee >= 1 name-bearing pass, overriding the budget if no
    # name-bearing pass fit. Pick the most-covering name-bearing pass from the pool.
    if name_fields and not any(_is_name(c) for c in selected):
        name_candidates = [(cfg, cov) for (cfg, count, cov) in pool if _is_name(cfg)]
        if name_candidates:
            anchor = max(name_candidates, key=lambda t: len(t[1]))[0]
            selected.append(anchor)

    return selected
```
NOTE the Task-5 tests call `_select_passes_within_budget(pool, budget=...)` WITHOUT `name_fields`; in those tests every pass contains `"surname"`, and the first test asserts the budget path while the second asserts the anchor. For the second test to exercise the anchor, call it with `name_fields={"surname"}`. **Update the second test to pass `name_fields={"surname"}`** (the real caller in Task 6 always supplies `name_fields`):
```python
    selected = _select_passes_within_budget(pool, budget=10, name_fields={"surname"})
```
(Adjust the test in Step 1 accordingly before implementing -- the first test does not need `name_fields` because it asserts budget/greedy behavior where passes already fit.)

- [ ] **Step 4: Run the tests -- green.**

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "feat(autoconfig): _select_passes_within_budget -- coverage-greedy set-cover w/ recall anchor"
```

---

## Task 6: Rewrite `_build_probabilistic_blocking` to wire it together

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (`_build_probabilistic_blocking`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py`

- [ ] **Step 1: Write the failing tests** (append).

```python
from goldenmatch.core.autoconfig import _build_probabilistic_blocking, profile_columns

def test_build_probabilistic_blocking_within_budget_and_name_bearing():
    df = _person_df()  # from Task 3
    profiles = profile_columns(df)
    cfg = _build_probabilistic_blocking(profiles, df)
    assert cfg.strategy == "multi_pass"
    assert cfg.passes, "must emit passes"
    # at least one name-bearing pass (recall anchor)
    name_fields = {"first_name", "surname"}
    assert any(any(f in name_fields for f in p.fields) for p in cfg.passes)
    # total candidate count of the selected union is within K*N (+ allow the anchor
    # override, so assert it is not the full broad union: fewer candidates than a
    # single broad [surname] pass would give if that pass is excluded)
    # (smoke: it returns a usable multi_pass config consumed downstream)

def test_build_probabilistic_blocking_degenerate_fallback():
    # df with ONLY name columns -> no orthogonal fields -> fall back to build_blocking
    from goldenmatch.core.autoconfig import build_blocking
    df = pl.DataFrame({
        "first_name": ["ann", "ann", "bob", "bob"],
        "surname":    ["lee", "lee", "kim", "kim"],
    })
    profiles = profile_columns(df)
    cfg = _build_probabilistic_blocking(profiles, df)
    base = build_blocking(profiles, df)
    # fallback returns build_blocking's output unchanged (deliberate change from the
    # old augmented-superset degenerate path -- see spec)
    assert (cfg.passes or cfg.keys) == (base.passes or base.keys)
```

- [ ] **Step 2: Run, verify fail** (the rewrite isn't in place; old function returns the augmented union).

- [ ] **Step 3: Implement.** Replace the body of `_build_probabilistic_blocking` (keep the signature `(profiles, df, max_extra_passes=5)`; `max_extra_passes` is now unused but kept for call-site compatibility -- callers pass it positionally? verify and drop if safe):
```python
def _build_probabilistic_blocking(profiles, df, max_extra_passes=5):
    """Selective compound blocking for the probabilistic path: pick a union of passes
    under a K*N candidate budget via coverage-greedy set-cover (compounds preferred),
    keeping a name-bearing recall anchor. Replaces the old broad-single-key augment
    that flooded candidates (8.84M on historical_50k -> 3.4% precision ceiling).
    Degenerate input (no orthogonal fields / profiling failure) -> build_blocking."""
    base = build_blocking(profiles, df)
    base_passes = list(base.passes) if base.passes else list(base.keys or [])
    name_fields = {f for p in base_passes for f in p.fields}

    try:
        pool = _candidate_blocking_passes(profiles, df)
    except Exception:
        logger.warning("selective blocking pool build failed; falling back to build_blocking", exc_info=True)
        return base

    # Degenerate: pool is just single-field name passes (no compounds/orthogonals)
    # -> fall back to build_blocking. NOTE: if build_blocking itself emitted a
    # multi-field compound name pass (its geo/compound paths), len(fields)>1 makes
    # this True and we proceed -- that base compound IS selective, so that is correct.
    has_compound_or_orthogonal = any(
        len(p.fields) > 1 or not (set(p.fields) & name_fields) for p in pool
    )
    if not has_compound_or_orthogonal:
        return base

    n = df.height
    budget = _blocking_candidate_budget_k() * max(n, 1)
    stats = []
    for p in pool:
        count, cov = _estimate_pass_stats(p, df)
        if count > 0:
            stats.append((p, count, cov))
    if not stats:
        return base

    selected = _select_passes_within_budget(stats, budget, name_fields=name_fields)
    if not selected:
        return base

    fallback_block = max(1000, min(10_000, n // 200)) if n else 5000
    return BlockingConfig(
        strategy="multi_pass",
        passes=selected,
        max_block_size=base.max_block_size or fallback_block,
        skip_oversized=base.skip_oversized,
    )
```
Verify call sites of `_build_probabilistic_blocking` (grep) still work; if `max_extra_passes` was only ever defaulted, leave the param (harmless) or drop it and update the one caller.

- [ ] **Step 4: Run the new tests + the probabilistic autoconfig + blocker regression -- all green.**
`$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py packages/python/goldenmatch/tests/test_autoconfig_probabilistic_entry.py packages/python/goldenmatch/tests/test_autoconfig_probabilistic_721.py packages/python/goldenmatch/tests/test_blocker.py -q`
Then a quick end-to-end smoke that the emitted config is consumed by the pipeline + per-rule EM:
`...python.exe -m pytest packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py -q`
All must pass.

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_selective_blocking.py
git commit -m "feat(autoconfig): selective compound blocking -- budget set-cover replaces broad single-key flood"
```

---

## Task 7: K calibration + gate measurement (no code; dump PR-curve method)

- [ ] **Step 1** Extend the measurement script `.profile_tmp/diag_pr_per_rule.py` (or a copy) so it loads the config via `auto_configure_probabilistic_df` (which now routes through the new `_build_probabilistic_blocking`) and reports, for historical_50k: total candidate count of the emitted union (confirm it dropped from ~8.84M into K*N), blocking_recall (via `.profile_tmp/diag_blocking_recall.py`), and the per-rule PR-curve BEST F1 (P/R at best).
- [ ] **Step 2** **Sweep K** in {10, 25, 50, 100} via `GOLDENMATCH_BLOCKING_CANDIDATE_BUDGET_K` (no code edits). For each K record: emitted candidate count, blocking_recall, per-rule BEST F1 + P/R. Pick the K that maximizes historical_50k F1 subject to non-regression.
- [ ] **Step 3** **Non-regression:** run the same on febrl3 + synthetic_person at the chosen K; confirm F1 does not drop below the floors (febrl3 ~0.982, synthetic ~0.987). These have low candidate ratios so the budget should not bind -- confirm.
- [ ] **Step 4: DECISION GATE.**
  - If a K exists where historical_50k F1 is materially > 0.655 (target P >= ~0.8 @ R >= ~0.7) AND febrl3/synthetic hold -> the lever is validated. Decide whether to update the shipped `_BLOCKING_CANDIDATE_BUDGET_K_DEFAULT` constant to the winning K (commit that one-line change) or keep 25 and document the winner. Record the before/after PR-curve numbers for the PR body.
  - If NO K clears the gate (cutting candidates craters recall before precision rises, or within-budget candidates are still dominated by same-name non-matches) -> **kill criterion fires**: the wall is F-S same-name-different-person disambiguation, not blocking selectivity. STOP and re-brainstorm scoring-side disambiguation / a learned blocker; do NOT keep chasing K. Record the swept numbers as the evidence.

---

## Final checks before PR

- [ ] Run all touched test files individually (not the full suite).
- [ ] `ruff check` the changed files (`schemas.py`, `blocker.py`, `autoconfig.py`, the test file).
- [ ] Confirm `field_transforms=None` keeps `_build_block_key_expr` byte-identical (Task 1 test) so the weighted/exact path is untouched.
- [ ] Confirm the emitted `BlockingConfig` is consumed unchanged by the pipeline's probabilistic branches + `_build_blocks_per_pass` (per-rule EM smoke green).
- [ ] PR body: the K sweep table + before/after candidate count, blocking_recall, and PR-curve F1 on historical_50k + the febrl3/synthetic non-regression + the gate verdict. Do NOT `git add docs/`.

## Follow-ups (NOT in this plan)

- Large-N (>> 50K) budget estimation (block size scales with N; the exact-groupby estimate is computed at the 50K gate scale -- sample-extrapolation is a separate effort).
- Weighted/exact-path blocking selectivity; TS parity for `field_transforms`; CI Splink head-to-head panel + branch rebase.
- If the kill criterion fires: scoring-side entity disambiguation / learned blocker.
