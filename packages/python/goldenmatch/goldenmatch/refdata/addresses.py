"""USPS-style address-token normalization.

Ships the ``address_normalize`` transform — collapses street suffix,
directional, and secondary-unit variants to their USPS Publication 28
canonical short forms so that "123 Main Street North" and
"123 Main St N" both reduce to "123 main st n" before scoring.

Public API:

- ``normalize_address(value)`` — the bare transform function.
- ``is_available()`` — True iff the bundled abbreviation table loaded.
- ``known_tokens()`` — frozen set of every variant the transform
  recognises (lower-case), for diagnostics / tests.
"""
from __future__ import annotations

import json
import logging
import re
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "address_abbreviations.json"

_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")
_PUNCT_STRIP_RE = re.compile(r"[.,;:]+$")
_LEAD_PUNCT_STRIP_RE = re.compile(r"^[.,;:#\-]+")

# Pre-tokenization rewrites that preserve match invariance across common
# US address variant pairs. Each substitution turns one surface form into
# the canonical-short USPS form *before* tokenization, so the rest of the
# pipeline (tokenize → lowercase → strip punct → canonical lookup) sees
# the same input regardless of which variant the source data used.
_PRENORMALIZE = [
    # Apartment-with-pound-sign: "123 Main St #5" should reduce to the
    # same canonical as "123 Main St Apt 5". Match `#` followed by digits
    # (optionally with whitespace between) and emit `apt <digits>`. Anchor
    # to a word boundary on the # side so "#tag" mid-text isn't touched.
    (re.compile(r"(?<![A-Za-z0-9])#\s*(\d+)", re.IGNORECASE), r"apt \1"),
    # PO Box variants — strip the periods and collapse whitespace so
    # `PO Box 42`, `P.O. Box 42`, `P. O. Box 42`, `POBOX 42` all reduce
    # to `po box 42`.
    (re.compile(r"\bP\.?\s*O\.?\s*Box\b", re.IGNORECASE), "PO Box"),
    (re.compile(r"\bPOBOX\b", re.IGNORECASE), "PO Box"),
]

_lock = Lock()
_state: dict = {
    "loaded": False,
    "available": False,
    # variant (lower-case, no punct) -> canonical short form
    "canonical": {},
    "known": frozenset(),
}


def _load() -> None:
    if _state["loaded"]:
        return
    with _lock:
        if _state["loaded"]:
            return
        try:
            with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
                "r", encoding="utf-8"
            ) as f:
                payload = json.load(f)
            canonical: dict[str, str] = {}
            for section in ("street_suffixes", "directionals", "secondary_units"):
                block = payload.get(section, {})
                for canon, variants in block.items():
                    c = canon.lower().strip()
                    if not c:
                        continue
                    canonical[c] = c  # canonical maps to itself (idempotent)
                    for v in variants:
                        nv = v.lower().strip()
                        if nv:
                            canonical[nv] = c
            if not canonical:
                _state["available"] = False
                _state["loaded"] = True
                return
            _state["canonical"] = canonical
            _state["known"] = frozenset(canonical.keys())
            _state["available"] = True
        except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "goldenmatch.refdata.addresses: data file unavailable (%s); "
                "address_normalize will be lowercase+strip only.",
                exc,
            )
            _state["available"] = False
        finally:
            _state["loaded"] = True


def _reload() -> None:
    """Test-only: force re-parse of the data file."""
    with _lock:
        _state["loaded"] = False
        _state["available"] = False
        _state["canonical"] = {}
        _state["known"] = frozenset()
    _load()


def is_available() -> bool:
    _load()
    return _state["available"]


def known_tokens() -> frozenset[str]:
    """Every variant recognised by the transform, lower-case. Diagnostic."""
    _load()
    return _state["known"]


def _normalize_token(t: str) -> str:
    """Lower-case, strip leading/trailing punctuation."""
    t = _LEAD_PUNCT_STRIP_RE.sub("", t)
    t = _PUNCT_STRIP_RE.sub("", t)
    return t.lower()


def normalize_address(value: str | None) -> str | None:
    """Canonicalize an address string.

    Tokenizes on whitespace + commas, lowercases each token, strips
    leading/trailing punctuation, and maps every recognised street-suffix
    / directional / secondary-unit variant to its USPS canonical short
    form ("street" → "st", "north" → "n", "apartment" → "apt"). Unknown
    tokens pass through unchanged.

    Returns ``None`` for ``None`` input. If the bundled data file is
    missing, falls back to lowercase + whitespace-normalize.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return ""
    # Pre-tokenization rewrites for variants that don't decompose cleanly
    # along whitespace/comma boundaries (#5 ↔ Apt 5, P.O. Box ↔ POBOX).
    for pattern, replacement in _PRENORMALIZE:
        cleaned = pattern.sub(replacement, cleaned)
    _load()
    canonical_map = _state["canonical"]
    out: list[str] = []
    for raw_token in _TOKEN_SPLIT_RE.split(cleaned):
        if not raw_token:
            continue
        norm = _normalize_token(raw_token)
        if not norm:
            continue
        canon = canonical_map.get(norm, norm) if canonical_map else norm
        out.append(canon)
    return " ".join(out)


# ── Plugin protocol adapter ──────────────────────────────────────────────


class AddressNormalizeTransform:
    """Adapter exposing ``normalize_address`` through the plugin transform
    interface."""

    name = "address_normalize"

    def transform(self, value: str | None) -> str | None:
        return normalize_address(value)


def register_transforms() -> None:
    """Idempotent. Called from ``goldenmatch.refdata.__init__`` on import."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(AddressNormalizeTransform.name):
        reg.register_transform(AddressNormalizeTransform.name, AddressNormalizeTransform())
