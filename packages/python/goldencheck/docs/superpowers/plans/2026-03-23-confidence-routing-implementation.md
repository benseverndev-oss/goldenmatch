# Confidence-Based LLM Routing Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add confidence scoring to findings, improve profiler accuracy, and route only low-confidence columns to the LLM for cheaper, more surgical enhancement.

**Architecture:** Each profiler assigns a confidence score (0.0-1.0) to its findings. A post-scan confidence module applies corroboration boost and optional INFO downgrade. The LLM sample block builder gains a `focus_columns` filter to send only uncertain columns.

**Tech Stack:** Existing stack — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-23-confidence-routing-design.md`

---

## Task 1: Add `confidence` Field to Finding Model

**Files:**
- Modify: `goldencheck/models/finding.py`
- Modify: `goldencheck/reporters/json_reporter.py`
- Modify: `goldencheck/reporters/rich_console.py`
- Modify: `goldencheck/tui/findings.py`
- Modify: `tests/models/test_finding.py`
- Modify: `tests/reporters/test_reporters.py`

- [ ] **Step 1: Write tests**

Add to `tests/models/test_finding.py`:
```python
def test_finding_default_confidence():
    f = Finding(severity=Severity.INFO, column="x", check="y", message="z")
    assert f.confidence == 1.0

def test_finding_custom_confidence():
    f = Finding(severity=Severity.WARNING, column="x", check="y", message="z", confidence=0.3)
    assert f.confidence == 0.3
```

Add to `tests/reporters/test_reporters.py`:
```python
def test_json_reporter_always_includes_confidence():
    findings = [Finding(severity=Severity.INFO, column="x", check="y", message="ok", confidence=0.7)]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert data["findings"][0]["confidence"] == 0.7

def test_json_reporter_default_confidence():
    findings = [Finding(severity=Severity.INFO, column="x", check="y", message="ok")]
    profile = DatasetProfile(file_path="test.csv", row_count=100, column_count=5, columns=[])
    buf = io.StringIO()
    report_json(findings, profile, buf)
    data = json.loads(buf.getvalue())
    assert data["findings"][0]["confidence"] == 1.0
```

- [ ] **Step 2: Add confidence field to Finding**

In `goldencheck/models/finding.py`, add after `source: str | None = None`:
```python
    confidence: float = 1.0
```

- [ ] **Step 3: Update JSON reporter**

In `goldencheck/reporters/json_reporter.py`, add `"confidence": f.confidence` to the findings dict. This goes OUTSIDE the `if v is not None` filter since confidence should always be included:
```python
        "findings": [
            {
                **{k: v for k, v in {
                    "severity": f.severity.name.lower(),
                    "column": f.column,
                    "check": f.check,
                    "message": f.message,
                    "affected_rows": f.affected_rows,
                    "sample_values": f.sample_values,
                    "source": f.source,
                }.items() if v is not None},
                "confidence": f.confidence,
            }
            for f in findings
        ],
```

- [ ] **Step 4: Update Rich console reporter**

Add confidence indicator to table. In `goldencheck/reporters/rich_console.py`, add a "Conf" column after "Message":
```python
    table.add_column("Conf", width=4)
```
And in the row loop:
```python
    conf = "H" if f.confidence >= 0.8 else "M" if f.confidence >= 0.5 else "[red]L[/red]"
    table.add_row(..., conf)
