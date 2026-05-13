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
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from threading import Lock

from goldenmatch.plugins.base import TransformPlugin

logger = logging.getLogger(__name__)

_DATA_FILE = "address_abbreviations.json"

_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")
_PUNCT_STRIP_RE = re.compile(r"[.,;:]+$")
_LEAD_PUNCT_STRIP_RE = re.compile(r"^[.,;:#\-]+")

# Pre-tokenization rewrites preserving match invariance across common
# US address variant pairs whose surface forms don't decompose along
# whitespace/comma boundaries.
_PRENORMALIZE = [
    # `#5` ↔ `Apt 5`: pound-then-digits is the apartment designator;
    # rewrite to `apt <digits>` so the rest of the pipeline reduces both
    # forms identically. Anchor away from word boundaries so `#tag` mid-
    # text isn't touched.
    (re.compile(r"(?<![A-Za-z0-9])#\s*(\d+)", re.IGNORECASE), r"apt \1"),
    # `P.O. Box` / `P. O. Box` / `POBOX` ↔ `PO Box` — three surface forms
    # collapse to one before tokenization.
    (re.compile(r"\bP\.?\s*O\.?\s*Box\b", re.IGNORECASE), "PO Box"),
    (re.compile(r"\bPOBOX\b", re.IGNORECASE), "PO Box"),
]


@dataclass(frozen=True)
class _AddressState:
    """Loaded state: a single dict mapping any recognised surface variant
    to its USPS canonical short form, plus the variant set for
    diagnostics. Frozen so readers see a consistent snapshot."""

    canonical: Mapping[str, str]
    known: frozenset[str]


_lock = Lock()
_state: _AddressState | None = None


def _build_state_from_file() -> _AddressState | None:
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
            canonical[c] = c  # canonical maps to itself for idempotency
            for v in variants:
                nv = v.lower().strip()
                if nv:
                    canonical[nv] = c
    if not canonical:
        return None
    return _AddressState(canonical=canonical, known=frozenset(canonical.keys()))


def _load() -> None:
    global _state
    if _state is not None:
        return
    with _lock:
        if _state is not None:
            return
        try:
            _state = _build_state_from_file()
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "goldenmatch.refdata.addresses: data file unavailable (%s); "
                "address_normalize will be lowercase+strip only.",
                exc,
            )
            _state = None


def _reload() -> None:
    """Test-only: atomic-swap reload via None-then-rebuild."""
    global _state
    with _lock:
        _state = None


def is_available() -> bool:
    _load()
    return _state is not None


def known_tokens() -> frozenset[str]:
    _load()
    if _state is None:
        return frozenset()
    return _state.known


def _normalize_token(t: str) -> str:
    t = _LEAD_PUNCT_STRIP_RE.sub("", t)
    t = _PUNCT_STRIP_RE.sub("", t)
    return t.lower()


def normalize_address(value: str | None) -> str | None:
    """Canonicalize an address string.

    Tokenizes on whitespace + commas, lowercases each token, strips
    leading/trailing punctuation, and maps every recognised street-
    suffix / directional / secondary-unit variant to its USPS canonical
    short form. Unknown tokens pass through unchanged.

    ``None`` → ``None``. Data file missing → lowercase + whitespace-
    collapse pass-through.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return ""
    for pattern, replacement in _PRENORMALIZE:
        cleaned = pattern.sub(replacement, cleaned)
    _load()
    canonical_map: Mapping[str, str] = _state.canonical if _state is not None else {}
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


class AddressNormalizeTransform(TransformPlugin):
    """Adapter exposing ``normalize_address`` through the
    ``goldenmatch.plugins.base.TransformPlugin`` protocol."""

    name = "address_normalize"

    def transform(self, value: str | None) -> str | None:
        return normalize_address(value)


def register_transforms() -> None:
    """Idempotent. Called from ``goldenmatch.refdata.__init__``."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(AddressNormalizeTransform.name):
        reg.register_transform(AddressNormalizeTransform.name, AddressNormalizeTransform())
