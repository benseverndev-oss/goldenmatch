"""Alias scorer — matches fields that are known synonyms of each other.

The ``ALIASES`` module-level dict is seeded from
``infermap/dictionaries/generic.yaml`` at import time and can be extended
via ``infermap.yaml`` config (see ``MapEngine._apply_config``).

For domain-specific aliases (healthcare, finance, ecommerce, ...), pass
``MapEngine(domains=[...])`` — the engine builds an alias dict that merges
generic + requested domains and constructs a per-engine AliasScorer with
it. The module-level ``ALIASES`` is unchanged in that case, so code that
imports it directly keeps working.
"""
from __future__ import annotations

from infermap.dictionaries import load_domain
from infermap.types import FieldInfo, ScorerResult


def build_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    """Build a reverse lookup: every alias (and canonical) -> canonical key."""
    lookup: dict[str, str] = {}
    for canonical, alias_list in aliases.items():
        lookup[canonical] = canonical
        for alias in alias_list:
            lookup[alias.strip().lower()] = canonical
    return lookup


# Default alias dict, seeded from the shipped `generic` domain YAML.
# Kept as a module-level mutable dict so existing code that imports and
# mutates ALIASES (tests, user configs, engine._apply_config) keeps working.
ALIASES: dict[str, list[str]] = load_domain("generic")
_ALIAS_LOOKUP: dict[str, str] = build_lookup(ALIASES)


def _get_canonical(name: str) -> str | None:
    """Return the canonical form of *name* per the module-level lookup.

    Used by AliasScorer instances that don't have their own per-instance
    lookup (i.e. the default constructed by ``default_scorers()``).
    """
    return _ALIAS_LOOKUP.get(name.strip().lower())


class AliasScorer:
    """Scores 0.95 when both fields resolve to the same canonical name.

    Parameters
    ----------
    aliases:
        Optional per-instance alias dict. When provided, the scorer builds
        its own lookup and does not read the module-level ``ALIASES``.
        When ``None`` (default), uses the module-level dict so any
        mutations (e.g. ``engine._apply_config``) are picked up live.
    """

    name: str = "AliasScorer"
    weight: float = 0.95

    def __init__(self, aliases: dict[str, list[str]] | None = None):
        if aliases is not None:
            self._lookup: dict[str, str] | None = build_lookup(aliases)
        else:
            self._lookup = None

    def _canonical(self, name: str) -> str | None:
        if self._lookup is not None:
            return self._lookup.get(name.strip().lower())
        return _get_canonical(name)

    def score(self, source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
        src_name = source.name.strip().lower()
        tgt_name = target.name.strip().lower()

        src_canonical = self._canonical(src_name)
        tgt_canonical = self._canonical(tgt_name)

        # Check schema-file declared aliases on target
        declared_aliases: list[str] = target.metadata.get("aliases", [])
        declared_aliases_lower = [a.strip().lower() for a in declared_aliases]

        target_has_declared = bool(declared_aliases)

        # If source name is in target's declared aliases → strong match
        if src_name in declared_aliases_lower:
            return ScorerResult(
                score=0.95,
                reasoning=(
                    f"'{source.name}' matches declared alias of target '{target.name}'"
                ),
            )

        # If neither field has a known alias and target has no declared aliases → abstain
        if src_canonical is None and tgt_canonical is None and not target_has_declared:
            return None

        # Both resolve to the same canonical → match
        if src_canonical is not None and src_canonical == tgt_canonical:
            return ScorerResult(
                score=0.95,
                reasoning=(
                    f"'{source.name}' and '{target.name}' share canonical name '{src_canonical}'"
                ),
            )

        # Different canonicals (or one is unknown) → no match
        return ScorerResult(
            score=0.0,
            reasoning=(
                f"'{source.name}' (canonical={src_canonical}) and "
                f"'{target.name}' (canonical={tgt_canonical}) are different"
            ),
        )
