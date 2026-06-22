r"""Pure, language-portable per-value field canonicalizers (#1128).

``canonicalize(value, kind)`` reduces a single field value to a deterministic
canonical string for match keys (email / phone / name / postal). Unlike the
frame-level ``phone_e164`` / ``address_standardize`` transforms — which infer
country, parse streets, and lean on ``phonenumbers`` / parsing libraries — these
are deliberately *scalar* and *dependency-free* so they can be reproduced
**byte-for-byte** in another language (e.g. a browser JavaScript/TypeScript
port). That matters for privacy-preserving record linkage / clean rooms: in the
true-clean-room tier each party hashes its own values client-side, so the
server-side Python and the browser-side JS MUST agree on the exact canonical
string before hashing, or the CLKs never line up.

Design contract — every canonicalizer is:

- **scalar**: one ``str`` in, one ``str`` out (no Series / DataFrame).
- **total**: never raises on string input; ``None`` maps to ``""``.
- **idempotent**: ``f(f(x)) == f(x)`` for all ``x`` (pinned by tests).
- **locale-independent**: case folding is ASCII-only (``A``–``Z`` ↔ ``a``–``z``),
  NOT Unicode/locale-aware ``str.lower()``. Non-ASCII bytes pass through
  unchanged, so the output depends only on the input bytes — never on the host's
  locale or Unicode version. Callers who need Unicode folding (e.g. NFC,
  accent stripping) should normalize upstream.
- **dependency-free**: stdlib only; no ``polars`` / ``phonenumbers`` / parsing.

JS/TS port spec (each rule is written to mirror one-for-one):

- ASCII-lowercase  → ``s.replace(/[A-Z]/g, c => String.fromCharCode(c.charCodeAt(0) + 32))``
- ASCII-uppercase  → ``s.replace(/[a-z]/g, c => String.fromCharCode(c.charCodeAt(0) - 32))``
- ASCII whitespace → the set ``" \t\n\r\f\v"`` (JS: ``/[ \t\n\r\f\v]/``); collapse
  runs to one space and trim ends of exactly that set (NOT JS ``\s``, which is
  Unicode-aware).
- ASCII punctuation → ``string.punctuation`` =
  ``!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~`` (JS: the same literal set).
- ASCII digit / letter / alnum → ``[0-9]`` / ``[A-Za-z]`` / ``[A-Za-z0-9]``
  (NOT ``str.isdigit``/``isalnum``, which accept superscripts, Arabic-Indic
  digits, etc.).

Per-``kind`` rules:

- ``"email"``  : trim ASCII whitespace, then ASCII-lowercase. (No domain/dot
  surgery — keep it a reproducible byte transform.)
- ``"phone"``  : keep ASCII digits only; if the result is 11 digits starting
  with ``1`` (NANP country code), drop the leading ``1`` → 10 digits.
- ``"name"``   : ASCII-lowercase, delete ASCII punctuation, collapse ASCII
  whitespace runs to one space, trim.
- ``"postal"`` : if the value contains any ASCII letter (alphanumeric postcode,
  e.g. UK ``"SW1A 1AA"`` / CA ``"K1A 0B1"``), keep ASCII alphanumerics only and
  ASCII-uppercase; otherwise (numeric ZIP-like) keep ASCII digits and take the
  first 5.
"""

from __future__ import annotations

import string
from typing import Literal

__all__ = ["canonicalize", "CanonicalizeKind"]

CanonicalizeKind = Literal["email", "phone", "name", "postal"]

# ── Portable ASCII primitives ─────────────────────────────────────────────────
# str.translate tables keep the operations branch-free and obviously ASCII-only.
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_ASCII_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)
# Delete every ASCII punctuation char (string.punctuation is the canonical set).
_DELETE_PUNCT = str.maketrans("", "", string.punctuation)
# The ASCII whitespace set we collapse/trim on. Deliberately NOT Unicode \s.
_ASCII_WS = " \t\n\r\f\v"


def _ascii_lower(s: str) -> str:
    return s.translate(_ASCII_LOWER)


def _ascii_upper(s: str) -> str:
    return s.translate(_ASCII_UPPER)


def _collapse_ws(s: str) -> str:
    """Collapse runs of ASCII whitespace to a single space and trim the ends.

    Avoids ``re``/``\\s`` (Unicode-aware) so a JS port matches exactly: split on
    the ASCII whitespace set, drop empties, join with one space.
    """
    return " ".join(_ascii_ws_split(s))


def _ascii_ws_split(s: str) -> list[str]:
    """Split on runs of the ASCII whitespace set, dropping empty tokens."""
    tokens: list[str] = []
    current: list[str] = []
    for ch in s:
        if ch in _ASCII_WS:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _canon_email(value: str) -> str:
    return _ascii_lower(value.strip(_ASCII_WS))


def _canon_phone(value: str) -> str:
    digits = "".join(c for c in value if "0" <= c <= "9")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits


def _canon_name(value: str) -> str:
    lowered = _ascii_lower(value)
    no_punct = lowered.translate(_DELETE_PUNCT)
    return _collapse_ws(no_punct)


def _canon_postal(value: str) -> str:
    has_letter = any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in value)
    if has_letter:
        alnum = "".join(
            c
            for c in value
            if ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9")
        )
        return _ascii_upper(alnum)
    digits = "".join(c for c in value if "0" <= c <= "9")
    return digits[:5]


_CANONICALIZERS = {
    "email": _canon_email,
    "phone": _canon_phone,
    "name": _canon_name,
    "postal": _canon_postal,
}


def canonicalize(value: str | None, kind: CanonicalizeKind) -> str:
    """Reduce a single field value to its canonical match-key string.

    Pure, total, idempotent, locale-independent, and dependency-free — see the
    module docstring for the full spec and the JS/TS port mapping. ``None`` maps
    to ``""``.

    Args:
        value: the raw field value (or ``None``).
        kind: which canonicalizer — ``"email"``, ``"phone"``, ``"name"``, or
            ``"postal"``.

    Returns:
        The canonical string.

    Raises:
        ValueError: if ``kind`` is not one of the four supported values (a
            programming error, surfaced loudly rather than silently no-op'd).
    """
    fn = _CANONICALIZERS.get(kind)
    if fn is None:
        raise ValueError(
            f"Unknown canonicalize kind {kind!r}; "
            f"expected one of {sorted(_CANONICALIZERS)}."
        )
    if value is None:
        return ""
    return fn(value)
