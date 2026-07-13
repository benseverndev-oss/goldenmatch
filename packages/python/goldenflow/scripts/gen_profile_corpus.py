#!/usr/bin/env python
"""Generate/check the cross-surface parity corpus for goldenflow auto-detect.

Each row pins the owned type-inference DECISION (``infer_type`` /
``_infer_type_list``) for one column of values:

    {"values": [...], "hint": "...", "expected_type": "..."}

``expected_type`` is recomputed as ``_infer_type_list(values)`` -- the pure-Python
reference, which the Rust ``goldenflow_core::profile`` unit tests already pin
byte-for-byte to the owned kernel, so it is a valid cross-surface oracle. ``hint``
is what ``_scalar_type_hint(values) or "string"`` returns (the numeric/boolean/
Utf8 short-circuit the native FFI takes). When the native ``infer_type_list_arrow``
symbol is importable, the generator asserts the kernel agrees with the pure oracle
on every row (str(v)/None-mapped, same hint), so either source is valid.

Usage:
    python scripts/gen_profile_corpus.py            # rewrite the corpus
    python scripts/gen_profile_corpus.py --check     # diff only, exit 1 on drift
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goldenflow.core._native_loader import native_available, native_module  # noqa: E402
from goldenflow.engine.profiler_bridge import (  # noqa: E402
    _infer_type_list,
    _scalar_type_hint,
)

CORPUS_PATH = Path(__file__).resolve().parent.parent / "tests" / "parity" / "profile_corpus.jsonl"

# Non-matching filler tokens: plain lowercase words match none of the five
# regexes (email/zip/date/phone/name), so they dilute a match ratio cleanly.
_F = ["foo", "bar", "baz", "qux", "quux", "corge"]

# Titlecased multi-word names (match _NAME_RE only).
_NAMES = ["John Smith", "Jane Doe", "Bob Roe", "Amy Poe", "Sam Lee"]
# Phone strings that are NOT date-shaped (match _PHONE_RE, not _DATE_RE).
_PHONES = ["(212) 555-1234", "+1 415 555 9999", "212-555-0000", "(646) 555-7788", "800 555 1212"]
# Date strings (match _DATE_RE; note yyyy-mm-dd ALSO matches _PHONE_RE, but date
# is checked first, and phone alone stays below its 0.6 threshold in the -below rows).
_DATES = ["2020-01-02", "1999/12/31", "1/2/99", "12-31-2020", "March 3, 2001"]
_EMAILS = ["a@b.co", "x@y.io", "p@q.net", "u@v.org", "m@n.com", "c@d.co", "e@f.io"]
_ZIPS = ["12345", "90210", "10001", "60601", "94105", "30301", "02139"]

# The >100-values boundary row: the first 100 NON-NULL values decide the type
# (pure code drops nulls THEN caps at 100), so 100 leading emails -> "email"
# even though nulls + non-emails are interspersed past the 100th non-null.
_BIG_100: list = ["a@b.co"] * 100
for _i in range(40):
    _BIG_100.append(None if _i % 2 == 0 else "not-an-email")

# Each entry is a column of values; expected_type + hint are RECOMPUTED, never
# hand-maintained. Grouped by the branch each row pins.
_CASES: list[list] = [
    # --- scalar-hint short-circuits (numeric / boolean) ---
    [1, 2, 3],  # all int -> numeric
    [1.5, 2.5, 3.5],  # all float -> numeric
    [1, 2, None, 3],  # int with null -> numeric
    [True, False, True],  # all bool -> boolean
    [False, None, True],  # bool with null -> boolean
    # --- email: threshold 0.7 (just-above / just-below) ---
    _EMAILS[:7] + _F[:3],  # 7/10 = 0.70 -> email
    _EMAILS[:6] + _F[:4],  # 6/10 = 0.60 -> string
    # --- zip: threshold 0.7 (checked before date/phone) ---
    _ZIPS[:7] + _F[:3],  # 7/10 -> zip
    _ZIPS[:6] + _F[:4],  # 6/10 -> string
    # --- date: threshold 0.5 (checked before phone) ---
    _DATES[:5] + _F[:5],  # 5/10 = 0.50 -> date
    _DATES[:4] + _F[:6],  # 4/10 = 0.40 -> string
    # --- phone: threshold 0.6 ---
    _PHONES[:3] + _PHONES[:3] + _F[:4],  # 6/10 -> phone
    _PHONES[:5] + _F[:5],  # 5/10 = 0.50 -> string
    # --- name: threshold 0.5 ---
    _NAMES[:5] + _F[:5],  # 5/10 -> name
    _NAMES[:4] + _F[:6],  # 4/10 -> string
    # --- plain string ---
    ["foo", "bar", "baz"],
    # --- empty / all-null / stripped-empties ---
    [None, None],  # all-null -> string
    ["   ", ""],  # stripped-empty skipped -> string
    ["   ", None, "\t"],  # whitespace + null -> string
    # --- most-specific-first collisions ---
    ["12345", "12345", "12345"],  # all zip -> zip (zip beats date/phone)
    ["a@b.co", "foo", "bar"],  # 1/3 email -> string
    _ZIPS[:1] + _DATES[:1] + _F[:1],  # zip+date+filler: none over threshold -> string
    # --- the mixed [1, "1"] row: hint None (not all-numeric), regex path -> string ---
    [1, "1"],
    # --- >100 values, nulls interspersed past the 100th non-null + a
    # type-determining leading pattern (pins "first <=100 non-null then strip"
    # across the FFI stringify/None-drop) ---
    _BIG_100,
]


def _native_crosscheck_active() -> bool:
    """True only when the native ``infer_type_list_arrow`` symbol is actually
    importable -- i.e. when :func:`compute_row` really validates each row against
    the kernel. A stale/absent wheel (native imports but lacks the symbol) is False,
    so the log label can't overclaim a cross-check that didn't happen."""
    if not native_available():
        return False
    nm = native_module()
    return nm is not None and hasattr(nm, "infer_type_list_arrow")


def _hint_for(values: list) -> str:
    return _scalar_type_hint(values) or "string"


def compute_row(values: list) -> dict[str, object]:
    """Build one corpus row. ``expected_type`` = the pure oracle; when the native
    ``infer_type_list_arrow`` symbol exists, assert the kernel agrees (str(v)/None
    mapped, same hint) so either source is a valid oracle."""
    hint = _hint_for(values)
    expected = _infer_type_list(values)
    if _native_crosscheck_active():
        nm = native_module()
        strs = [None if v is None else str(v) for v in values]
        nat = nm.infer_type_list_arrow(strs, hint)
        if nat != expected:
            raise AssertionError(
                f"native/pure disagree on {values!r} (hint={hint!r}): "
                f"native={nat!r} pure={expected!r}"
            )
    return {"values": values, "hint": hint, "expected_type": expected}


def build_corpus() -> list[dict[str, object]]:
    return [compute_row(values) for values in _CASES]


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

    oracle = "native+pure (cross-checked)" if _native_crosscheck_active() else "pure-Python reference"

    if args.check:
        if not CORPUS_PATH.exists():
            print(f"MISSING: {CORPUS_PATH}", file=sys.stderr)
            return 1
        current = CORPUS_PATH.read_text(encoding="utf-8")
        if current != new_content:
            print(
                f"DRIFT: {CORPUS_PATH} does not match the regenerated corpus "
                f"(oracle: {oracle}). Run `python scripts/gen_profile_corpus.py` "
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
