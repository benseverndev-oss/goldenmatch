from __future__ import annotations

import re

import polars as pl

from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    collapse_whitespace_native,
    extract_numbers_native,
    fix_mojibake_native,
    lowercase_native,
    normalize_line_endings_native,
    normalize_quotes_native,
    normalize_unicode_native,
    pad_left_native,
    pad_right_native,
    remove_digits_native,
    remove_emojis_native,
    remove_html_tags_native,
    remove_punctuation_native,
    remove_urls_native,
    strip_native,
    title_case_native,
    truncate_native,
    uppercase_native,
)
from goldenflow.transforms._normalize_unicode_map import NORMALIZE_MAP

# --- Wave D text-1: mechanical / ASCII-bound transforms migrated to owned
# goldenflow-core::text kernels (native-first + byte-matched pure-Python
# fallback = the reference under reference-mode). All are mode="expr",
# dispatched via map_batches so the func(column)->Expr signature is unchanged.
# The pure-Python fallbacks reproduce the Rust kernel bytes; where a kernel is
# ASCII-bounded (remove_digits/extract_numbers), the fallback uses an explicit
# ASCII char class ([0-9]) NOT `\d`, to match the kernel not the old polars.


def _strip_py(val: str | None) -> str | None:
    if val is None:
        return None
    return val.strip()


def _strip_series(series: pl.Series) -> pl.Series:
    native = strip_native()
    if native is not None:
        return native(series)
    return series.map_elements(_strip_py, return_dtype=pl.Utf8)


@register_transform(name="strip", input_types=["string"], auto_apply=True, priority=90, mode="expr")
def strip(column: str) -> pl.Expr:
    return pl.col(column).map_batches(_strip_series, return_dtype=pl.Utf8)


def _collapse_whitespace_py(val: str | None) -> str | None:
    if val is None:
        return None
    return re.sub(r"\s{2,}", " ", val)


def _collapse_whitespace_series(series: pl.Series) -> pl.Series:
    native = collapse_whitespace_native()
    if native is not None:
        return native(series)
    return series.map_elements(_collapse_whitespace_py, return_dtype=pl.Utf8)


@register_transform(
    name="collapse_whitespace", input_types=["string"], auto_apply=True, priority=80, mode="expr"
)
def collapse_whitespace(column: str) -> pl.Expr:
    return pl.col(column).map_batches(_collapse_whitespace_series, return_dtype=pl.Utf8)


def _normalize_quotes_py(val: str | None) -> str | None:
    if val is None:
        return None
    return (
        val.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("″", '"')
        .replace("′", "'")
    )


def _normalize_quotes_series(series: pl.Series) -> pl.Series:
    native = normalize_quotes_native()
    if native is not None:
        return native(series)
    return series.map_elements(_normalize_quotes_py, return_dtype=pl.Utf8)


@register_transform(
    name="normalize_quotes", input_types=["string"], auto_apply=True, priority=84, mode="expr"
)
def normalize_quotes(column: str) -> pl.Expr:
    """Replace smart/curly quotes with straight quotes."""
    return pl.col(column).map_batches(_normalize_quotes_series, return_dtype=pl.Utf8)


def _normalize_line_endings_py(val: str | None) -> str | None:
    if val is None:
        return None
    return val.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_line_endings_series(series: pl.Series) -> pl.Series:
    native = normalize_line_endings_native()
    if native is not None:
        return native(series)
    return series.map_elements(_normalize_line_endings_py, return_dtype=pl.Utf8)


@register_transform(
    name="normalize_line_endings", input_types=["string"], auto_apply=False, priority=82,
    mode="expr",
)
def normalize_line_endings(column: str) -> pl.Expr:
    r"""Normalize \r\n and \r to \n."""
    return pl.col(column).map_batches(_normalize_line_endings_series, return_dtype=pl.Utf8)


