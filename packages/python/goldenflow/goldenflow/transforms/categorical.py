from __future__ import annotations

import polars as pl

from goldenflow.transforms import register_transform

_TRUE_VALUES = {"yes", "y", "1", "true", "t"}
_FALSE_VALUES = {"no", "n", "0", "false", "f"}
_NULL_VALUES = {"n/a", "null", "none", "na", "nil", "nan", "-", ""}


@register_transform(
    name="boolean_normalize", input_types=["boolean", "string"], auto_apply=False, priority=50, mode="series"
)
def boolean_normalize(series: pl.Series) -> pl.Series:
    def _norm(val: str | None) -> bool | None:
        if val is None:
            return None
        v = val.strip().lower()
        if v in _TRUE_VALUES:
            return True
        if v in _FALSE_VALUES:
            return False
        return None

    return series.map_elements(_norm, return_dtype=pl.Boolean)


@register_transform(
    name="gender_standardize", input_types=["string"], auto_apply=False, priority=50, mode="series"
)
def gender_standardize(series: pl.Series) -> pl.Series:
    _map = {"male": "M", "m": "M", "female": "F", "f": "F"}

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        return _map.get(val.strip().lower(), val)

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="null_standardize", input_types=["string"], auto_apply=True, priority=80, mode="series"
)
def null_standardize(series: pl.Series) -> pl.Series:
    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        if val.strip().lower() in _NULL_VALUES:
            return None
        return val

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="category_standardize",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def category_standardize(
    series: pl.Series, mapping: dict[str, list[str]] | None = None
) -> pl.Series:
    """Map variant values to canonical values. mapping: {canonical: [variant1, variant2, ...]}"""
    if not mapping:
        return series
    lookup: dict[str, str] = {}
    for canonical, variants in mapping.items():
        for v in variants:
            lookup[v.lower()] = canonical

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        return lookup.get(val.strip().lower(), val)

    return series.map_elements(_std, return_dtype=pl.Utf8)


@register_transform(
    name="category_from_file",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="series",
)
def category_from_file(
    series: pl.Series, lookup_path: str | None = None
) -> pl.Series:
    """Load mapping from a CSV/YAML file and standardize values.
    CSV must have columns: variant, canonical."""
    if not lookup_path:
        return series
    from pathlib import Path
    p = Path(lookup_path)
    if p.suffix == ".csv":
        import polars as pl_inner
        lookup_df = pl_inner.read_csv(p)
        mapping: dict[str, str] = {}
        for row in lookup_df.iter_rows(named=True):
            mapping[row["variant"].lower()] = row["canonical"]
    elif p.suffix in (".yaml", ".yml"):
        import yaml
        with open(p) as f:
            raw = yaml.safe_load(f) or {}
        mapping = {}
        for canonical, variants in raw.items():
            for v in variants:
                mapping[v.lower()] = canonical
    else:
        return series

    def _std(val: str | None) -> str | None:
        if val is None:
            return None
        return mapping.get(val.strip().lower(), val)

    return series.map_elements(_std, return_dtype=pl.Utf8)
