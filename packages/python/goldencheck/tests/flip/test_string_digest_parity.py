"""Phase-1 fused string-digest parity gate.

The fused ``string_column_digest`` kernel folds ~10 per-column string passes
(``null_count`` + ``n_unique`` + the 7 fixed ``str_match_count`` scans) into ONE
Rust pass. This asserts it is byte-identical to the per-method path:

- ``match_counts[i]`` == the regex-crate ``str_contains_count(patterns[i])`` (the
  ground truth the ``str_match_count`` fallback uses) for each of the 7 patterns.
- ``n_unique`` == pyarrow ``count_distinct(mode="all")`` (nulls = one distinct).
- ``null_count`` == ``arr.null_count``.

Also checks the ``ArrowColumn`` wiring: the digest fast path returns the same
counts as the pyarrow/kernel fallback, computes the digest exactly once, and
opportunistically feeds ``n_unique``/``null_count``.
"""
from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")
pc = pytest.importorskip("pyarrow.compute")

from goldencheck.core._native_loader import native_enabled, native_module  # noqa: E402
from goldencheck.core.frame import _SCAN_STRING_PATTERNS, ArrowColumn  # noqa: E402

# Skip cleanly when the native kernel isn't built (the fused path is an optional
# accelerator; the fallback is covered by the existing pyarrow parity suite).
pytestmark = pytest.mark.skipif(
    not native_enabled("string_digest"),
    reason="goldencheck._native.string_column_digest not built/enabled",
)

# Tricky code points built via chr() so the source stays plain-ASCII.
_ZW = chr(0x200B)      # zero-width space
_FEFF = chr(0xFEFF)    # zero-width no-break space (BOM)
_RSQUO = chr(0x2019)   # right single quote (smart quote)
_LDQUO = chr(0x201C)   # left double quote (smart quote)
_EACUTE = chr(0x00E9)  # é (non-ascii)
_BELL = chr(0x0007)    # bell (control char)


def _corpus() -> list[str | None]:
    return [
        "alice@example.com",
        "bob.smith@sub.domain.co",
        "not-an-email",
        "(212) 555-0198",
        "212.555.0198",
        "5550198",  # too short -> not a phone
        "https://example.com/path",
        "http://x.io",
        "ftp://nope",  # not http(s)
        "plain text",
        "caf" + _EACUTE,             # non-ascii
        "zero" + _ZW + "width",      # zero-width + non-ascii
        "bom" + _FEFF,               # BOM + non-ascii
        "smart" + _RSQUO + "quote",  # smart quote + non-ascii
        "quote" + _LDQUO + "d",      # smart quote + non-ascii
        "ctrl" + _BELL + "char",     # control char
        None,
        None,
        "alice@example.com",  # duplicate of row 0
    ]


@pytest.mark.parametrize("large", [False, True])
def test_digest_matches_per_method_path(large: bool) -> None:
    vals = _corpus()
    typ = pa.large_string() if large else pa.string()
    arr = pa.array(vals, type=typ)

    null_count, n_unique, match_counts = native_module().string_column_digest(
        arr, list(_SCAN_STRING_PATTERNS)
    )

    # 1) match_counts == the regex-crate kernel per pattern (ground truth).
    pylist = arr.to_pylist()
    for i, pat in enumerate(_SCAN_STRING_PATTERNS):
        expected = native_module().str_contains_count(pylist, pat)
        assert match_counts[i] == expected, (
            f"pattern {i} {pat!r}: digest={match_counts[i]} kernel={expected}"
        )

    # 2) n_unique == pyarrow count_distinct(mode="all").
    assert n_unique == int(pc.count_distinct(arr, mode="all").as_py())

    # 3) null_count == arr.null_count.
    assert null_count == arr.null_count


def test_arrowcolumn_fast_path_equals_fallback_and_caches() -> None:
    arr = pa.array(_corpus(), type=pa.string())

    col = ArrowColumn(arr)
    fast = [col.str_match_count(p) for p in _SCAN_STRING_PATTERNS]

    # The digest is computed exactly once and cached.
    assert col._str_digest is not None

    # Reference: the regex-crate kernel (what the pyarrow path falls back to for
    # the \uXXXX encoding patterns RE2 cannot compile).
    pylist = arr.to_pylist()
    ref = [native_module().str_contains_count(pylist, p) for p in _SCAN_STRING_PATTERNS]
    assert fast == ref

    # n_unique / null_count read the cached digest (same values as a fresh scan).
    assert col.n_unique() == int(pc.count_distinct(arr, mode="all").as_py())
    assert col.null_count() == arr.null_count


def test_unknown_pattern_does_not_force_digest() -> None:
    arr = pa.array(["aXb", "cd", None, "X"], type=pa.string())
    col = ArrowColumn(arr)
    # A pattern NOT in the fixed set stays on the vectorized pyarrow path and must
    # not populate the digest cache.
    assert col.str_match_count("X") == 2
    assert col._str_digest is None
