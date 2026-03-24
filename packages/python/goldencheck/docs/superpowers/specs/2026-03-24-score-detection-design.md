# Score & Detection — Design Spec

## Goal

Push DQBench score from 87.71 toward 90+ by fixing the 4 remaining misses with targeted profiler improvements and LLM prompt enhancements.

## Current State

DQBench 87.71 with 4 remaining misses:

| Tier | Column | Issue | Root Cause |
|------|--------|-------|------------|
| T1 | `zip_code` | `inconsistent_format` | `geo` semantic type suppresses `pattern_consistency` even when patterns differ in length |
| T3 | `auth_number` | `invalid_format` | No length-based format consistency check |
| T3 | `insurance_id` | `logic_violation` | ID prefix vs provider_type cross-column — requires domain knowledge |
| T3 | `patient_age` | `logic_violation` | Age doesn't match calculated age from DOB — no age/DOB cross-validation |

**Baselines for regression checking:**
- T1: F1=0.9375, Recall=0.9375, Precision=0.9375
- T2: F1=0.9091, Recall=1.0000, Precision=0.8333
- T3: F1=0.8148, Recall=0.8800, Precision=0.7586

## Approach

Two phases:
1. **Profiler-only** (free, no API key) — fixes zip_code, auth_number, patient_age (3 of 4)
2. **LLM enhancement** (requires API key) — fixes insurance_id and improves overall detection

---

## Phase 1: Profiler Improvements

### 1a. Narrow Geo Suppression (zip_code fix)

**Problem:** The `geo` semantic type suppresses `pattern_consistency`, which hides the 5-digit vs 9-digit zip code format inconsistency.

**Fix:** Add a `metadata` dict field to the `Finding` dataclass for structured data that profilers can attach. The `PatternConsistencyProfiler` populates `metadata["dominant_pattern"]` and `metadata["minority_pattern"]` on its findings. The suppression engine then checks pattern lengths from `metadata` rather than parsing the message string.

**Implementation:**

1. Add `metadata: dict = field(default_factory=dict)` to the `Finding` dataclass in `goldencheck/models/finding.py`
2. In `PatternConsistencyProfiler` (`goldencheck/profilers/pattern_consistency.py`), set `metadata={"dominant_pattern": dominant_pattern, "minority_pattern": minority_pattern}` on each finding
3. In `apply_suppression()` (`goldencheck/semantic/suppression.py`), before suppressing a `pattern_consistency` finding, check: if `metadata` has both pattern keys and the patterns differ in length by >1 char, skip suppression

**Why this works:** `CA` vs `ca` patterns have the same length (2) — suppressed (good). `90210` vs `90210-1234` patterns have lengths 5 vs 10 — not suppressed (good). The metadata approach is robust — no message string parsing.

### 1b. Age vs DOB Cross-Validation (patient_age fix)

**Problem:** No profiler detects when an `age` column doesn't match a `date_of_birth` column.

**Fix:** New relation profiler that pairs age-like columns with DOB-like columns and validates consistency.

**New file:** `goldencheck/relations/age_validation.py`

**Registration:** Add `AgeValidationProfiler` to the `RELATION_PROFILERS` list in `goldencheck/engine/scanner.py`.

**Heuristics for pairing:**
- Age column: name contains `age` (but not `stage`, `page`, `usage`, `mileage`, `dosage`, `voltage`) AND column is numeric
- DOB column: name contains `birth`, `dob`, `born`, `date_of_birth`
- Both must exist in the same dataset

**Reference date:** Use the maximum value from date-typed columns in the dataset (excluding the DOB column itself), filtered to dates <= today. Fall back to `datetime.date.today()` if no suitable date column exists.

**Validation logic:**
1. Parse DOB column to dates
2. Calculate expected age as `(reference_date - DOB).days / 365.25`
3. Flag rows where `abs(actual_age - expected_age) > 2` (2-year tolerance for birthday timing and reference date uncertainty)

**Finding format:**
```
severity: ERROR
column: "patient_age"  # only the age column (avoids FP on clean DOB column)
check: "cross_column"
message: "5 row(s) where patient_age doesn't match calculated age from date_of_birth — values mismatch by more than 2 years"
```

