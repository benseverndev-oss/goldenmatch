# Flip §8b Differential — Stage 0 (seam-clean column profilers)

Authoritative = seam profilers over a `PolarsFrame` (2.x-Polars).  
Fused = the SAME profilers over an `ArrowFrame` (owned Arrow-native seam).

Profilers exercised: TypeInference, Nullability, Uniqueness, RangeDistribution, Cardinality, SequenceDetection, Freshness.
## Overall verdict

- STRICT (non-stat/non-dtype) Jaccard = 1.000 (PASS iff 1.000)  ->  PASS
- dtype-vocab diffs (expected, raw-polars vs neutral): **27**
- max stat-float delta across all datasets: **0.000e+00**
- strict-set divergences: **0** (must be 0)


## age_dob

- full finding-set Jaccard (with sample_values): **1.000** (3/3)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (3/3)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| nullability | 1 | 2 | 2 | +0 |
| range_distribution | 1 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| age | `Int64` | `int` |
| date_of_birth | `Date` | `date` |

## benford

- full finding-set Jaccard (with sample_values): **1.000** (7/7)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (7/7)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| nullability | 1 | 2 | 2 | +0 |
| range_distribution | 1 | 2 | 2 | +0 |
| range_distribution | 2 | 1 | 1 | +0 |
| type_inference | 2 | 1 | 1 | +0 |
| uniqueness | 2 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| amount | `Int64` | `int` |
| flat_id | `Int64` | `int` |

## correlation

- full finding-set Jaccard (with sample_values): **1.000** (16/16)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (16/16)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| cardinality | 1 | 2 | 2 | +0 |
| nullability | 1 | 5 | 5 | +0 |
| range_distribution | 1 | 3 | 3 | +0 |
| range_distribution | 2 | 3 | 3 | +0 |
| uniqueness | 1 | 3 | 3 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| var_a | `Float64` | `float` |
| var_b | `Float64` | `float` |
| var_c | `Float64` | `float` |
| cat1 | `String` | `str` |
| cat2 | `String` | `str` |

## duplicates

- full finding-set Jaccard (with sample_values): **1.000** (5/5)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (5/5)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| cardinality | 1 | 2 | 2 | +0 |
| nullability | 1 | 3 | 3 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| first_name | `String` | `str` |
| city | `String` | `str` |
| email | `String` | `str` |

## freshness

- full finding-set Jaccard (with sample_values): **1.000** (2/2)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (2/2)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| nullability | 1 | 1 | 1 | +0 |
| stale_data | 1 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| event_date | `Date` | `date` |

## large_sampled

- full finding-set Jaccard (with sample_values): **1.000** (12/12)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (12/12)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| cardinality | 1 | 1 | 1 | +0 |
| nullability | 1 | 4 | 4 | +0 |
| range_distribution | 1 | 3 | 3 | +0 |
| range_distribution | 2 | 2 | 2 | +0 |
| type_inference | 2 | 1 | 1 | +0 |
| uniqueness | 1 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| measure | `Float64` | `float` |
| region | `String` | `str` |
| amount | `Int64` | `int` |
| rec_id | `Int64` | `int` |

## mixed_dtypes

- full finding-set Jaccard (with sample_values): **1.000** (15/15)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (15/15)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| cardinality | 1 | 2 | 2 | +0 |
| nullability | 1 | 6 | 6 | +0 |
| range_distribution | 1 | 4 | 4 | +0 |
| range_distribution | 2 | 1 | 1 | +0 |
| type_inference | 2 | 1 | 1 | +0 |
| uniqueness | 1 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| i8 | `Int8` | `int` |
| u32 | `UInt32` | `uint` |
| f32 | `Float32` | `float` |
| flag | `Boolean` | `bool` |
| label | `String` | `str` |
| maybe_num | `String` | `str` |

## numeric_outliers

- full finding-set Jaccard (with sample_values): **1.000** (9/9)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (9/9)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| nullability | 1 | 3 | 3 | +0 |
| range_distribution | 1 | 3 | 3 | +0 |
| range_distribution | 2 | 2 | 2 | +0 |
| uniqueness | 1 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| score | `Float64` | `float` |
| noise | `Float64` | `float` |
| count | `Int64` | `int` |

## sequence_gaps

- full finding-set Jaccard (with sample_values): **1.000** (5/5)
- STRICT (non-sample/non-dtype) Jaccard: **1.000** (5/5)
- max stat-float delta: **0.000e+00**

### finding count per (check, severity)

| check | severity | authoritative | fused | delta |
|---|---|---|---|---|
| nullability | 1 | 1 | 1 | +0 |
| range_distribution | 1 | 1 | 1 | +0 |
| sequence_detection | 2 | 1 | 1 | +0 |
| type_inference | 2 | 1 | 1 | +0 |
| uniqueness | 1 | 1 | 1 | +0 |

### dtype vocabulary (expected raw-polars vs neutral divergence)

| column | authoritative repr | fused repr |
|---|---|---|
| row_id | `Int64` | `int` |
