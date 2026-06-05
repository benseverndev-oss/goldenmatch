---
layout: default
title: Auto-config cost model
nav_order: 10
---

# Auto-config cost model: why exact matchkeys are cardinality-gated, not row-count-gated

Zero-config auto-config inspects your columns and decides, for each one, whether to
anchor an exact matchkey, use it as a blocking key for fuzzy matching, or skip it.
The decision is driven by **cardinality ratio** (distinct values / total rows), not raw
row count -- and the reason comes from how exact matchkeys are implemented.

---

## How exact matchkeys work

An exact matchkey is a Polars hash self-join, not a nested loop. GoldenMatch groups
records by the matchkey value and emits candidate pairs only within each group.
Cost is proportional to the number of emitted pairs, which is bounded by how many
records share any single value -- the column's cardinality.

A high-cardinality column (most values are unique or near-unique) emits a small
number of pairs per group. It is both cheap to compute and safe: it cannot create a
mega-cluster because no single value matches thousands of unrelated records.

---

## The admission band

Auto-config admits a column as an exact matchkey when its cardinality ratio falls in
the half-open interval `[0.5, 1.0)`.

| Cardinality ratio | Interpretation | Decision |
|-------------------|----------------|----------|
| `< 0.5` | Too few distinct values. An exact match collapses many records that merely share a common value (e.g., a status code, a state abbreviation) into one mega-cluster. | Excluded |
| `>= 0.5 and < 1.0` | A real shared identifier. Two records for the same entity share the value; distinct enough to stay bounded, shared enough to catch true duplicates. | **Admitted as exact matchkey** |
| `== 1.0` | Perfectly unique per-record surrogate (e.g., a database primary key). Never shared, so an exact match finds nothing and asserts no real-world identity. | Excluded for config hygiene |

The default blocking-candidate cardinality gate is controlled by
`GOLDENMATCH_BLOCKING_MAX_RATIO` (default `0.5`). Columns above this threshold are
too unique to block on and skip the fuzzy blocking path entirely.

---

## Identifiers do not need blocking

Because the exact matchkey is a hash join, high-cardinality identifiers anchor
matchkeys directly. They are intentionally NOT used as blocking keys.

A near-unique column makes terrible blocks -- each group contains one or two records,
so the candidate-pair reduction is negligible. Blocking exists to bound the candidate
space for **fuzzy** matchkeys (names, addresses), where you need approximate
comparison within a manageable neighborhood.

Auto-config caps single-key block size via `max_safe_block` (scaled by row count)
and falls back to compound or multi-pass blocking when a single key would produce
oversized blocks.

---

## Worked example: healthcare-provider dataset

| Column | Approx. cardinality ratio | Exact matchkey? | Blocking key? | Fuzzy? |
|--------|--------------------------|-----------------|---------------|--------|
| `source` | 0.003 | No (too low) | No | No |
| `npi` | 0.98 | **Yes** | No | No |
| `email` | 0.82 | **Yes** | No | No |
| `phone_number` | 0.75 | **Yes** | No | No |
| `matching_id` | 1.0 | No (perfectly unique) | No | No |
| `zip5` | 0.12 | No (too low) | **Yes** | No |
| `last_name` | 0.31 | No (too low) | **Yes** (soundex) | **Yes** |
| `first_name` | 0.18 | No (too low) | **Yes** (first token) | **Yes** |

`npi`, `email`, and `phone_number` all fall in `[0.5, 1.0)`, so they anchor exact
matchkeys directly. `zip5`, `last_name`, and `first_name` are low-cardinality, so
they drive blocking and fuzzy scoring instead. `source` and `matching_id` are skipped
(too low and perfectly unique, respectively).

---

## When auto-config falls back to fuzzy-only

If every column that looks like an identifier is below `0.5` cardinality or exactly
`1.0`, auto-config logs a WARNING naming the excluded columns and degrades to
fuzzy-only mode:

```
WARNING  goldenmatch.autoconfig: no exact matchkey candidates found
         (excluded: matching_id=1.0, source=0.003, status=0.001)
         falling back to fuzzy-only matching
```

Remedies:

- Pass an explicit config if you know which column is the real identifier.
- Check whether the identifier column is genuinely populated. A sparsely filled NPI
  column reads as low-cardinality until nulls are excluded.
- If the dataset legitimately has no shared identifier, fuzzy-only is correct and the
  WARNING is informational.

---

## Environment notes

**Windows startup hang.** On some Windows configurations a WMI query during Polars
initialization can stall for several seconds. Set `POLARS_SKIP_CPU_CHECK=1` in your
environment if `goldenmatch` hangs before printing any output.

```bash
set POLARS_SKIP_CPU_CHECK=1   # cmd
$env:POLARS_SKIP_CPU_CHECK=1  # PowerShell
```
