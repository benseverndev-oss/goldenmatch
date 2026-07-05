#!/usr/bin/env python
"""Generate the explicit `normalize_unicode` decompose+strip-combining map for
all three surfaces (Rust goldenflow-core, Python fallback, TS fallback) from
Python's ``unicodedata`` NFKD -- ONE source of truth.

`normalize_unicode` = NFKD-normalize then drop combining marks. Over the
covered ranges this reduces to a fixed char->replacement table (a precomposed
char -> its base letter(s), diacritic dropped). We materialize that table
EXPLICITLY (like `name_transliterate`) rather than call each runtime's
`unicodedata`/`String.normalize` -- so the three surfaces are byte-identical
regardless of their bundled Unicode DB version.

Coverage: U+00C0-U+017F (Latin-1 Supplement + Latin Extended-A) and
U+1E00-U+1EFF (Latin Extended Additional / Vietnamese). Chars outside these
ranges (CJK, rare precomposed) pass through unchanged -- the documented
reference-mode boundary. Non-decomposing chars in-range (ss-eszett, ae/oe
ligatures, o-slash, l-stroke, d-stroke, thorn, eth) are NOT in the table (NFKD
leaves them), so they pass through -- distinct from `name_transliterate`.

Run: python scripts/gen_normalize_unicode_map.py
Emits three snippet files under the scratchpad for pasting into the kernels.
"""
from __future__ import annotations

import unicodedata

_RANGES = [(0x00C0, 0x0180), (0x1E00, 0x1F00)]


def _norm(c: str) -> str:
    nfkd = unicodedata.normalize("NFKD", c)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def build() -> list[tuple[int, str]]:
    out = []
    for lo, hi in _RANGES:
        for cp in range(lo, hi):
            c = chr(cp)
            r = _norm(c)
            if r != c:
                out.append((cp, r))
    return out


def _rs_lit(s: str) -> str:
    # Rust string literal with \u{..} escapes for non-ASCII.
    parts = []
    for ch in s:
        if ch == "\\":
            parts.append("\\\\")
        elif ch == '"':
            parts.append('\\"')
        elif 0x20 <= ord(ch) < 0x7F:
            parts.append(ch)
        else:
            parts.append(f"\\u{{{ord(ch):X}}}")
    return '"' + "".join(parts) + '"'


def _js_lit(s: str) -> str:
    parts = []
    for ch in s:
        if ch == "\\":
            parts.append("\\\\")
        elif ch == '"':
            parts.append('\\"')
        elif 0x20 <= ord(ch) < 0x7F:
            parts.append(ch)
        else:
            parts.append(f"\\u{{{ord(ch):X}}}")
    return '"' + "".join(parts) + '"'


def main() -> None:
    table = build()
    # Rust: match arms `'\u{XX}' => "..",`
    rs = "\n".join(f"        '\\u{{{cp:X}}}' => {_rs_lit(r)}," for cp, r in table)
    # Python: dict entries `"\uXXXX": "..",`
    py = "\n".join(f'    "\\U{cp:08X}": {r!r},' for cp, r in table)
    # TS: `[cp, ".."],` pairs (Map from codepoint -> replacement)
    ts = "\n".join(f"  [0x{cp:X}, {_js_lit(r)}]," for cp, r in table)
    print(f"// {len(table)} entries")
    print("=== RUST ===")
    print(rs)
    print("=== PYTHON ===")
    print(py)
    print("=== TS ===")
    print(ts)


if __name__ == "__main__":
    main()