```

- [ ] **Step 5: Update TUI findings tab**

In `goldencheck/tui/findings.py`, add "Conf" column to the DataTable and show H/M/L per row.

- [ ] **Step 6: Run all tests**

```bash
pytest -v
```

- [ ] **Step 7: Commit**

```bash
git add goldencheck/models/ goldencheck/reporters/ goldencheck/tui/ tests/
git commit -m "feat: add confidence field to Finding model and all reporters"
```

---

## Task 2: Add `context` to BaseProfiler + Type Inference Improvements

**Files:**
- Modify: `goldencheck/profilers/base.py`
- Modify: `goldencheck/profilers/type_inference.py`
- Modify: `tests/profilers/test_type_inference.py`

- [ ] **Step 1: Write tests for minority wrong type**

Add to `tests/profilers/test_type_inference.py`:
```python
def test_minority_numeric_in_text_column():
    # 3 numbers out of 100 strings — should flag as low confidence
    values = [f"Name{i}" for i in range(97)] + ["12345", "99999", "11111"]
    df = pl.DataFrame({"last_name": values})
    findings = TypeInferenceProfiler().profile(df, "last_name")
    assert len(findings) > 0
    assert any(f.confidence < 0.5 for f in findings)

def test_type_inference_writes_context():
    df = pl.DataFrame({"age": ["25", "30", "45", "28", "33"]})
    context = {}
    findings = TypeInferenceProfiler().profile(df, "age", context=context)
    assert context.get("age", {}).get("mostly_numeric") is True

def test_type_inference_existing_behavior_unchanged():
    # >80% numeric still works as before
    df = pl.DataFrame({"age": ["25", "30", "forty-five", "28", "33"]})
    findings = TypeInferenceProfiler().profile(df, "age")
    assert any(f.confidence >= 0.8 for f in findings)
```

- [ ] **Step 2: Update BaseProfiler signature**

In `goldencheck/profilers/base.py`:
```python
class BaseProfiler(ABC):
    @abstractmethod
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        ...
```

- [ ] **Step 3: Update TypeInferenceProfiler**

Add minority detection branch and context writing. Add `confidence` to all findings:
- Existing `>= 0.80` branch: confidence=0.9, write `context[column]["mostly_numeric"] = True`
- New `> 0 and < 0.05` branch: confidence=0.3 (low), INFO severity
- Context writing: `if context is not None: context.setdefault(column, {})["mostly_numeric"] = True`

- [ ] **Step 4: Update all other profilers to accept `context` kwarg**

Each profiler's `profile` method gains `*, context: dict | None = None` but ignores it (for now only type_inference uses it). This is needed to match the updated ABC.

- [ ] **Step 5: Run tests**

```bash
pytest -v
```

- [ ] **Step 6: Commit**

```bash
git add goldencheck/profilers/ tests/profilers/
git commit -m "feat: add context to BaseProfiler, minority wrong type detection"
```

---

## Task 3: Profiler Confidence Values + Range Chaining

**Files:**
- Modify: `goldencheck/profilers/nullability.py`
- Modify: `goldencheck/profilers/uniqueness.py`
- Modify: `goldencheck/profilers/format_detection.py`
- Modify: `goldencheck/profilers/range_distribution.py`
- Modify: `goldencheck/profilers/cardinality.py`
- Modify: `goldencheck/profilers/pattern_consistency.py`
- Modify: relevant test files

- [ ] **Step 1: Add confidence values to each profiler per spec rules**

Each profiler assigns `confidence=X` on every Finding it creates. Follow the spec table exactly:

**Nullability:** 0 nulls in 1000+ rows → 0.95. 0 nulls in <50 → 0.5. All null → 0.99.
**Uniqueness:** 100% unique, 100+ rows → 0.95. 95-99% → 0.6.
**Format detection:** >95% match → 0.9. 70-95% → 0.6.
**Range/distribution:** >5 stddev → 0.9. 3-5 stddev → 0.7.
**Cardinality:** <10 unique in 1000+ rows → 0.9. 10-20 in 50-100 → 0.5.
**Pattern consistency:** minority <5% → 0.8. 5-30% → 0.5.

- [ ] **Step 2: Chain range profiler with type inference context**

In `RangeDistributionProfiler.profile()`, check `context`:
```python
def profile(self, df, column, *, context=None):
    col = df[column]
    dtype = col.dtype
    is_numeric = dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float32, pl.Float64)

    # Chain: if type inference flagged as mostly numeric, cast and run
    if not is_numeric and context and context.get(column, {}).get("mostly_numeric"):
        col = col.cast(pl.Float64, strict=False).drop_nulls()
        is_numeric = True

    if not is_numeric:
        return []
    # ... rest of existing logic
