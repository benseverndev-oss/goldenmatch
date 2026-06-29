"""Per-value relative-frequency tables shared by the FS TF-adjustment and the
weighted name scorer's data-driven downweight (#1207 PR2a)."""
from __future__ import annotations

import polars as pl


def value_frequencies(
    df: pl.DataFrame, field: str, transforms: list[str] | None = None,
) -> dict[str, float]:
    """Relative frequency of each transformed non-empty value in ``field``.

    Mirrors the counting in probabilistic._build_tf_tables: applies the same
    transforms, drops None/empty, returns {value -> count/total}. Empty dict
    when the column is absent or all-empty."""
    from goldenmatch.utils.transforms import apply_transforms

    if field not in df.columns:
        return {}
    counts: dict[str, int] = {}
    total = 0
    for v in df[field].to_list():
        if v is None:
            continue
        s = str(v)
        if transforms:
            s = apply_transforms(s, transforms)
        if s is None or s == "":
            continue
        counts[s] = counts.get(s, 0) + 1
        total += 1
    if total == 0:
        return {}
    return {val: c / total for val, c in counts.items()}