def _remove_html_tags_py(val: str | None) -> str | None:
    if val is None:
        return None
    return re.sub(r"<[^>]+>", "", val)


def _remove_html_tags_series(series: pl.Series) -> pl.Series:
    native = remove_html_tags_native()
    if native is not None:
        return native(series)
    return series.map_elements(_remove_html_tags_py, return_dtype=pl.Utf8)


@register_transform(
    name="remove_html_tags", input_types=["string"], auto_apply=False, priority=45, mode="expr"
)
def remove_html_tags(column: str) -> pl.Expr:
    """Strip HTML tags from text."""
    return pl.col(column).map_batches(_remove_html_tags_series, return_dtype=pl.Utf8)


def _remove_urls_py(val: str | None) -> str | None:
    if val is None:
        return None
    return re.sub(r"https?://\S+", "", val)


def _remove_urls_series(series: pl.Series) -> pl.Series:
    native = remove_urls_native()
    if native is not None:
        return native(series)
    return series.map_elements(_remove_urls_py, return_dtype=pl.Utf8)


@register_transform(
    name="remove_urls", input_types=["string"], auto_apply=False, priority=40, mode="expr"
)
def remove_urls(column: str) -> pl.Expr:
    """Strip URLs (http/https) from text."""
    return pl.col(column).map_batches(_remove_urls_series, return_dtype=pl.Utf8)


def _remove_digits_py(val: str | None) -> str | None:
    if val is None:
        return None
    # ASCII-bounded to match the Rust kernel (NOT `\d`, which is Unicode-aware).
    return re.sub(r"[0-9]", "", val)


def _remove_digits_series(series: pl.Series) -> pl.Series:
    native = remove_digits_native()
    if native is not None:
        return native(series)
    return series.map_elements(_remove_digits_py, return_dtype=pl.Utf8)


@register_transform(
    name="remove_digits", input_types=["string"], auto_apply=False, priority=35, mode="expr"
)
def remove_digits(column: str) -> pl.Expr:
    """Remove all digit characters from text."""
    return pl.col(column).map_batches(_remove_digits_series, return_dtype=pl.Utf8)


def _remove_punctuation_py(val: str | None) -> str | None:
    if val is None:
        return None
    return re.sub(r"[^a-zA-Z0-9\s]", "", val)


def _remove_punctuation_series(series: pl.Series) -> pl.Series:
    native = remove_punctuation_native()
    if native is not None:
        return native(series)
    return series.map_elements(_remove_punctuation_py, return_dtype=pl.Utf8)


@register_transform(
    name="remove_punctuation", input_types=["string"], auto_apply=False, priority=40, mode="expr"
)
def remove_punctuation(column: str) -> pl.Expr:
    return pl.col(column).map_batches(_remove_punctuation_series, return_dtype=pl.Utf8)


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


def _remove_emojis_py(val: str | None) -> str | None:
    if val is None:
        return None
    return _EMOJI_RE.sub("", val)


def _remove_emojis_series(series: pl.Series) -> pl.Series:
    native = remove_emojis_native()
    if native is not None:
        return native(series)
    return series.map_elements(_remove_emojis_py, return_dtype=pl.Utf8)


@register_transform(
    name="remove_emojis", input_types=["string"], auto_apply=False, priority=38, mode="expr"
)
def remove_emojis(column: str) -> pl.Expr:
    """Remove emoji characters from text."""
    return pl.col(column).map_batches(_remove_emojis_series, return_dtype=pl.Utf8)


def _extract_numbers_py(val: str | None) -> str | None:
    if val is None:
        return None
    # ASCII-bounded to match the Rust kernel (NOT `\d`).
    return " ".join(re.findall(r"[0-9]+\.?[0-9]*", val))


def _extract_numbers_series(series: pl.Series) -> pl.Series:
    native = extract_numbers_native()
    if native is not None:
        return native(series)
    return series.map_elements(_extract_numbers_py, return_dtype=pl.Utf8)


