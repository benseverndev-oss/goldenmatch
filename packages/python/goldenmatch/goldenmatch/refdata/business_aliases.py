"""Business / brand alias canonicalization for candidate-generation blocking.

Maps known brand / synonym / abbreviation surface forms of a company to a
single canonical key, so records that name the same company differently
("Google" vs "Alphabet Inc.", "IBM" vs "International Business Machines")
collapse to the same value when used as a blocking transform — landing them
in the same candidate set before scoring.

Public API:

- ``canonical_company_form(name)`` — the canonical key for ``name``. The
  input is normalized (lower-case; trailing legal-form stripped via
  ``business.strip_legal_form``; whitespace collapsed) and then mapped
  through the bundled alias table. An OOV name maps to its own normalized
  form (stable, idempotent passthrough). ``None`` → ``None``.
- ``is_available()`` — True iff the bundled alias file loaded.
- ``known_canonicals()`` — frozen set of canonical keys, for diagnostics.

Two ``TransformPlugin`` adapters live here:

- ``BusinessCanonicalTransform`` (``refdata_business_canonical``) wraps
  ``canonical_company_form``.
- ``GivenNameCanonicalTransform`` (``refdata_given_name_canonical``) wraps
  ``given_names.canonical_form`` so first-name nickname variants
  ("Bob" / "Robert") share a blocking key.

Loading is lazy: parsed on first call and cached. A missing data file
degrades to legal-form-stripped passthrough rather than raising.
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
from goldenmatch.refdata.business import strip_legal_form

logger = logging.getLogger(__name__)

_DATA_FILE = "business_aliases.json"


@dataclass(frozen=True)
class _BusinessAliasState:
    """Loaded state. Frozen for atomic-swap semantics on ``_reload`` —
    readers don't take the lock, so an in-place dict mutation would race.

    ``surface_to_canonical`` maps each normalized surface form (canonical
    keys included, so they map to themselves) to its canonical key.
    """

    surface_to_canonical: Mapping[str, str]
    canonicals: frozenset[str]


_lock = Lock()
_state: _BusinessAliasState | None = None


def _normalize(name: str) -> str:
    """Lower-case, strip a trailing legal-form suffix, collapse whitespace.

    Mirrors the normalization the alias table keys/values were authored in,
    so lookups hit. ``strip_legal_form`` already collapses whitespace, but
    we collapse again defensively for the data-file-missing path.
    """
    stripped = strip_legal_form(name) or ""
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _build_state_from_file() -> _BusinessAliasState | None:
    surface_to_canonical: dict[str, str] = {}
    canonicals: set[str] = set()
    with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
        "r", encoding="utf-8"
    ) as f:
        payload = json.load(f)
    aliases_block = payload.get("aliases", {})
    for raw_canonical, raw_aliases in aliases_block.items():
        c = _normalize(raw_canonical)
        if not c:
            continue
        canonicals.add(c)
        # The canonical key maps to itself so a record already in canonical
        # form is recognized.
        surface_to_canonical.setdefault(c, c)
        for raw_alias in raw_aliases:
            a = _normalize(raw_alias)
            if not a:
                continue
            # First canonical wins on a collision; log so the bundled file
            # can be deconflicted rather than silently last-winning.
            existing = surface_to_canonical.get(a)
            if existing is not None and existing != c:
                logger.warning(
                    "goldenmatch.refdata.business_aliases: surface form %r maps "
                    "to both %r and %r in %s; keeping %r.",
                    a, existing, c, _DATA_FILE, existing,
                )
                continue
            surface_to_canonical[a] = c
    if not surface_to_canonical:
        return None
    return _BusinessAliasState(
        surface_to_canonical=dict(surface_to_canonical),
        canonicals=frozenset(canonicals),
    )


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
                "goldenmatch.refdata.business_aliases: data file unavailable "
                "(%s); canonical_company_form falls back to normalized "
                "passthrough.",
                exc,
            )
            _state = None


def _reload() -> None:
    """Test-only: drop the cached state; next access re-parses."""
    global _state
    with _lock:
        _state = None


def is_available() -> bool:
    _load()
    return _state is not None


def known_canonicals() -> frozenset[str]:
    _load()
    if _state is None:
        return frozenset()
    return _state.canonicals


def canonical_company_form(name: str | None) -> str | None:
    """Canonical company key for ``name``.

    Normalizes (lower-case, legal-form-stripped, whitespace-collapsed) then
    maps through the alias table. An OOV name returns its own normalized
    form — stable and idempotent. ``None`` → ``None``; empty / whitespace-
    only → ``""``. Data file missing → normalized passthrough.
    """
    if name is None:
        return None
    norm = _normalize(name)
    if not norm:
        return ""
    _load()
    if _state is None:
        return norm
    return _state.surface_to_canonical.get(norm, norm)


class BusinessCanonicalTransform(TransformPlugin):
    """Adapter exposing ``canonical_company_form`` through the
    ``TransformPlugin`` protocol. Use the ``refdata_business_canonical``
    transform name in a matchkey's ``transforms:`` list to block on the
    canonical company form."""

    name = "refdata_business_canonical"

    def transform(self, value: str | None) -> str | None:
        return canonical_company_form(value)


class GivenNameCanonicalTransform(TransformPlugin):
    """Adapter exposing ``given_names.canonical_form`` through the
    ``TransformPlugin`` protocol. Use the ``refdata_given_name_canonical``
    transform name to block on the canonical given-name form so nickname
    variants (Bob/Robert) share a blocking key."""

    name = "refdata_given_name_canonical"

    def transform(self, value: str | None) -> str | None:
        from goldenmatch.refdata.given_names import canonical_form

        return canonical_form(value)


def register_transforms() -> None:
    """Idempotent. Called from ``goldenmatch.refdata.__init__`` on import."""
    from goldenmatch.plugins.registry import PluginRegistry

    reg = PluginRegistry.instance()
    for plugin_cls in (BusinessCanonicalTransform, GivenNameCanonicalTransform):
        if not reg.has_transform(plugin_cls.name):
            reg.register_transform(plugin_cls.name, plugin_cls())
