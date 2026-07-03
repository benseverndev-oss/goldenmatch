#!/usr/bin/env python
"""Generate/check the byte-parity corpus for goldenflow identifier transforms.

Recomputes ``expected`` for every corpus row by calling the reference
kernels -- the native ``goldenflow._native`` (or ``goldenflow_native._native``)
module when importable, else the pure-Python fallback in
``goldenflow.transforms.identifiers``. Both are asserted to agree wherever
native is available, so either source is a valid oracle; native is preferred
because it is the canonical reference (docs/design/2026-07-01-rust-is-the-
reference-roadmap.md).

Usage:
    python scripts/gen_identifiers_corpus.py            # rewrite the corpus
    python scripts/gen_identifiers_corpus.py --check     # diff only, exit 1 on drift

The corpus format is JSON Lines, one row per case:
    {"transform": "cc_validate", "input": "4242 4242 4242 4242", "expected": true}
    {"transform": "cc_format",   "input": "378282246310005",     "expected": "3782 822463 10005"}
    {"transform": "cc_mask",     "input": "4242424242424242",    "expected": "************4242"}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goldenflow.core._native_loader import native_available, native_module  # noqa: E402
from goldenflow.transforms.identifiers import (  # noqa: E402
    _cc_format_py,
    _cc_mask_py,
    _cc_validate_py,
)

CORPUS_PATH = Path(__file__).resolve().parent.parent / "tests" / "parity" / "identifiers_corpus.jsonl"

# (transform, input) pairs. `expected` is recomputed, never hand-maintained.
_CASES: list[tuple[str, str | None]] = [
    # --- cc_validate: valid ---
    ("cc_validate", "4242 4242 4242 4242"),  # Visa test, spaced
    ("cc_validate", "4242424242424242"),  # Visa test, bare
    ("cc_validate", "5555555555554444"),  # Mastercard
    ("cc_validate", "378282246310005"),  # Amex (15)
    ("cc_validate", "4000-0000-0000-0002"),  # Visa, dashed
    ("cc_validate", "6011111111111117"),  # Discover
    ("cc_validate", "30569309025904"),  # Diners Club (14)
    ("cc_validate", "4111111111111111111"),  # 19-digit, length-boundary, bad checksum
    # --- cc_validate: invalid ---
    ("cc_validate", "4242424242424241"),  # bad checksum
    ("cc_validate", "1234"),  # too short
    ("cc_validate", "4242abcd42424242"),  # non-digit
    ("cc_validate", "42424242424242424242"),  # 20 digits, too long
    ("cc_validate", "123456789012"),  # 12 digits, too short
    ("cc_validate", ""),  # empty
    ("cc_validate", None),  # null
    # --- cc_format: valid ---
    ("cc_format", "4242424242424242"),  # 16-digit -> 4-4-4-4
    ("cc_format", "4242 4242 4242 4242"),  # already spaced
    ("cc_format", "378282246310005"),  # Amex -> 4-6-5
    ("cc_format", "340000000000009"),  # Amex (34 prefix) -> 4-6-5
    ("cc_format", "6011111111111117"),  # Discover -> 4-4-4-4
    ("cc_format", "30569309025904"),  # Diners (14, not Amex) -> 4-4-4-2
    ("cc_format", "4111111111111111111"),  # 19-digit -> trailing groups of 4
    # --- cc_format: invalid -> null ---
    ("cc_format", "4242424242424241"),  # bad checksum
    ("cc_format", "1234"),  # too short
    ("cc_format", ""),
    ("cc_format", None),
    # --- cc_mask: valid (length-only, no Luhn requirement) ---
    ("cc_mask", "4242424242424242"),
    ("cc_mask", "4242 4242 4242 4242"),
    ("cc_mask", "378282246310005"),
    ("cc_mask", "4242424242424241"),  # bad checksum but still maskable (len OK)
    ("cc_mask", "4111111111111111111"),  # 19-digit
    # --- cc_mask: invalid -> null ---
    ("cc_mask", "bogus"),
    ("cc_mask", "1234"),
    ("cc_mask", ""),
    ("cc_mask", None),
]

_PY_FN = {
    "cc_validate": _cc_validate_py,
    "cc_format": _cc_format_py,
    "cc_mask": _cc_mask_py,
}

_NATIVE_ARROW_FN = {
    "cc_validate": "cc_validate_arrow",
    "cc_format": "cc_format_arrow",
    "cc_mask": "cc_mask_arrow",
}


def _native_one(transform: str, value: str | None) -> object:
    """Call the native kernel on a single value via a length-1 Arrow array.
    Returns ``_NO_NATIVE_SYMBOL`` if the installed/built module predates the
    ``cc`` kernel (wheel-skew: a stale ``goldenflow-native`` wheel without the
    new symbols) -- in that case pure-Python is the only oracle for this row."""
    import pyarrow as pa

    nm = native_module()
    attr = _NATIVE_ARROW_FN[transform]
    if not hasattr(nm, attr):
        return _NO_NATIVE_SYMBOL
    func = getattr(nm, attr)
    out = func(pa.array([value], type=pa.string()))
    return out.to_pylist()[0]


_NO_NATIVE_SYMBOL = object()


def compute_expected(transform: str, value: str | None) -> object:
    py_result = _PY_FN[transform](value)
    if native_available():
        try:
            nat_result = _native_one(transform, value)
        except ImportError:
            nat_result = _NO_NATIVE_SYMBOL  # pyarrow not installed
        if nat_result is not _NO_NATIVE_SYMBOL and nat_result != py_result:
            raise AssertionError(
                f"native/python disagree on {transform}({value!r}): "
                f"native={nat_result!r} python={py_result!r}"
            )
    return py_result


def build_corpus() -> list[dict[str, object]]:
    rows = []
    for transform, value in _CASES:
        expected = compute_expected(transform, value)
        rows.append({"transform": transform, "input": value, "expected": expected})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in-memory and diff against the committed corpus; "
        "exit nonzero on drift (used by CI)",
    )
    args = parser.parse_args()

    rows = build_corpus()
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    new_content = "\n".join(lines) + "\n"

    oracle = "native" if native_available() else "pure-Python fallback"

    if args.check:
        if not CORPUS_PATH.exists():
            print(f"MISSING: {CORPUS_PATH}", file=sys.stderr)
            return 1
        current = CORPUS_PATH.read_text(encoding="utf-8")
        if current != new_content:
            print(
                f"DRIFT: {CORPUS_PATH} does not match the regenerated corpus "
                f"(oracle: {oracle}). Run `python scripts/gen_identifiers_corpus.py` "
                "to refresh it.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: corpus matches regenerated output (oracle: {oracle})")
        return 0

    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(new_content, encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {CORPUS_PATH} (oracle: {oracle})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