@register_transform(
    name="extract_numbers", input_types=["string"], auto_apply=False, priority=30, mode="expr"
)
def extract_numbers(column: str) -> pl.Expr:
    """Extract all numbers from text, joined by spaces."""
    return pl.col(column).map_batches(_extract_numbers_series, return_dtype=pl.Utf8)


# --- parameterized text transforms (truncate / pad_left / pad_right). The
# per-column-constant params are captured into the map_batches closure; the
# non-default-param behavior is pinned in tests/transforms/test_text_kernels.py
# (the numeric round/clamp precedent), so these don't sit in the shared corpus.


def _truncate_py(val: str | None, n: int) -> str | None:
    if val is None:
        return None
    return val[:n]


@register_transform(name="truncate", input_types=["string"], auto_apply=False, priority=30,
                    mode="expr")
def truncate(column: str, n: int | str = 255) -> pl.Expr:
    """Truncate string to the first n characters.

    Native-first (goldenflow-core's ``text::truncate`` kernel); the pure-Python
    fallback is the byte-exact reference. Accepts str or int for ``n`` (the
    engine passes config params as strings).
    """
    nn = int(n)

    def _series(series: pl.Series) -> pl.Series:
        native = truncate_native(nn)
        if native is not None:
            return native(series)
        return series.map_elements(lambda v: _truncate_py(v, nn), return_dtype=pl.Utf8)

    return pl.col(column).map_batches(_series, return_dtype=pl.Utf8)


def _pad_left_py(val: str | None, width: int, pad: str) -> str | None:
    if val is None:
        return None
    return val.rjust(width, pad)


@register_transform(name="pad_left", input_types=["string"], auto_apply=False, priority=30,
                    mode="expr")
def pad_left(column: str, width: int | str = 10, char: str = "0") -> pl.Expr:
    """Left-pad strings to a fixed width.

    Native-first (goldenflow-core's ``text::pad_left`` kernel); the pure-Python
    fallback is the byte-exact reference.
    """
    w = int(width)

    def _series(series: pl.Series) -> pl.Series:
        native = pad_left_native(w, char)
        if native is not None:
            return native(series)
        return series.map_elements(lambda v: _pad_left_py(v, w, char), return_dtype=pl.Utf8)

    return pl.col(column).map_batches(_series, return_dtype=pl.Utf8)


def _pad_right_py(val: str | None, width: int, pad: str) -> str | None:
    if val is None:
        return None
    return val.ljust(width, pad)


@register_transform(name="pad_right", input_types=["string"], auto_apply=False, priority=30,
                    mode="expr")
def pad_right(column: str, width: int | str = 10, char: str = " ") -> pl.Expr:
    """Right-pad strings to a fixed width.

    Native-first (goldenflow-core's ``text::pad_right`` kernel); the pure-Python
    fallback is the byte-exact reference.
    """
    w = int(width)

    def _series(series: pl.Series) -> pl.Series:
        native = pad_right_native(w, char)
        if native is not None:
            return native(series)
        return series.map_elements(lambda v: _pad_right_py(v, w, char), return_dtype=pl.Utf8)

    return pl.col(column).map_batches(_series, return_dtype=pl.Utf8)


# --- Wave D text-2: Unicode-heavy transforms migrated to owned goldenflow-core
# kernels (native-first + byte-matched pure-Python fallback). Casing uses Rust
# std / Python str (agree on Latin/ASCII; exotic casing is the documented
# boundary); title_case is ASCII-title; normalize_unicode uses the SAME explicit
# generated NORMALIZE_MAP as the kernel (NOT unicodedata); fix_mojibake is a
# portable latin-1<->utf-8 round-trip.


def _lowercase_py(val: str | None) -> str | None:
    if val is None:
        return None
    return val.lower()


def _lowercase_series(series: pl.Series) -> pl.Series:
    native = lowercase_native()
    if native is not None:
        return native(series)
    return series.map_elements(_lowercase_py, return_dtype=pl.Utf8)


