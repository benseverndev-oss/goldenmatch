"""Print the survivorship corpus through PYTHON's ``merge_field``, for
differential comparison with ``survivorship-core``'s Rust port.

Committed alongside the Rust half. The transforms harness of the same shape
caught two real bugs review had passed, which is the argument for keeping this
one runnable rather than running it once in a scratch directory.

    cargo run --example parity_dump --manifest-path \
        packages/rust/extensions/survivorship-core/Cargo.toml > rust.txt
    python scripts/survivorship_parity_dump.py > py.txt
    diff rust.txt py.txt

The corpus targets TIE-BREAKS, because that is where this port silently
diverges: Python's ``Counter.most_common`` keeps insertion order and ``max()``
keeps the first maximum, while Rust's ``max_by_key`` keeps the LAST. A tie
resolved the other way is a different golden record with no error attached.

Only the strategies the SPARK call site can reach are dumped -- it passes values
and a strategy and nothing else, so ``source_priority`` and ``most_recent``
raise in Python and are refused in Rust.
"""
from __future__ import annotations

import io
import sys

CASES = [
    ["a", "b"],
    ["b", "a", "a"],
    ["a", "a", "b", "b"],
    [None, "x", "y"],
    [None, None],
    ["a", None, "a"],
    ["ab", "abcd"],
    ["ab", "cd"],
    ["café", "abcde"],
    ["", "x"],
    ["same", "same", "same"],
    ["日本語", "ab"],
]

SUPPORTED = [
    "most_complete", "majority_vote", "first_non_null", "longest_value",
    "unanimous_or_null", "confidence_majority",
]


def _rust_opt(v: str | None) -> str:
    return "None" if v is None else f'Some({v!r})'.replace("'", '"')


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field

    for case in CASES:
        shown = "[" + ", ".join(_rust_opt(v) for v in case) + "]"
        for s in SUPPORTED:
            try:
                merged, _conf, _src = merge_field(list(case), GoldenFieldRule(strategy=s))
            except Exception:  # noqa: BLE001 - a refusal is a result
                out = "REFUSED"
            else:
                out = "None" if merged is None else f'{merged!r}'.replace("'", '"')
            print(f"{s}\t{shown}\t{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
