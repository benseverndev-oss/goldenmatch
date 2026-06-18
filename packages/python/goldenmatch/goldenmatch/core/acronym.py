"""Initialism blocking transform — abbreviation candidate generation.

Turns a multi-token name into the initials of its tokens
("International Business Machines" -> "IBM") so an initials-keyed blocking
pass proposes the abbreviation as a match candidate ("IBM" <-> the full
form). This closes a recall gap: an exact/soundex block on the raw name
would never co-locate "IBM" with "International Business Machines".

Exposed as a ``TransformPlugin`` named ``initialism`` (resolved by
``goldenmatch.utils.transforms.apply_transform`` via the ``PluginRegistry``),
mirroring ``goldenmatch.refdata.business.LegalFormStripTransform``. It is NOT
a member of ``VALID_SIMPLE_TRANSFORMS`` — that frozenset is the native-Polars
fast path; this is a Python-callable plugin transform.
"""
from __future__ import annotations

import re

from goldenmatch.plugins.base import TransformPlugin

# First alphabetic character of a token (skips leading digits / punctuation).
_FIRST_ALPHA = re.compile(r"[A-Za-z]")
# Any alphabetic character (used for the punctuation-only-token drop).
_ANY_ALPHA = re.compile(r"[A-Za-z]")
# A trailing/leading parenthetical group: "(Armonk, NY)", "(WHO)", etc.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


def _legal_form_variants() -> frozenset[str]:
    """Normalized (lower-case) set of entity-TYPE legal-form variants, e.g.
    ``{"inc", "incorporated", "corp", "corporation", "llc", ...}`` — EXCLUDING
    descriptive tokens like "Industries" / "Group" / "Holdings".

    Sourced from the same refdata pack ``strip_legal_form`` uses, so the
    per-token drop here stays in lockstep with the trailing-suffix stripper but
    keeps the descriptive token that makes the abbreviation meaningful
    ("Acme Industries LLC" -> drop "LLC", keep "Industries" -> "AI"). Empty
    frozenset when the pack is unavailable (initialism blocking then just keeps
    the legal-form token, which is harmless — it only changes the key).
    """
    try:
        from goldenmatch.refdata import business

        return business.entity_form_variants()
    except Exception:  # noqa: BLE001 — never let an unhealthy pack break blocking
        return frozenset()


def _normalize_token_for_legal_check(token: str) -> str:
    """Lower-case + strip trailing punctuation, matching the refdata pack's
    ``_normalize_token`` so a token like ``"Corp."`` compares equal to the
    bundled variant ``"corp"``."""
    return token.strip().rstrip(".,").lower()


def derive_initialism(text: str | None) -> str | None:
    """Return an abbreviation block key for a name, noise-tolerant + acronym-aware.

    Steps:

    1. Strip parentheticals: ``"... (Armonk, NY)"`` -> ``"..."``.
    2. Tokenize on whitespace; drop legal-form tokens ANYWHERE (not just
       trailing) and punctuation-only tokens.
    3. **Acronym-as-own-key:** if exactly ONE token survives AND it looks like
       an acronym (all-uppercase in the ORIGINAL, all alphabetic, length 2-6),
       return it uppercased — so ``"IBM"`` blocks under its own letters and
       co-locates with its expansion.
    4. If >= 2 cleaned tokens: return the first-alpha-letter-of-each-token
       initialism, uppercased (``"International Business Machines"`` -> ``"IBM"``).
    5. Otherwise (1 non-acronym token like ``"Apple"``, or empty) -> ``""``.

    Examples:
        ``"IBM"`` -> ``"IBM"``; ``"Apple"`` -> ``""``;
        ``"International Business Machines Corporation (Armonk, NY)"`` -> ``"IBM"``;
        ``"International Business Machines"`` -> ``"IBM"``.

    ``None`` -> ``None``.
    """
    if text is None:
        return None

    # 1. Strip parentheticals before tokenizing.
    stripped = _PARENTHETICAL.sub("", text)

    legal_variants = _legal_form_variants()

    # 2. Tokenize, dropping legal-form tokens (anywhere) and punctuation-only tokens.
    cleaned_tokens: list[str] = []
    for token in stripped.split():
        if _ANY_ALPHA.search(token) is None:
            continue  # punctuation-only / digits-only token
        if _normalize_token_for_legal_check(token) in legal_variants:
            continue  # drop legal-form token wherever it appears
        cleaned_tokens.append(token)

    # 3. Acronym-as-own-key: a lone all-caps alphabetic 2-6 char token blocks
    #    under its own letters (so the acronym co-locates with its expansion).
    if len(cleaned_tokens) == 1:
        tok = cleaned_tokens[0]
        if tok.isupper() and tok.isalpha() and 2 <= len(tok) <= 6:
            return tok.upper()
        # Non-acronym single token (e.g. "Apple") -> too coarse a key.
        return ""

    # 5. Empty after cleaning.
    if not cleaned_tokens:
        return ""

    # 4. Multi-token initialism: first alpha letter of each token, uppercased.
    initials: list[str] = []
    for token in cleaned_tokens:
        m = _FIRST_ALPHA.search(token)
        if m is not None:
            initials.append(m.group(0).upper())

    if len(initials) < 2:
        return ""
    return "".join(initials)


class InitialismTransform(TransformPlugin):
    """Adapter exposing ``derive_initialism`` through the
    ``goldenmatch.plugins.base.TransformPlugin`` protocol."""

    name = "initialism"

    def transform(self, value: str | None) -> str | None:
        if value is None:
            return None
        return derive_initialism(value)


def register_transforms() -> None:
    """Idempotent. Registers the ``initialism`` transform plugin.

    Called at module import (bottom of this file) and re-exported through
    ``goldenmatch.refdata.__init__`` so it is live before blocking runs.
    """
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(InitialismTransform.name):
        reg.register_transform(InitialismTransform.name, InitialismTransform())


register_transforms()
