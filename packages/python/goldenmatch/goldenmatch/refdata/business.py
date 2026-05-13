"""Business-name normalization.

Ships the ``legal_form_strip`` transform — removes trailing corporate
legal-form tokens ("Inc", "LLC", "GmbH", "Pty Ltd", etc.) from business
names so that "Acme Inc.", "Acme Incorporated", and "Acme Corp." all
collapse to "Acme" before scoring.

The transform is registered into ``PluginRegistry`` on
``import goldenmatch.refdata``. ``apply_transform`` falls through to the
registry for unknown transform names — see
``goldenmatch.utils.transforms.apply_transform``.

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
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "legal_forms.json"

_lock = Lock()
_state: dict = {
    "loaded": False,
    "available": False,
    # Compiled regex matching any known trailing legal-form variant,
    # anchored at end-of-string, case-insensitive. Built once at load
    # time so per-call cost is one regex sub.
    "pattern": None,
    "variants_normalized": frozenset(),
}


def _normalize_token(t: str) -> str:
    """Lower-case, collapse whitespace, strip trailing periods+commas."""
    t = re.sub(r"\s+", " ", t).strip().rstrip(".,")
    return t.lower()


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
            forms_block = payload.get("legal_forms", {})
            variants: set[str] = set()
            for variant_list in forms_block.values():
                for v in variant_list:
                    n = _normalize_token(v)
                    if n:
                        variants.add(n)
            if not variants:
                _state["available"] = False
                _state["loaded"] = True
                return
            # Sort by descending length so multi-word variants like
            # "Limited Liability Company" are tried before "Limited" or
            # "Company" alone. Otherwise "Acme Limited Liability Company"
            # would strip to "Acme Liability Company" (wrong) instead of
            # "Acme" (right).
            sorted_variants = sorted(variants, key=lambda s: (-len(s), s))
            # Build a single regex: ``[ ,\-.]+(v1|v2|...) [.,]*$`` —
            # whitespace/separator before the suffix, optional trailing
            # punctuation. Case-insensitive.
            escaped = [re.escape(v) for v in sorted_variants]
            pattern = re.compile(
                r"[\s,\-.]+(?:" + "|".join(escaped) + r")[\s.,]*$",
                re.IGNORECASE,
            )
            _state["pattern"] = pattern
            _state["variants_normalized"] = frozenset(variants)
            _state["available"] = True
        except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "goldenmatch.refdata.business: data file unavailable (%s); "
                "strip_legal_form will be a no-op.",
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
        _state["pattern"] = None
        _state["variants_normalized"] = frozenset()
    _load()


def is_available() -> bool:
    _load()
    return _state["available"]


def known_variants() -> frozenset[str]:
    """Every recognised surface variant, normalized lower-case. Diagnostic."""
    _load()
    return _state["variants_normalized"]


def strip_legal_form(value: str | None) -> str | None:
    """Remove a trailing legal-form suffix from a business name.

    "Acme Inc." → "Acme", "Acme Limited Liability Company" → "Acme",
    "Acme GmbH" → "Acme". Idempotent — stripping twice leaves the
    result unchanged. If no known suffix matches, the value is
    returned unchanged (only whitespace-normalized).

    Returns ``None`` for ``None`` input. If the bundled data file is
    missing, returns the input with whitespace collapsed (no stripping).
    """
    if value is None:
        return None
    # Normalize internal whitespace early so multi-word suffixes like
    # "Pty   Ltd" still match.
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return cleaned
    _load()
    pattern = _state["pattern"]
    if pattern is None:
        return cleaned
    # Iterate: a name like "Acme Holdings Inc." has two strippable
    # tokens ("Inc.", then "Holdings"). Strip in a loop until nothing
    # changes, bounded to prevent runaway on adversarial input.
    for _ in range(4):
        new = pattern.sub("", cleaned).strip()
        if new == cleaned or not new:
            cleaned = new if new else cleaned
            break
        cleaned = new
    return cleaned


# ── Plugin protocol adapter ──────────────────────────────────────────────


class LegalFormStripTransform:
    """Adapter exposing ``strip_legal_form`` through the plugin transform
    interface (the registry calls ``.transform(value)`` per the protocol
    in ``goldenmatch.plugins.base``)."""

    name = "legal_form_strip"

    def transform(self, value: str | None) -> str | None:
        return strip_legal_form(value)


def register_transforms() -> None:
    """Register the bundled transforms. Idempotent. Called from
    ``goldenmatch.refdata.__init__`` on import."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    if not reg.has_transform(LegalFormStripTransform.name):
        reg.register_transform(LegalFormStripTransform.name, LegalFormStripTransform())