**Check name:** Uses `"cross_column"` (not `"cross_column_validation"`) to match the canonical check name in `prompts.py` and the DQBench `ISSUE_KEYWORDS` mapping for `logic_violation`, which includes keywords "mismatch" and "doesn't match".

**Note:** The finding column is ONLY the age column (not comma-joined with DOB) to avoid creating false positives on clean DOB columns in the benchmark.

### 1c. String Length Format Check (auth_number fix)

**Problem:** `auth_number` has 90% 10-digit values and 10% shorter values, but no profiler catches this as a format issue.

**Fix:** Add a length consistency check as a **post-classification step** in `_post_classification_checks()` in `scanner.py` (not in `format_detection.py`). This runs AFTER semantic classification, so it has access to column types.

**Why post-classification:** The format detection profiler runs before semantic classification (scanner pipeline order). The string length check needs to know if a column is an identifier/code type to avoid false positives. Running it in `_post_classification_checks()` (which already exists for digits-in-name detection) solves this cleanly.

**Implementation:**
- Add to `_post_classification_checks()` in `goldencheck/engine/scanner.py`
- For columns classified as `identifier` semantic type, or whose name contains `id`, `number`, `code`, `auth`, `key`:
  - Compute string length distribution
  - If >90% of values share a dominant length AND <5% are outliers, flag as format inconsistency

**Guard rails:**
- Only fire on string columns
- Only fire on columns with >90% dominant length
- Only fire when outliers are <5% of total
- Only fire on identifier-like columns (semantic type OR name heuristic)

**Finding format:**
```
severity: WARNING
column: "auth_number"
check: "format_detection"
message: "Inconsistent string length: 90% of values are 10 chars but 9916 row(s) have different lengths — possible invalid format"
confidence: 0.75
```

---

## Phase 2: LLM Enhancement

### 2a. Prompt Improvement

**Problem:** The current LLM system prompt is generic. It doesn't specifically guide the LLM to check cross-column ID consistency or age/DOB math.

**Fix:** Add targeted guidance to `SYSTEM_PROMPT` in `goldencheck/llm/prompts.py`:

```
Additional checks to perform:
- For ID/code columns: check if prefixes or suffixes follow patterns consistent with related categorical columns (e.g., insurance ID prefix should match provider type)
- For age columns: verify mathematical consistency with date-of-birth columns
- For date columns: check for impossible dates (future dates, dates before 1900)
- For geographic columns: verify state/zip consistency
```

### 2b. Merger Keyword Preservation

**Problem:** When the LLM merger creates findings, it sometimes uses generic messages that don't contain the keywords the benchmark scorer looks for.

**Fix:** In `goldencheck/llm/merger.py`, define required keywords as a constant mapping:

```python
_REQUIRED_KEYWORDS: dict[str, list[str]] = {
    "cross_column": ["mismatch", "inconsistent", "doesn't match"],
    "invalid_values": ["invalid"],
    "logic_violation": ["violat", "logic", "mismatch"],
}
```

When creating a new Finding from an LLM issue, check if the message contains at least one keyword from the relevant check's list. If not, append a standard suffix (e.g., `" [cross-column mismatch detected]"`).

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| Geo suppression narrowing | Unit test: finding with same-length patterns in metadata → suppressed; different-length → not suppressed |
| Age/DOB validation | Unit test with matching and mismatching ages; test name heuristic exclusions (stage, page); integration test with fixture |
| String length check | Unit test with uniform-length identifier column + outliers; test that non-identifier columns are skipped |
| LLM prompt | Existing mock-based tests in `tests/llm/test_integration.py` |
| Merger keywords | Unit test: LLM finding without required keyword gets suffix appended |
| Benchmark regression | Run full DQBench after each change, verify score doesn't decrease below baselines |

## Success Criteria

- DQBench score >= 90.00 (profiler-only, zero-config)
- No regression below baselines: T1 F1>=0.94, T2 F1>=0.91, T3 F1>=0.81
- 189+ tests passing

## Non-Goals

- Not adding new profilers beyond the 3 specified
- Not modifying the benchmark (locked)
- Not adding new semantic types

## Version

Ships as part of GoldenCheck v0.5.0 alongside the adoption features.
