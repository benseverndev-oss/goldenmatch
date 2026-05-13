"""Business-name normalization.

Ships the ``legal_form_strip`` transform — removes trailing corporate
legal-form tokens ("Inc", "LLC", "GmbH", "Pty Ltd", etc.) from business
names so that "Acme Inc.", "Acme Incorporated", and "Acme Corp." all
collapse to "Acme" before scoring.

Public API:

- ``strip_legal_form(value)`` — the bare transform function.
- ``is_available()`` — True iff the bundled token list loaded.
- ``known_variants()`` — frozen set of every recognised surface variant
  (normalized lower-case), for diagnostics / tests.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from importlib import resources
from threading import Lock

from goldenmatch.plugins.base import TransformPlugin

logger = logging.getLogger(__name__)

_DATA_FILE = "legal_forms.json"


@dataclass(frozen=True)
class _BusinessState:
    """Loaded state: the compiled regex matching every known trailing
    legal-form variant plus the normalized variant set for diagnostics.
    Frozen so callers reading ``_state.pattern`` always see a consistent
    snapshot — readers don't take the lock, so an in-place dict mutation
    would race with ``_reload``."""

    pattern: re.Pattern[str]
    variants_normalized: frozenset[str]


_lock = Lock()
# ``None`` means "not loaded yet, or data file missing". ``_load`` swaps
# in a fully-built ``_BusinessState`` atomically; readers see either the
# old object or the new one, never a half-built dict.
_state: _BusinessState | None = None


def _normalize_token(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip().rstrip(".,")
    return t.lower()


def _build_state_from_file() -> _BusinessState | None:
    """Parse the bundled data file into a fresh state, or return None on
    any failure. Logging happens at the call site so this function stays
    pure-data."""
    with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
        "r", encoding="utf-8"
    ) as f:
        payload = json.load(f)
    forms_block = payload.get("legal_forms", {})
    variants: set[str] = set()
    for variant_list in forms_block.values():
        for v in variant_list:
            n = _normalize_token(v)
            if n:
                variants.add(n)
    if not variants:
        return None
    # Descending length so "Limited Liability Company" beats "Limited" or
    # "Company" alone in the alternation; otherwise the iterative strip
    # would chip off the shorter form first and miss the multi-word match.
    sorted_variants = sorted(variants, key=lambda s: (-len(s), s))
    escaped = [re.escape(v) for v in sorted_variants]
    pattern = re.compile(
        r"[\s,\-.]+(?:" + "|".join(escaped) + r")[\s.,]*$",
        re.IGNORECASE,
    )
    return _BusinessState(pattern=pattern, variants_normalized=frozenset(variants))


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
                "goldenmatch.refdata.business: data file unavailable (%s); "
                "strip_legal_form will be a no-op.",
                exc,
            )
            _state = None


def _reload() -> None:
    """Test-only: drop the cached state so the next access re-parses.

    Atomic: we set ``_state = None`` then return; the next reader will
    drive ``_load`` under the lock. Compared to mutating the old state
    dict in place, this never exposes a half-built state to a concurrent
    reader."""
    global _state
    with _lock:
        _state = None


def is_available() -> bool:
    _load()
    return _state is not None


def known_variants() -> frozenset[str]:
    _load()
    if _state is None:
        return frozenset()
    return _state.variants_normalized


def strip_legal_form(value: str | None) -> str | None:
    """Remove a trailing legal-form suffix from a business name.

    "Acme Inc." → "Acme", "Acme Limited Liability Company" → "Acme",
    "Acme GmbH" → "Acme". Idempotent. If no known suffix matches, the
    value is returned with whitespace collapsed only. ``None`` → ``None``.
    Data file missing → whitespace-collapse pass-through.
    """
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return cleaned
    _load()
    if _state is None:
        return cleaned
    # Iterative strip handles compound suffixes like "Acme Holdings Inc."
    # Bound prevents pathological inputs from spinning forever.
    pattern = _state.pattern
    for _ in range(4):
        new = pattern.sub("", cleaned).strip()
        if new == cleaned or not new:
            cleaned = new if new else cleaned
            break
        cleaned = new
    return cleaned


class LegalFormStripTransform(TransformPlugin):
    """Adapter exposing ``strip_legal_form`` through the
    ``goldenmatch.plugins.base.TransformPlugin`` protocol."""

    name = "legal_form_strip"

    def transform(self, value: str | None) -> str | None:
        return strip_legal_form(value)


def register_transforms() -> None:
    """Idempotent. Called from ``goldenmatch.refdata.__init__`` on import."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(LegalFormStripTransform.name):
        reg.register_transform(LegalFormStripTransform.name, LegalFormStripTransform())
