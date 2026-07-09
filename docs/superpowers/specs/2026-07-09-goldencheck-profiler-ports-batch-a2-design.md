# GoldenCheck Polars eviction — Profiler ports Batch A2 (str_match_count + str_filter seam)

Date: 2026-07-09
Status: design — approved in brainstorming, pending spec review
Base: fresh `origin/main` (P0 #1605 + Batch A #1606 both merged; the Frame/Column seam + dtype/cast/member_count are present)
Parent program: goldencheck Polars eviction (see `2026-07-08-goldencheck-polars-eviction-p0-design.md`)

## Context

Continuing the goldencheck Polars eviction. P0 built the `Frame`/`Column` seam; Batch A ported
`nullability`/`cardinality`/`uniqueness` (P0) + `type_inference`/`fuzzy_values` (adding `dtype`,
`cast`, `member_count`). 5 of ~13 column profilers are polars-free. This batch ports the two
**string-format** profilers that Batch A deferred: `format_detection` and `encoding_detection`.

Batch A's spec review flagged why they were deferred: `format_detection` has a **cross-format**
block `non_null.filter(~matchesA).str.contains(B).sum()` ("count values not matching A that match
B"), and `encoding_detection` samples the **matching** rows (`filter(mask).head(5)`), not the
complement — neither expressible byte-identically with Batch A's seam. This batch designs the
string-op seam family from a complete reading of both files.

**Seam philosophy (unchanged from Batch A):** the ops go on the `Column` seam; `PolarsColumn`
delegates to the exact Polars call (byte-identical, no perf regression, Polars stays the fast
path). The non-Polars implementation arrives with the Stage-2 substrate backend — not here.

## Scope

### In scope
Port `format_detection` and `encoding_detection` onto the seam, adding **2** composable `Column`
methods. Byte-identical, no version bump.

### Explicitly NOT in scope
`pattern_consistency` (its `_generalize` is a cProfile hotspot — needs a vectorized
`str.replace_all` + `value_counts` seam op; a later batch). Batch B (`freshness`,
`range_distribution`, `sequence_detection` — scalar stats). Batch C (`drift_detection`). The
relation profilers. The Stage-2 non-Polars backend. The reader. The deps flip.

### Success criteria
- `format_detection` + `encoding_detection` are polars-free (import no `pl`), routing through the seam.
- Their existing tests pass **unedited** (byte-identical Findings — the parity gate).
- Full suite green; `import goldencheck` still loads zero Polars.

## The seam additions (`core/frame.py` — `Column`, PolarsColumn delegates)

Two composable methods (`PolarsColumn` delegates each to the exact Polars call):

| Method | Signature | PolarsColumn impl | Covers |
|---|---|---|---|
| `str_match_count` | `str_match_count(pattern: str) -> int` | `int(self._s.str.contains(pattern).sum())` | every `.str.contains(p).sum()` count |
| `str_filter` | `str_filter(pattern: str, *, matching: bool) -> Column` | `matching`: `PolarsColumn(self._s.filter(self._s.str.contains(pattern)))`; else `PolarsColumn(self._s.filter(~self._s.str.contains(pattern)))` | the matching/non-matching row sets |

`str_filter` returns a full `PolarsColumn`, so it composes with existing seam ops (`to_list`,
`str_match_count`). New `pl.` refs are all in-function → import gate stays green.

**Composition covers all three cases (byte-identical):**
- **count matching:** `col.str_match_count(pat)` = `non_null.str.contains(pat).sum()` (as `int`).
- **sample matching** (encoding): `col.str_filter(pat, matching=True).to_list()[:5]` =
  `non_null.filter(mask).head(5).to_list()` (`.head(5).to_list()` ≡ `.to_list()[:5]`, same first-5).
- **sample non-matching** (format): `col.str_filter(A, matching=False).to_list()[:5]` =
  `non_null.filter(~matches).head(5).to_list()`.
- **cross-format count** (format): `col.str_filter(A, matching=False).str_match_count(B)` =
  `non_null.filter(~matches).str.contains(B).sum()`.

Note: this recomputes `str.contains` where the original reused a `matches` mask — deterministic, so
byte-identical (Batch A's review already accepted this for format). Perf: PolarsColumn still runs
Polars vectorized `str.contains`/`filter`, so no regression vs today.

## The 2 ports

Both currently start `frame = to_frame(frame)`; `df = frame.native`; `col = df[column]`;
`col.dtype not in (pl.Utf8, pl.String)` → `return`; `non_null = col.drop_nulls()`; `total = len(non_null)`.

- **format_detection** — `col = frame.column(column)`; `if col.dtype != "str": return`;
  `non_null = col.drop_nulls()`; `total = len(non_null)`. Per format:
  `match_count = non_null.str_match_count(pattern)`; `if non_match_count > 0:
  sample = non_null.str_filter(pattern, matching=False).to_list()[:5]`; cross-format:
  `wrong_fmt_count = non_null.str_filter(pattern, matching=False).str_match_count(other_pattern)`.
  All thresholds/messages/Findings UNCHANGED.
- **encoding_detection** — same dtype gate + `non_null`; per pattern:
  `count = non_null.str_match_count(PATTERN)`; `if count > 0:
  sample = non_null.str_filter(PATTERN, matching=True).to_list()[:5]`. All UNCHANGED.

Each port: signature `def profile(self, frame, column: str, *, context=None)` (drop the
`frame: pl.DataFrame` annotation — matches P0/Batch-A profilers); remove
`from goldencheck._polars_lazy import pl` → polars-free.

## Testing

- **Seam unit tests** (`tests/core/test_frame.py` additions): `str_match_count` on a mixed column
  == `int(s.str.contains(p).sum())`; `str_filter(p, matching=True)`/`(matching=False)` return the
  matching / non-matching values (compare `.to_list()` to the raw `s.filter(mask)`/`filter(~mask)`);
  `str_filter(A, matching=False).str_match_count(B)` == the cross-format raw expression.
- **Parity gate:** `test_format_detection.py` + `test_encoding_detection.py` pass **unedited**
  (byte-identical Findings). If a test diverges, the port broke byte-identity — fix the port.
- **Regression:** full suite green (same counts); import gate green; the 2 ported files grep-clean
  of `polars`/`_polars_lazy`/`pl.`.

## Risks

- **`.head(5).to_list()` vs `.to_list()[:5]`** — byte-identical (both first-5 in order); confirmed
  equivalent for a Polars Series. If any profiler used `.head(n)` for a non-slice reason (it does
  not — both just sample 5), revisit.
- **`str.contains` recomputation** — deterministic → byte-identical; PolarsColumn keeps it
  vectorized (no perf regression). The regex-vs-Python risk is NOT here (Polars regex under the
  hood) — it moves to Stage 2's non-Polars backend.
- **`.sum()` type** — `str_match_count` wraps in `int(...)`; the profilers use the count in
  arithmetic (`/ total`) + `affected_rows` (an int field), so the value is byte-identical.
- **Seam growth** — only 2 methods, both composable primitives (not task-shaped over-specific ops).
  Do NOT add a bespoke `not_matching_match_count(A,B)` — `str_filter(A, matching=False)
  .str_match_count(B)` composes it from the two general methods.

## Non-goals (YAGNI)
`pattern_consistency`; Batches B/C; the relation profilers; scalar-stats / `.dt` seam ops; the
Stage-2 non-Polars backend; reader; deps flip.