@register_transform(
    name="lowercase", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def lowercase(column: str) -> pl.Expr:
    return pl.col(column).map_batches(_lowercase_series, return_dtype=pl.Utf8)


def _uppercase_py(val: str | None) -> str | None:
    if val is None:
        return None
    return val.upper()


def _uppercase_series(series: pl.Series) -> pl.Series:
    native = uppercase_native()
    if native is not None:
        return native(series)
    return series.map_elements(_uppercase_py, return_dtype=pl.Utf8)


@register_transform(
    name="uppercase", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def uppercase(column: str) -> pl.Expr:
    return pl.col(column).map_batches(_uppercase_series, return_dtype=pl.Utf8)


def _title_case_py(val: str | None) -> str | None:
    """ASCII title-case, byte-identical to goldenflow-core ``names::ascii_title``
    (reused by the ``title_case`` kernel): first alphabetic char of each word
    upper, rest lower; non-alpha resets the word."""
    if val is None:
        return None
    out: list[str] = []
    prev_alpha = False
    for c in val:
        if c.isalpha():
            out.append(c.lower() if prev_alpha else c.upper())
            prev_alpha = True
        else:
            out.append(c)
            prev_alpha = False
    return "".join(out)


def _title_case_series(series: pl.Series) -> pl.Series:
    native = title_case_native()
    if native is not None:
        return native(series)
    return series.map_elements(_title_case_py, return_dtype=pl.Utf8)


@register_transform(
    name="title_case", input_types=["string"], auto_apply=False, priority=50, mode="expr"
)
def title_case(column: str) -> pl.Expr:
    return pl.col(column).map_batches(_title_case_series, return_dtype=pl.Utf8)


_NON_ASCII_RE = r"[^\x00-\x7F]"


def _normalize_unicode_py(val: str | None) -> str | None:
    """NFKD-decompose + strip-combining via the explicit ``NORMALIZE_MAP`` (NOT
    ``unicodedata`` -- the map is byte-identical to the Rust kernel across
    surfaces). ASCII passes through; a mapped char emits its replacement; an
    unmapped non-ASCII char passes through unchanged (documented boundary)."""
    if val is None:
        return None
    out: list[str] = []
    for c in val:
        if ord(c) < 128:
            out.append(c)
        else:
            out.append(NORMALIZE_MAP.get(c, c))
    return "".join(out)


@register_transform(
    name="normalize_unicode", input_types=["string"], auto_apply=True, priority=85, mode="series"
)
def normalize_unicode(series: pl.Series) -> pl.Series:
    # Column-level ASCII fast-path: a pure-ASCII column is a no-op (kept from the
    # original for perf -- this transform was a top wall slice at 10M rows). The
    # native kernel handles ASCII per-char too; this bails before any per-row work.
    if series.dtype != pl.Utf8:
        return series
    non_null = series.drop_nulls()
    if non_null.len() == 0:
        return series
    if not bool(non_null.str.contains(_NON_ASCII_RE).any()):
        return series
    native = normalize_unicode_native()
    if native is not None:
        return native(series)
    return series.map_elements(_normalize_unicode_py, return_dtype=pl.Utf8)


def _fix_mojibake_py(val: str | None) -> str | None:
    if val is None:
        return None
    try:
        return val.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return val


@register_transform(
    name="fix_mojibake",
    input_types=["string"],
    auto_apply=False,
    priority=86,
    mode="series",
)
def fix_mojibake(series: pl.Series) -> pl.Series:
    """Fix common UTF-8/Latin-1 mojibake by re-encoding.

    Native-first (goldenflow-core's ``text::fix_mojibake``); the pure-Python
    fallback is the byte-exact reference.
    """
    native = fix_mojibake_native()
    if native is not None:
        return native(series)
    return series.map_elements(_fix_mojibake_py, return_dtype=pl.Utf8)