```

- [ ] **Step 3: Write tests for chaining**

```python
def test_range_profiler_chains_with_type_inference():
    df = pl.DataFrame({"age": ["25", "30", "999", "28", "33"]})
    context = {"age": {"mostly_numeric": True}}
    findings = RangeDistributionProfiler().profile(df, "age", context=context)
    assert len(findings) > 0  # should detect outlier 999
```

- [ ] **Step 4: Run tests**

```bash
pytest -v
```

- [ ] **Step 5: Commit**

```bash
git add goldencheck/profilers/ tests/profilers/
git commit -m "feat: add confidence scores to all profilers, chain range with type inference"
```

---

## Task 4: Temporal Order + Null Correlation Improvements

**Files:**
- Modify: `goldencheck/relations/temporal.py`
- Modify: `goldencheck/relations/null_correlation.py`
- Modify: `tests/relations/test_temporal.py`
- Modify: `tests/relations/test_null_correlation.py`

- [ ] **Step 1: Write tests for new temporal pairs**

```python
def test_signup_login_pair_detected():
    df = pl.DataFrame({
        "signup_date": ["2024-01-01", "2024-02-01"],
        "last_login": ["2024-01-15", "2024-01-15"],  # second row: login before signup
    })
    findings = TemporalOrderProfiler().profile(df)
    assert any(f.severity == Severity.ERROR for f in findings)

def test_any_date_pair_low_confidence():
    df = pl.DataFrame({
        "date_a": ["2024-01-01", "2024-03-01"],
        "date_b": ["2024-01-15", "2024-02-01"],  # violation
    })
    findings = TemporalOrderProfiler().profile(df)
    # Should detect but with low confidence (not keyword matched)
    if findings:
        assert any(f.confidence < 0.5 for f in findings)

def test_many_date_columns_skips_exhaustive():
    # 12 date columns — should skip any-date-pair check
    data = {f"date_{i}": ["2024-01-01"] for i in range(12)}
    df = pl.DataFrame(data)
    findings = TemporalOrderProfiler().profile(df)
    # Only keyword-matched pairs, no exhaustive check
    assert len(findings) == 0  # no keyword matches among date_0..date_11
```

- [ ] **Step 2: Implement temporal improvements**

Add new keyword pairs: `(signup, login)`, `(signup, last_login)`, `(open, close)`, `(opened, closed)`, `(hire, termination)`, `(birth, death)`, `(order, delivery)`, `(order, ship)`.

Add any-date-pair fallback: detect all Date-typed columns, check pairs with confidence=0.4. Guard: skip if >10 date columns.

Keyword-matched pairs get confidence=0.9. Non-keyword pairs get confidence=0.4.

- [ ] **Step 3: Write tests for null correlation improvements**

```python
def test_three_column_group_reported():
    df = pl.DataFrame({
        "addr": ["123 St", None, "456 Ave", None] * 25,
        "city": ["NYC", None, "LA", None] * 25,
        "zip": ["10001", None, "90001", None] * 25,
    })
    findings = NullCorrelationProfiler().profile(df)
    assert any("correlat" in f.message.lower() for f in findings)

def test_two_column_pair_suppressed():
    df = pl.DataFrame({
        "a": [1, None, 3, None] * 25,
        "b": [10, None, 30, None] * 25,
    })
    findings = NullCorrelationProfiler().profile(df)
    # Pairs of 2 should no longer be reported
    corr = [f for f in findings if "correlat" in f.message.lower()]
    assert len(corr) == 0
