# GoldenMatch config folder

Copy-paste-ready configs you can point at your own data. Start from the one
closest to your job, change the column names, and run.

| File | Use it when |
| --- | --- |
| [`customers.yaml`](customers.yaml) | Single-node dedupe of customers / contacts / accounts. The robust default starting point. |
| [`distributed-100m.yaml`](distributed-100m.yaml) | 100M+ rows on a Ray cluster. The matching policy plus the env-var + cluster recipe. |

> **You often need no config at all.** `goldenmatch dedupe data.csv` auto-tunes
> blocking, matchkeys, and thresholds, and tells you when it is unsure. Reach
> for a config when you want a *pinned, reviewable* matching policy, or to
> override one auto-tuned decision.

## Run it

```bash
# Single node
goldenmatch dedupe customers.csv --config configs/customers.yaml

# From Python
from goldenmatch import dedupe
result = dedupe("customers.csv", config="configs/customers.yaml")
print(result.golden)        # canonical records
print(result.clusters)      # who merged with whom
```

## Anatomy of a config

Every section is optional. A config is a YAML mapping with these top-level keys:

### `matchkeys` — the rules that declare two records "the same"

Records merge if **any** matchkey fires. Order cheap+precise first.

```yaml
matchkeys:
  - name: email_exact          # exact match on a normalized field
    type: exact
    fields:
      - column: email
        transforms: [lowercase, strip]

  - name: name_fuzzy           # fuzzy match; weighted needs a threshold
    type: weighted
    threshold: 0.88            # pair merges when the weighted score >= this
    fields:
      - column: first_name
        scorer: jaro_winkler   # see "Scorers" below
        weight: 1.0
      - column: last_name
        scorer: jaro_winkler
        weight: 1.5            # surname agreement counts more
```

- `type: exact` — binary equality on the (transformed) field. No scorer/weight.
- `type: weighted` — per-field fuzzy scores combined by `weight`; merge when the
  combined score crosses `threshold` (**required** for weighted).
- `column` (or `field`) — the input column. `transforms` normalize it first.

**Scorers** (common): `jaro_winkler` (names), `levenshtein` /
`token_sort_ratio` (free text), `exact`, `jaccard` (sets/addresses),
`embedding` (semantic). **Transforms** (common): `lowercase`, `strip`,
`name_proper`, `email`, `phone`, `zip5`, `substring:start:end`.

### `blocking` — who gets compared to whom (the scale knob)

Without blocking, matching is O(n^2). A blocking key buckets records so only
records sharing a key are scored. Tune for recall (bigger buckets) vs speed
(smaller buckets).

```yaml
blocking:
  max_block_size: 5000           # skip pathologically huge buckets
  skip_oversized: true
  keys:
    - fields: [last_name]
      transforms: [lowercase, "substring:0:3"]   # block on first 3 letters
```

### `standardization` — clean fields before matching

```yaml
standardization:
  email: [email]
  last_name: [strip, name_proper]
  zip: [zip5]
```

### `golden_rules` — collapse a cluster into one canonical row

```yaml
golden_rules:
  default_strategy: most_complete   # keep the most-populated/cleanest value per field
```

### `output`

```yaml
output:
  format: csv          # csv | parquet
  directory: ./goldenmatch_out
  run_name: customers
```

## Tuning, in order of impact

1. **Blocking selectivity.** This decides scale. Too-broad keys blow up pair
   counts; too-narrow keys miss true duplicates. Block on the most selective
   field that still co-locates duplicates.
2. **Matchkey threshold.** Lower = more recall (more merges, more false
   merges); higher = more precision. Names usually sit at 0.85-0.92.
3. **Add a second matchkey** for an independent signal (e.g. exact phone) before
   loosening a threshold.

## Scaling to 100M+

The matching policy is the same; the *invocation* changes. See
[`distributed-100m.yaml`](distributed-100m.yaml) for the full recipe:
`GOLDENMATCH_DISTRIBUTED_PIPELINE=2` over a Ray cluster (head as a pure driver,
workers do all compute), with a global `__row_id__` on the input. Verified at
100,000,000 rows in ~213 s with a 0.30 GB driver footprint. Runnable demo:
[`examples/distributed_pipeline.py`](../examples/distributed_pipeline.py).
