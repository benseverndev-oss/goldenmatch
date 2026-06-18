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


def _strip_trailing_legal_form(text: str) -> str:
    """Drop a SINGLE trailing corporate-entity suffix (LLC, Inc, GmbH, ...).

    Deliberately NOT ``goldenmatch.refdata.business.strip_legal_form`` — that
    helper iterates up to 4 passes, so on "Acme Industries LLC" it strips
    "LLC" *and then* "Industries" (a descriptive token also present in the
    legal-form variant list), collapsing to "Acme". For an initialism block
    key we want only the entity-type suffix gone ("Acme Industries"), keeping
    the descriptive token so the abbreviation stays meaningful ("AI"). One
    application of the shared compiled pattern does exactly that. Falls back
    to whitespace-collapse when the refdata pack is unavailable.
    """
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return cleaned
    try:
        from goldenmatch.refdata import business

        business._load()
        state = business._state
    except Exception:  # noqa: BLE001 — never let an unhealthy pack break blocking
        state = None
    if state is None:
        return cleaned
    return state.pattern.sub("", cleaned).strip() or cleaned


def derive_initialism(text: str | None) -> str | None:
    """Return the upper-cased initials of a multi-token name.

    "International Business Machines" -> "IBM". A single trailing legal-form
    token is dropped first, so "Acme Industries LLC" -> "AI". A single
    resulting token yields ``""`` (one letter is too coarse a block key — it
    would over-merge), as does empty / whitespace-only input.

    ``None`` -> ``None``.
    """
    if text is None:
        return None

    stripped = _strip_trailing_legal_form(text)

    initials: list[str] = []
    for token in stripped.split():
        m = _FIRST_ALPHA.search(token)
        if m is not None:
            initials.append(m.group(0).upper())

    # Fewer than 2 tokens contributes no useful abbreviation block key.
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
