"""Normalization for WhoIsWho SND -- the make-or-break for the relational signal.

Name string is CONSTANT within a name-block, so it carries zero disambiguation
signal. What discriminates two real people sharing a name is *who they publish
with* and *where* -- co-author and organization set overlap. Those signals only
work if names/orgs are normalized to a canonical form before set intersection,
so a co-author written "Y. Zeng" in one paper and "Yong Zeng" in another isn't
silently treated as two different people.

These helpers are deliberately dependency-free (str ops only) so the scorer and
the frame builder share ONE normalization and can't drift.
"""
from __future__ import annotations

import re
import unicodedata

# Co-author / org sets are encoded as a single delimited string per cell so they
# ride through goldenmatch's string-typed scorer surface. "|" never appears in a
# normalized token (punctuation is stripped), so it is a safe set delimiter.
SET_DELIM = "|"

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_token(s: str | None) -> str:
    """Canonicalize one free-text token (an org string, a venue)."""
    if not s:
        return ""
    s = _strip_accents(str(s)).lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def norm_name(name: str | None) -> str:
    """Canonicalize a person name to an ORDER-INSENSITIVE token signature.

    Chinese/Western name-order varies across papers ("Haifeng Qian" vs
    "Qian Haifeng"); sorting the tokens makes the two collide. Single-letter
    initials are kept (they still discriminate) but the sort means an initials-
    only form ("H Qian") overlaps a full form ("Haifeng Qian") only on the
    shared "qian" token -- which is the honest partial signal.
    """
    n = norm_token(name)
    if not n:
        return ""
    return " ".join(sorted(n.split()))


def name_key(name: str | None) -> str:
    """A hashable identity key for a name (used to drop the self-author)."""
    return norm_name(name)


def encode_set(items) -> str:
    """Encode an iterable of tokens as a sorted, de-duplicated delimited string.

    Empty / falsy tokens are dropped. The result is stable (sorted) so two cells
    with the same underlying set are byte-identical.
    """
    seen = {t for t in (norm_token(i) for i in items) if t}
    return SET_DELIM.join(sorted(seen))


def decode_set(cell: str | None) -> set[str]:
    """Inverse of :func:`encode_set` -- the scorer's view of a set cell."""
    if not cell:
        return set()
    return {t for t in cell.split(SET_DELIM) if t}


def jaccard(a: set[str], b: set[str]) -> float:
    """|A n B| / |A u B|. Two empty sets share no positive evidence -> 0.0."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)
