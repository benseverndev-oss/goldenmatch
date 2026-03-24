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

## Approach

Two phases:
1. **Profiler-only** (free, no API key) — fixes zip_code, auth_number, patient_age (3 of 4)
2. **LLM enhancement** (requires API key) — fixes insurance_id and improves overall detection

---

## Phase 1: Profiler Improvements

### 1a. Narrow Geo Suppression (zip_code fix)

**Problem:** The `geo` semantic type suppresses `pattern_consistency`, which hides the 5-digit vs 9-digit zip code format inconsistency.

**Fix:** In `suppression.py`, when suppressing a `pattern_consistency` finding on a geo column, check whether the minority pattern has a different **string length** than the dominant pattern. If lengths differ significantly, don't suppress — it's a format inconsistency, not noise.

**Implementation:**
- Modify `apply_suppression()` in `goldencheck/semantic/suppression.py`
- Before suppressing a `pattern_consistency` finding, extract the dominant and minority pattern lengths from the finding message (the message already contains pattern strings like `'DDDDD'` vs `'DDDDD-DDDD'`)
- If the patterns differ in length by >1 character, skip suppression for this finding

**Why this works:** `CA` vs `ca` patterns have the same length (2) — suppressed (good). `90210` vs `90210-1234` patterns have lengths 5 vs 10 — not suppressed (good).

### 1b. Age vs DOB Cross-Validation (patient_age fix)

**Problem:** No profiler detects when an `age` column doesn't match a `date_of_birth` column.

**Fix:** New relation profiler that pairs age-like columns with DOB-like columns and validates consistency.

**New file:** `goldencheck/relations/age_validation.py`

**Heuristics for pairing:**
- Age column: name contains `age` (but not `stage`, `page`, `usage`, etc.) AND column is numeric
- DOB column: name contains `birth`, `dob`, `born`, `date_of_birth`
- Both must exist in the same dataset

**Validation logic:**
1. Parse DOB column to dates
2. Calculate expected age as `(reference_date - DOB) / 365.25` where reference_date is the max date in any date column (or today)
3. Flag rows where `abs(actual_age - expected_age) > 2` (2-year tolerance for birthday timing)

**Finding format:**
```
severity: ERROR
column: "patient_age"  # only the age column, not DOB (avoids FP on clean DOB column)
check: "cross_column_validation"
message: "5 row(s) where age doesn't match calculated age from date_of_birth — possible logic violation"
```

**Note:** The finding column is ONLY the age column (not comma-joined with DOB) to avoid creating false positives on clean DOB columns in the benchmark.

### 1c. String Length Format Check (auth_number fix)

**Problem:** `auth_number` has 90% 10-digit values and 10% shorter values, but no profiler catches this as a format issue.

**Fix:** This was attempted in the previous session but the string length check was too aggressive (hurt precision). The narrower approach: only flag length inconsistency on columns that are classified as `identifier` semantic type, where length uniformity is expected.

**Implementation:**
- Add to `goldencheck/profilers/format_detection.py`
- After existing format checks, if the column is high-uniformity (>90% same length) AND classified as identifier/code, flag length outliers
- Use context dict to check semantic type (if available)

**Guard rails:**
- Only fire on columns with >90% dominant length
- Only fire when outliers are <5% of total
- Only fire on columns that look like IDs/codes (high uniqueness OR name contains id, number, code, auth, key)

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

**Fix:** In `goldencheck/llm/merger.py`, ensure that LLM-generated findings preserve key phrases:
- Cross-column issues must include "mismatch", "inconsistent with", or "doesn't match"
- Logic violations must include "violat" or "logic"
- Invalid values must include "invalid"

This is already partially working (the LLM prompt asks for these keywords). The fix is defensive: if the LLM's message doesn't contain any keyword from the relevant check's keyword set, append a standard suffix like "[cross-column mismatch detected]".

---

## Testing Strategy

| Component | Test Approach |
|-----------|---------------|
| Geo suppression narrowing | Unit test: pattern with same length → suppressed; different length → not suppressed |
| Age/DOB validation | Unit test with matching and mismatching ages; integration test with fixture |
| String length check | Unit test with uniform-length column + outliers; test guard rails |
| LLM prompt | Existing mock-based tests in `tests/llm/test_integration.py` |
| Benchmark regression | Run full DQBench after each change, verify score doesn't decrease |

## Success Criteria

- DQBench score >= 90.00 (profiler-only, zero-config)
- No regression on existing T1/T2/T3 recall or precision
- 189+ tests passing

## Non-Goals

- Not adding new profilers beyond the 3 specified
- Not modifying the benchmark (locked)
- Not adding new semantic types

## Version

Ships as part of GoldenCheck v0.5.0 alongside the adoption features.
