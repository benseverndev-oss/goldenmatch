from __future__ import annotations

import re
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


@register_transform(
    name="remove_html_tags",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="expr",
)
def remove_html_tags(column: str) -> pl.Expr:
    """Strip HTML tags from text.

    Native Polars regex replace_all. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return pl.col(column).str.replace_all(r"<[^>]+>", "")


@register_transform(
    name="remove_urls",
    input_types=["string"],
    auto_apply=False,
    priority=40,
    mode="expr",
)
def remove_urls(column: str) -> pl.Expr:
    """Strip URLs (http/https) from text.

    Native Polars regex replace_all. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return pl.col(column).str.replace_all(r"https?://\S+", "")


@register_transform(
    name="remove_digits",
    input_types=["string"],
    auto_apply=False,
    priority=35,
    mode="expr",
)
def remove_digits(column: str) -> pl.Expr:
    """Remove all digit characters from text."""
    return pl.col(column).str.replace_all(r"\d", "")


@register_transform(
    name="pad_left",
    input_types=["string"],
    auto_apply=False,
    priority=30,
    mode="series",
)
def pad_left(series: pl.Series, width: int = 10, char: str = "0") -> pl.Series:
    """Left-pad strings to a fixed width."""

    def _pad(val: str | None) -> str | None:
        if val is None:
            return None
        return val.rjust(width, char)

    return series.map_elements(_pad, return_dtype=pl.Utf8)


@register_transform(
    name="pad_right",
    input_types=["string"],
    auto_apply=False,
    priority=30,
    mode="series",
)
def pad_right(series: pl.Series, width: int = 10, char: str = " ") -> pl.Series:
    """Right-pad strings to a fixed width."""

    def _pad(val: str | None) -> str | None:
        if val is None:
            return None
        return val.ljust(width, char)

    return series.map_elements(_pad, return_dtype=pl.Utf8)


_EMOJI_RE = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"  # dingbats
    "\U000024c2-\U0001f251"  # enclosed characters
    "\U0001f900-\U0001f9ff"  # supplemental symbols
    "\U0001fa00-\U0001fa6f"  # chess symbols
    "\U0001fa70-\U0001faff"  # symbols extended-A
    "\U00002600-\U000026ff"  # misc symbols
    "\U0000200d"             # zero-width joiner
    "\U0000fe0f"             # variation selector
    "]+",
    flags=re.UNICODE,
)


@register_transform(
    name="remove_emojis",
    input_types=["string"],
    auto_apply=False,
    priority=38,
    mode="series",
)
def remove_emojis(series: pl.Series) -> pl.Series:
    """Remove emoji characters from text."""

    def _strip(val: str | None) -> str | None:
        if val is None:
            return None
        return _EMOJI_RE.sub("", val)

    return series.map_elements(_strip, return_dtype=pl.Utf8)


@register_transform(
    name="fix_mojibake",
    input_types=["string"],
    auto_apply=False,
    priority=86,
    mode="series",
)
def fix_mojibake(series: pl.Series) -> pl.Series:
    """Fix common UTF-8/Latin-1 mojibake by re-encoding."""

    def _fix(val: str | None) -> str | None:
        if val is None:
            return None
        try:
            return val.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return val

    return series.map_elements(_fix, return_dtype=pl.Utf8)


@register_transform(
    name="normalize_line_endings",
    input_types=["string"],
    auto_apply=False,
    priority=82,
    mode="series",
)
def normalize_line_endings(series: pl.Series) -> pl.Series:
    """Normalize \\r\\n and \\r to \\n."""

    def _norm(val: str | None) -> str | None:
        if val is None:
            return None
        return val.replace("\r\n", "\n").replace("\r", "\n")

    return series.map_elements(_norm, return_dtype=pl.Utf8)


_NUMBER_RE = re.compile(r"\d+\.?\d*")


@register_transform(
    name="extract_numbers",
    input_types=["string"],
    auto_apply=False,
    priority=30,
    mode="expr",
)
def extract_numbers(column: str) -> pl.Expr:
    """Extract all numbers from text, joined by spaces.

    Native Polars: extract_all → list.join. Spec
    docs/superpowers/specs/2026-05-15-map-elements-attack-design.md Tier 1.
    """
    return pl.col(column).str.extract_all(r"\d+\.?\d*").list.join(" ")
