from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform

_TITLES = re.compile(
    r"^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+", re.IGNORECASE
)
_SUFFIXES = re.compile(
    r"\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$", re.IGNORECASE
)
_INITIAL_PATTERN = re.compile(r"\b[A-Z]\.\s")

_MC_PATTERN = re.compile(r"\bMc(\w)")
_O_PATTERN = re.compile(r"\bO'(\w)")


@register_transform(
    name="split_name", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'First Last' into first_name and last_name columns."""
    first_names = []
    last_names = []
    for val in df[column].to_list():
        if val is None:
            first_names.append(None)
            last_names.append(None)
            continue
        parts = val.strip().rsplit(" ", 1)
        if len(parts) == 2:
            first_names.append(parts[0])
            last_names.append(parts[1])
        else:
            first_names.append(parts[0])
            last_names.append("")
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


@register_transform(
    name="split_name_reverse", input_types=["name"], auto_apply=False, priority=50, mode="dataframe"
)
def split_name_reverse(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Split 'Last, First' into first_name and last_name columns."""
    first_names = []
    last_names = []
    for val in df[column].to_list():
        if val is None:
            first_names.append(None)
            last_names.append(None)
            continue
        parts = val.split(",", 1)
        if len(parts) == 2:
            last_names.append(parts[0].strip())
            first_names.append(parts[1].strip())
        else:
            first_names.append(val.strip())
            last_names.append("")
    return df.with_columns(
        pl.Series("first_name", first_names),
        pl.Series("last_name", last_names),
    )


@register_transform(
    name="strip_titles", input_types=["name"], auto_apply=True, priority=70, mode="series"
)
def strip_titles(series: pl.Series) -> pl.Series:
    def _strip(val: str | None) -> str | None:
        if val is None:
            return None
        return _TITLES.sub("", val).strip()

    return series.map_elements(_strip, return_dtype=pl.Utf8)


@register_transform(
    name="strip_suffixes", input_types=["name"], auto_apply=False, priority=60, mode="series"
)
def strip_suffixes(series: pl.Series) -> pl.Series:
    def _strip(val: str | None) -> str | None:
        if val is None:
            return None
        return _SUFFIXES.sub("", val).strip()

    return series.map_elements(_strip, return_dtype=pl.Utf8)


@register_transform(
    name="name_proper", input_types=["name"], auto_apply=False, priority=45, mode="series"
)
def name_proper(series: pl.Series) -> pl.Series:
    def _proper(val: str | None) -> str | None:
        if val is None:
            return None
        result = val.title()
        result = _MC_PATTERN.sub(lambda m: f"Mc{m.group(1).upper()}", result)
        result = _O_PATTERN.sub(lambda m: f"O'{m.group(1).upper()}", result)
        return result

    return series.map_elements(_proper, return_dtype=pl.Utf8)


@register_transform(
    name="initial_expand", input_types=["name"], auto_apply=False, priority=40, mode="series"
)
def initial_expand(series: pl.Series) -> tuple[pl.Series, list[int]]:
    """Returns (series, flagged_rows). Values with initials are unchanged but flagged."""
    flagged: list[int] = []
    for i, val in enumerate(series.to_list()):
        if val and _INITIAL_PATTERN.search(val):
            flagged.append(i)
    return series, flagged
