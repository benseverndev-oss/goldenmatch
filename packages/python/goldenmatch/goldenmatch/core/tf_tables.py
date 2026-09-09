"""Per-value relative-frequency tables shared by the FS TF-adjustment and the
weighted name scorer's data-driven downweight (#1207 PR2a)."""
from __future__ import annotations

from typing import Any

from goldenmatch._polars_lazy import pl  # noqa: F401  (docstring type refs)


def value_frequencies(
    df: Any, field: str, transforms: list[str] | None = None,
) -> dict[str, float]:
    """Relative frequency of each transformed non-empty value in ``field``.

    Mirrors the counting in probabilistic._build_tf_tables: applies the same
    transforms, drops None/empty, returns {value -> count/total}. Empty dict
    when the column is absent or all-empty."""
    from goldenmatch.core.frame import to_frame
    from goldenmatch.utils.transforms import apply_transforms

    # Engine-agnostic on purpose. `field not in df.columns` reads column
    # NAMES on a polars frame and column ARRAYS on a pyarrow.Table, so the
    # polars-era form was always true once Arrow reached here -- every
    # field read as absent, the caller got an empty table, and FS TF
    # adjustment silently did nothing. Nothing raised; a full-panel A/B
    # came back +0.0000 on every dataset (fs-lever-gate 34302210694).
    frame = to_frame(df)
    if field not in frame.columns:
        return {}
    counts: dict[str, int] = {}
    total = 0
    for v in frame.column(field).to_list():
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