```

- [ ] **Step 4: Implement null correlation improvements**

Raise threshold to 95%. Require >5% nulls. Group using union-find, only report groups of 3+. Confidence: 3+ columns at >95% → 0.8.

- [ ] **Step 5: Run tests**

```bash
pytest -v
```

- [ ] **Step 6: Commit**

```bash
git add goldencheck/relations/ tests/relations/
git commit -m "feat: broaden temporal heuristics, reduce null correlation noise"
```

---

## Task 5: Confidence Engine (Corroboration + Downgrade)

**Files:**
- Create: `goldencheck/engine/confidence.py`
- Create: `tests/engine/test_confidence.py`

- [ ] **Step 1: Write tests**

```python
from goldencheck.engine.confidence import apply_corroboration_boost, apply_confidence_downgrade
from goldencheck.models.finding import Finding, Severity

def test_corroboration_boost_two_profilers():
    findings = [
        Finding(severity=Severity.WARNING, column="email", check="format", message="a", confidence=0.6),
        Finding(severity=Severity.WARNING, column="email", check="pattern", message="b", confidence=0.5),
    ]
    result = apply_corroboration_boost(findings)
    assert all(f.confidence >= 0.6 for f in result)  # boosted by 0.1

def test_corroboration_boost_three_profilers():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="1", confidence=0.5),
        Finding(severity=Severity.WARNING, column="x", check="b", message="2", confidence=0.5),
        Finding(severity=Severity.WARNING, column="x", check="c", message="3", confidence=0.5),
    ]
    result = apply_corroboration_boost(findings)
    assert all(f.confidence == 0.7 for f in result)  # boosted by 0.2

def test_corroboration_boost_capped_at_1():
    findings = [
        Finding(severity=Severity.ERROR, column="x", check="a", message="1", confidence=0.95),
        Finding(severity=Severity.ERROR, column="x", check="b", message="2", confidence=0.95),
    ]
    result = apply_corroboration_boost(findings)
    assert all(f.confidence == 1.0 for f in result)

def test_corroboration_no_mutation():
    original = Finding(severity=Severity.WARNING, column="x", check="a", message="1", confidence=0.5)
    findings = [original, Finding(severity=Severity.WARNING, column="x", check="b", message="2", confidence=0.5)]
    apply_corroboration_boost(findings)
    assert original.confidence == 0.5  # original not mutated

def test_downgrade_low_confidence_without_llm():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="m", confidence=0.3),
        Finding(severity=Severity.ERROR, column="y", check="b", message="n", confidence=0.9),
    ]
    result = apply_confidence_downgrade(findings, llm_boost=False)
    assert result[0].severity == Severity.INFO  # low confidence downgraded
    assert result[1].severity == Severity.ERROR  # high confidence unchanged

def test_downgrade_skipped_with_llm():
    findings = [
        Finding(severity=Severity.WARNING, column="x", check="a", message="m", confidence=0.3),
    ]
    result = apply_confidence_downgrade(findings, llm_boost=True)
    assert result[0].severity == Severity.WARNING  # not downgraded, LLM will handle
