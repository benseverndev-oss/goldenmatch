# Passing an explicit GoldenMatchConfig

When auto-config raises `ControllerNotConfidentError`, the controller has determined that its sample-iteration cannot find a healthy config for your data. The recovery is to pass an explicit `GoldenMatchConfig` describing your blocking keys and matchkey fields.

## Quick template

```python
from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

cfg = GoldenMatchConfig(
    matchkeys=[
        MatchkeyConfig(
            name="default",
            type="weighted",
            threshold=0.85,
            fields=[
                MatchkeyField(field="last_name", scorer="jaro_winkler", weight=2.0),
                MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.5),
                MatchkeyField(field="zip", scorer="exact", weight=1.0),
            ],
        ),
    ],
    blocking=BlockingConfig(
        keys=[
            BlockingKeyConfig(fields=["last_name_soundex"]),
            BlockingKeyConfig(fields=["zip"]),
        ],
        max_block_size=1000,
    ),
)

result = dedupe_df(df, config=cfg)
```

## Diagnosing which sub-profile failed

`ControllerNotConfidentError.failing_sub_profile` is the lead diagnostic. Common patterns:

| `failing_sub_profile` | Likely cause | Try |
|---|---|---|
| `data` | All-null column; single-column input; n_rows == 0 | Inspect `df.describe()`; ensure at least two non-null columns |
| `blocking` | No blocking key reduces comparison space | Identify a column that's neither unique-per-row (no reduction) nor low-cardinality (mega-blocks). Soundex/metaphone of names usually works |
| `scoring` | Sample's matchkey threshold matches nothing | Lower threshold (0.85 → 0.70); add a transform to normalize the matchkey field |
| `matchkey` | Matchkey field is near-100%-unique (every row is its own cluster) | Pick a less-discriminative matchkey or add a fuzzy weight |
| `cluster` | Cluster output has one giant component | Add a stricter blocking key; the existing blocking is too permissive |

## Opting out

If you understand the controller is committing a noisy config and you want to run it anyway:

```python
result = dedupe_df(df, confidence_required=False)  # warn-and-run
```

This restores the pre-2026-05-16 behavior: a `DedupeResult` is returned, the controller logs a `WARNING` line about the RED commit, but the pipeline runs to completion (typically with mostly-noise output on large inputs).
