"""Shadow-compute proof for the W6 semantic format-match reuse.

`semantic/classifier.py` `_check_format_match` now shadow-computes the `regex`
kernel (`str_contains_count`) alongside the authoritative Polars
`str.contains(...).sum()` for the three format patterns (email/phone/date),
discarding the kernel result. This test proves the kernel count MATCHES the
Polars count the classifier actually uses -- i.e. the reuse is byte-identical
and ready to become authoritative at a future Flip.

No NEW kernel and no NEW parity contract: `str_contains_count` (the `regex`
component) is already parity-locked since S2.2. The regex kernel IS built on
this branch, so this test should RUN and PASS -- if it skips, the kernel isn't
built. The authoritative semantic classification is UNCHANGED (shadow); the
existing semantic/classifier tests stay green unedited."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck.core._native_loader import native_enabled, native_module

regex_only = pytest.mark.skipif(
    not native_enabled("regex"),
    reason="goldencheck native regex kernel not built/enabled",
)

# The three format patterns, verbatim from `_check_format_match`.
EMAIL_PATTERN = r"@.*\."
PHONE_PATTERN = r"\d{3}.*\d{3}.*\d{4}"
DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"

_EMAIL_COL = pl.Series(
    "email",
    [
        "alice@example.com",
        "bob@work.org",
        "carol@sub.domain.co.uk",
        "not-an-email",  # no @.
        "dave@localhost",  # @ but no dot
        None,
    ],
)
_PHONE_COL = pl.Series(
    "phone",
    [
        "555-123-4567",
        "(212) 867 5309",
        "800.555.0199",
        "12345",  # too short
        "abc-def-ghij",  # no digits
        None,
    ],
)
_DATE_COL = pl.Series(
    "date",
    [
        "2026-07-11",
        "1999-01-01",
        "2000-12-31T23:59:59",
        "07/11/2026",  # wrong shape
        "not a date",
        None,
    ],
)


@regex_only
@pytest.mark.parametrize(
    ("col", "pattern"),
    [
        (_EMAIL_COL, EMAIL_PATTERN),
        (_PHONE_COL, PHONE_PATTERN),
        (_DATE_COL, DATE_PATTERN),
    ],
)
def test_str_contains_count_matches_polars(col: pl.Series, pattern: str) -> None:
    """The regex kernel count == Polars `str.contains(...).sum()` per pattern.

    `_check_format_match` drops nulls before counting, so both the Polars path
    and the kernel count over the same non-null values."""
    non_null = col.drop_nulls()
    polars_count = int(non_null.str.contains(pattern, literal=False).sum())
    kernel_count = native_module().str_contains_count(non_null.to_list(), pattern)
    assert kernel_count == polars_count


@regex_only
def test_all_three_patterns_against_all_cols() -> None:
    """Cross every column against every pattern -- kernel == Polars for all 9."""
    cols = [_EMAIL_COL, _PHONE_COL, _DATE_COL]
    patterns = [EMAIL_PATTERN, PHONE_PATTERN, DATE_PATTERN]
    for col in cols:
        non_null = col.drop_nulls()
        for pattern in patterns:
            polars_count = int(non_null.str.contains(pattern, literal=False).sum())
            kernel_count = native_module().str_contains_count(non_null.to_list(), pattern)
            assert kernel_count == polars_count, (col.name, pattern)