```

- [ ] **Step 2: Implement confidence.py**

```python
"""Post-scan confidence processing."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
from goldencheck.models.finding import Finding, Severity


def apply_corroboration_boost(findings: list[Finding]) -> list[Finding]:
    """Boost confidence for columns flagged by multiple profilers."""
    # Count distinct WARNING/ERROR checks per column
    checks_per_col = defaultdict(set)
    for f in findings:
        if f.severity in (Severity.ERROR, Severity.WARNING):
            checks_per_col[f.column].add(f.check)

    result = []
    for f in findings:
        col_count = len(checks_per_col.get(f.column, set()))
        if col_count >= 3:
            boost = 0.2
        elif col_count >= 2:
            boost = 0.1
        else:
            boost = 0.0

        if boost > 0 and f.severity in (Severity.ERROR, Severity.WARNING):
            new_conf = min(f.confidence + boost, 1.0)
            result.append(replace(f, confidence=new_conf))
        else:
            result.append(f)
    return result


def apply_confidence_downgrade(findings: list[Finding], llm_boost: bool) -> list[Finding]:
    """Downgrade low-confidence findings to INFO when LLM boost is not enabled."""
    if llm_boost:
        return list(findings)

    result = []
    for f in findings:
        if f.confidence < 0.5 and f.severity in (Severity.ERROR, Severity.WARNING):
            result.append(replace(
                f,
                severity=Severity.INFO,
                message=f"{f.message} (low confidence — use --llm-boost to verify)",
            ))
        else:
            result.append(f)
    return result
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/engine/test_confidence.py -v
```

- [ ] **Step 4: Commit**

```bash
git add goldencheck/engine/confidence.py tests/engine/test_confidence.py
git commit -m "feat: add confidence corroboration boost and downgrade engine"
```

---

## Task 6: Wire Confidence into Scanner + LLM Routing

**Files:**
- Modify: `goldencheck/engine/scanner.py`
- Modify: `goldencheck/llm/sample_block.py`
- Modify: `tests/llm/test_sample_block.py`

- [ ] **Step 1: Write test for focus_columns filtering**

Add to `tests/llm/test_sample_block.py`:
```python
def test_focus_columns_filters():
    data = {f"col_{i}": list(range(100)) for i in range(10)}
    df = pl.DataFrame(data)
    blocks = build_sample_blocks(df, [], focus_columns={"col_0", "col_5"})
    assert len(blocks) == 2
    assert "col_0" in blocks
    assert "col_5" in blocks
    assert "col_1" not in blocks
```

- [ ] **Step 2: Add focus_columns to build_sample_blocks**

In `goldencheck/llm/sample_block.py`, add `focus_columns: set[str] | None = None` parameter. When provided, filter columns list early:
```python
if focus_columns is not None:
    columns = [c for c in columns if c in focus_columns]
```

- [ ] **Step 3: Wire confidence into scanner**

In `goldencheck/engine/scanner.py`:
- Create `profiler_context = {}` before the column loop
- Pass `context=profiler_context` to each profiler call
- After all profilers run, call:
  ```python
  from goldencheck.engine.confidence import apply_corroboration_boost, apply_confidence_downgrade
  all_findings = apply_corroboration_boost(all_findings)
  ```
- In `scan_file_with_llm`, after profilers + corroboration, compute `focus_columns`:
  ```python
  low_conf_cols = {f.column for f in findings if f.confidence < 0.5}
  blocks = build_sample_blocks(sample, findings, focus_columns=low_conf_cols if low_conf_cols else None)
  ```
  If no low-confidence columns, skip LLM entirely and log: "All findings are high confidence. LLM boost not needed."

- [ ] **Step 4: Run all tests**

```bash
pytest -v
```

- [ ] **Step 5: Commit**

```bash
git add goldencheck/engine/ goldencheck/llm/sample_block.py tests/
git commit -m "feat: wire confidence into scanner with LLM routing"
```

---

## Task 7: Benchmark Rerun + Push

**Files:**
- No new files — run existing benchmarks

- [ ] **Step 1: Run profiler-only benchmark**

```bash
cd D:/show_case/goldencheck && python -X utf8 benchmarks/goldencheck_benchmark.py
```

Target: column recall should improve from 87% toward 95%+.

- [ ] **Step 2: Run LLM comparison benchmark**

```bash
source .testing/.env && python -X utf8 benchmarks/goldencheck_benchmark_llm.py
```

Target: LLM boost should still hit 100%, with fewer tokens used.

- [ ] **Step 3: Run full test suite**

```bash
pytest -v
ruff check .
```

- [ ] **Step 4: Commit any benchmark file updates**

```bash
git add -A && git commit -m "bench: rerun benchmarks with confidence routing"
```

- [ ] **Step 5: Push**

```bash
gh auth switch --user benzsevern && git push
```
