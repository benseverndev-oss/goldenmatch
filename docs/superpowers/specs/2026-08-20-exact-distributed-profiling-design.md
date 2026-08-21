# Exact distributed profiling — slice 1 of distributed auto-config

**Status:** proposed, not started
**Depends on:** `goldenmatch/spark/autoconfig.py` (P6), `goldenmatch/core/autoconfig.py`

## The problem, stated as a bug class rather than a feature gap

Auto-config's decisions come from `profile_columns(df, sample_size=1000)`. Some
of those statistics are properties of a *distribution*, and a fixed-size sample
of a growing frame measures them wrong in a way that gets worse with scale.

This repo has already paid for it twice, and both are documented in the source:

**1. `cardinality_ratio`, the #876 surrogate guard.** `autoconfig.py` records an
`email` column whose true distinct-fraction is a flat **0.28** reading
**0.72 / 0.94 / 0.97 / 0.98 / 1.00** at 100K / 500K / 1M / 2M / 5M. Every
"#876 surrogate guard" compares that number to an absolute 1.0, so at 5M
zero-config discarded its only exact identity column as a "perfectly-unique
surrogate key", fell back to fuzzy-only matchkeys and name-based blocking, and
produced a 185M-pair run that took 66 GB and killed the CI runner.

> The verdict flipped with SCALE, not with the data — the exact thing the
> scale-invariant-correctness commitment forbids.

**2. `n_distinct`, PR #2687 (2026-08-19).** A new rule drops a scored field when
`n_distinct <= 1`. That count comes from the same 1,000-row sample, so a column
that is 99.99% one value reads constant and its field is dropped. Miss
probability for a value at frequency `p` over 1,000 rows: **37% at 1e-3, 90% at
1e-4, 99% at 1e-5**. A 2,000-row frame samples the column fully and keeps the
field; a 5M-row frame drops it. Same data, different config.

The pattern is identical both times: a **sample-derived distribution statistic
compared against an absolute threshold**.

## The insight that makes this a cluster feature

On one box, sampling is a necessary compromise. On a cluster it is not: these
statistics are a single distributed aggregate, and the exact answer is the one
that does not flip between 100K and 5M.

So this slice is primarily a **correctness** change that happens to require
distribution, not a scale feature. It would be worth doing even if the row
ceiling never moved.

## The line: what becomes exact, what stays sampled

The split is not arbitrary. **Distribution** statistics go exact; **classification**
inputs stay sampled, because they answer "what kind of column is this?", which a
sample answers correctly by construction.

| `ColumnProfile` field | today | after | why |
|---|---|---|---|
| `null_rate` | sample | **exact** | distribution; one `count` |
| `avg_len` | sample | **exact** | distribution; one `avg(length())` |
| `n_distinct` | sample | **exact** | distribution; drives a `<= 1` cut |
| `cardinality_ratio` | sample | **exact** | derived from `n_distinct / n_full`; the #876 bug |
| `sample_values` | sample | sample | classification input, and the value of a sample |
| `col_type`, `confidence` | sample | sample | derived from name + values, not from counts |
| `date_parse_rate` | sample | sample (slice 2) | needs the date parser in-engine; defer |

`sample_values` staying sampled is the point, not a compromise: type detection
reads *what values look like*, and 1,000 of them is plenty.

## Interface

`sample_to_driver(spark_df) -> (arrow_table, n_full)` already exists and already
returns the true cluster-side count. Add beside it:

```python
def exact_column_stats(spark_df, columns: list[str]) -> dict[str, ExactStats]:
    """One distributed pass: null count, avg length, distinct count per column."""
```

then merge over the sampled profiles, overwriting only the four fields above.
Classification still runs on the sample; the numbers that get compared to
absolute thresholds become exact.

This composes rather than forks: `profile_columns` keeps its current signature
and single-box behaviour, and the Spark path enriches its output.

## Cost, and the one real decision

Naively this is one pass with `count`, `count(col)`, `avg(length(col))` and
`count_distinct(col)` per column. The first three are free; **`count_distinct`
is a shuffle per column** and is the whole cost.

Proposal: **`approx_count_distinct` (HLL) for every column, exact
`count_distinct` only for columns whose decision sits on a boundary** — i.e.
where the approximate answer is near `<= 1` or near `n_full`. Those are exactly
the two cuts the bugs above turn on, and there are usually few such columns.

This mirrors the pattern `autoconfig.py` already uses for surrogate keys —
sample flags a candidate, an exact pass confirms it — generalised from one field
to four.

## Verification gate

Not "the tests pass". Two specific, already-documented cases must invert:

1. **The #876 shape.** An `email` column with a true ratio of 0.28 must read
   ~0.28 at 100K, 500K, 1M, 2M and 5M, instead of 0.72 → 1.00. The scale
   sensitivity is the thing under test, so a single scale proves nothing.
2. **The #2687 shape.** A column that is 99.99% one value must report its true
   `n_distinct` (2), not 1, so the drop-constant-field rule does not fire on it.

Plus: profiles must be **unchanged** on a frame small enough that the sample is
the whole frame. If exact and sampled disagree there, the exact path is wrong.

## Explicitly out of scope

- The controller loop and its iteration cost.
- Blocking-key selection.
- `REFUSE_AT_N = 100_000` and `ControllerNotConfidentError`. Replacing a refusal
  with a recommendation is a separate, higher-bar change and must not ride in on
  a profiling fix.
- Threshold calibration.

Those all *consume* profiles. Fixing the inputs first means each is evaluated
against numbers that are correct at scale, rather than re-tuned around sampling
artifacts — which is how ~12 rules came to be calibrated against a constant
`mass_above_threshold` (see `project_mass_above_tautology`).

## Risks

- **Recalibration.** Several rules compare these fields to absolute cuts chosen
  while the inputs were sample-derived. Making them truthful may move
  quality-gate scores, exactly as rebasing `mass_above_threshold` moved
  `anchor_person_match` from 1.0000 to 0.7303. Mitigation: land behind a flag,
  default OFF, and run the quality gate on both arms before flipping.
- **`count_distinct` cost on wide frames.** Mitigated by the approx-then-exact
  strategy; if it still bites, cap the exact pass at the top-N candidate columns
  and record which were capped rather than silently sampling.
- **A cluster round trip in a code path that currently has none.** The Spark
  autoconfig module already talks to the cluster, so this adds a pass, not a
  dependency.
