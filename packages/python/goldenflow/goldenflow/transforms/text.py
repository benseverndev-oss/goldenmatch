from __future__ import annotations

import unicodedata

import polars as pl

from goldenflow.transforms import register_transform


@register_transform(name="strip", input_types=["string"], auto_apply=True, priority=90, mode="expr")
def strip(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars()


@register_transform(
    name="lowercase", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def lowercase(column: str) -> pl.Expr:
    return pl.col(column).str.to_lowercase()


@register_transform(
    name="uppercase", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def uppercase(column: str) -> pl.Expr:
    return pl.col(column).str.to_uppercase()


@register_transform(
    name="title_case", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def title_case(column: str) -> pl.Expr:
    return pl.col(column).str.to_titlecase()


@register_transform(
    name="normalize_unicode", input_types=["string"], auto_apply=True, priority=85, mode="series"
)
def normalize_unicode(series: pl.Series) -> pl.Series:
    def _normalize(val: str | None) -> str | None:
        if val is None:
            return None
        nfkd = unicodedata.normalize("NFKD", val)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    return series.map_elements(_normalize, return_dtype=pl.Utf8)


@register_transform(
    name="remove_punctuation", input_types=["string"], auto_apply=False, priority=40, mode="expr"
)
def remove_punctuation(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(r"[^a-zA-Z0-9\s]", "")


@register_transform(
    name="collapse_whitespace", input_types=["string"], auto_apply=True, priority=80, mode="expr"
)
def collapse_whitespace(column: str) -> pl.Expr:
    return pl.col(column).str.replace_all(r"\s{2,}", " ")


@register_transform(
    name="truncate", input_types=["string"], auto_apply=False, priority=30, mode="series"
)
def truncate(series: pl.Series, n: int = 255) -> pl.Series:
    return series.str.slice(0, n)


@register_transform(
    name="normalize_quotes",
    input_types=["string"],
    auto_apply=True,
    priority=84,
    mode="expr",
)
def normalize_quotes(column: str) -> pl.Expr:
    """Replace smart/curly quotes with straight quotes."""
    return (
        pl.col(column)
        .str.replace_all("\u201c", '"')   # left double
        .str.replace_all("\u201d", '"')   # right double
        .str.replace_all("\u2018", "'")   # left single
        .str.replace_all("\u2019", "'")   # right single
        .str.replace_all("\u2033", '"')   # double prime
        .str.replace_all("\u2032", "'")   # prime
    )
