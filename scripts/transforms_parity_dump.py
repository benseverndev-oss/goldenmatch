"""Print the transform corpus through PYTHON, for differential comparison with
``transforms-core``'s Rust port.

Committed alongside the Rust half (``packages/rust/extensions/transforms-core/
examples/parity_dump.rs``) because a port is only as good as the evidence it
matches the original, and evidence in a scratch directory cannot be re-run by
the next person to touch the crate.

    cargo run --example parity_dump --manifest-path \\
        packages/rust/extensions/transforms-core/Cargo.toml > rust.txt
    python scripts/transforms_parity_dump.py > py.txt
    diff rust.txt py.txt

Byte-identical output is the bar. Normalization feeds BLOCKING, so a value that
transforms differently on the two surfaces does not produce a wrong score -- it
lands in a different block, the pair is never compared, and nothing downstream
notices. A tolerance would defeat the entire check.

The corpus is chosen for the ways a port breaks, not for readability: multi-byte
values (code-point vs byte slicing), case mappings that change length
(``straße`` -> ``STRASSE``), exotic whitespace (Python counts the C1 separators
``U+001C..U+001F``; Rust's ``char::is_whitespace`` does not), honorific-only
values (missing vs empty), and empty strings.

**Keep the two corpora identical.** They are duplicated rather than shared
because the point is to run each side through its own language's idea of what
the value is; a shared fixture file read by both would be strictly better and is
a fair follow-up, but it must not paper over an encoding difference.
"""
from __future__ import annotations

import io
import sys
import unicodedata

VALUES = [
    "Jonathan Smith",
    "  Dr. Jonathan   Smith  ",
    "",
    "café",
    "Zoë Müller",
    "日本語テキスト",
    "O'Brien-Smith Jr.",
    "ACME Corporation 123",
    "straße",
    "İstanbul",
    "ab",
    "\t mixed \n whitespace \r ",
    "Mr.",
    "Prof Dr Alice",
    "123-456-7890",
]

TRANSFORMS = [
    "lowercase", "uppercase", "strip", "strip_all", "digits_only", "alpha_only",
    "normalize_whitespace", "token_sort", "first_token", "last_token",
    "soundex", "metaphone", "strip_honorifics",
    "substring:0:3", "substring:2:5", "substring:0:99", "qgram:2", "qgram:3",
]


def _rust_repr(s: str) -> str:
    """Rust's ``{:?}`` for a string, so the two dumps are comparable.

    Rust escapes ``\\`` and ``"`` and the usual control characters, and prints
    everything else -- including non-ASCII -- literally. Python's ``repr`` uses
    single quotes and escapes differently, so it cannot be compared directly.
    """
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif c == "\0":
            out.append("\\0")
        elif ord(c) < 0x20 or ord(c) == 0x7F or unicodedata.combining(c):
            # Rust's ``{:?}`` uses ``char::escape_debug``, which escapes
            # GRAPHEME-EXTEND characters as well as control ones. ``"İ".lower()``
            # yields ``i`` + U+0307 (combining dot above), which Rust prints as
            # ``i\\u{307}`` and a naive Python repr prints literally. That is a
            # difference between the two DEBUG FORMATS, not between the
            # transforms, and reading it as a divergence sends you hunting a bug
            # that is not there -- it did, for one round. Match Rust's escaping
            # so a diff means what it says.
            out.append(f"\\u{{{ord(c):x}}}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    from goldenmatch.utils.transforms import apply_transform

    for v in VALUES:
        for t in TRANSFORMS:
            try:
                got = apply_transform(v, t)
            except Exception as exc:  # noqa: BLE001 - a refusal is a result
                out = f"UNSUPPORTED({type(exc).__name__})"
            else:
                out = "None" if got is None else _rust_repr(got)
            print(f"{t}\t{_rust_repr(v)}\t{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
