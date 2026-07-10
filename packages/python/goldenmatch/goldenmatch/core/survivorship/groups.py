"""Field-group detection: explicit > infermap-fed > heuristic. Spec section 2."""
from __future__ import annotations

import logging
import re

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import GoldenGroupRule

logger = logging.getLogger(__name__)

_HEURISTIC_GROUPS = {
    "address": ["street", "address", "addr", "city", "state", "province", "zip", "zipcode", "postal", "postcode"],
    "person_name": ["first_name", "firstname", "given", "last_name", "lastname", "surname", "family", "middle", "suffix"],
    "contact": ["phone", "phone_number", "email", "email_address", "mobile", "fax"],
}


def _match_members(columns: list[str], hints: list[str]) -> list[str]:
    """Return columns whose lowercased name contains a hint as a whole token
    (bounded by start, end, or `_`), so `state` does not match `real_estate`."""
    result = []
    for col in columns:
        normalized = col.lower()
        for hint in hints:
            if re.search(r"(?:^|_)" + re.escape(hint) + r"(?:_|$)", normalized):
                result.append(col)
                break
    return result


def detect_groups_heuristic(df: pl.DataFrame) -> list[GoldenGroupRule]:
    """Conservative name-based detection of address/person_name/contact groups.

    Categories are matched most-specific-first (contact, person_name, address) so
    an ambiguous column like `email_address` (which matches both `contact` and
    `address`) is claimed by the more specific category. A column matched by a
    more-specific category is never absorbed by a less-specific one, even when the
    specific category did not reach the >=2-member floor to form a group.
    """
    out = []
    claimed_by_more_specific: set[str] = set()
    for category in ("contact", "person_name", "address"):
        hints = _HEURISTIC_GROUPS[category]
        candidates = _match_members(df.columns, hints)
        members = [c for c in candidates if c not in claimed_by_more_specific]
        if len(members) >= 2:
            out.append(GoldenGroupRule(name=category, columns=members, category=category))
        claimed_by_more_specific.update(candidates)
    return out


def _disjoint_add(accepted: list[GoldenGroupRule], candidates: list[GoldenGroupRule]) -> None:
    """Add candidates whose column set is disjoint from already-accepted groups.

    A candidate is dropped ENTIRELY if ANY of its columns is already claimed by a
    higher-precedence group (no partial keeping). This is how explicit groups win:
    naming even one column of a group suppresses a lower-precedence group over it.
    """
    claimed = {c for g in accepted for c in g.columns}
    for cand in candidates:
        if any(c in claimed for c in cand.columns):
            continue
        accepted.append(cand)
        claimed.update(cand.columns)


def build_field_groups(df, pack=None, *, explicit=None, enabled=False, infermap_groups=None) -> list[GoldenGroupRule]:
    """Union the three sources with precedence explicit > infermap > heuristic.
    Dedupe key = column set (disjointness). Detection (infermap+heuristic) is
    skipped entirely when `enabled` is False; explicit groups always returned."""
    accepted: list[GoldenGroupRule] = list(explicit or [])
    if not enabled:
        return accepted
    try:
        if infermap_groups is None and pack is not None:
            infermap_groups = _infermap_fed_groups(df, pack)
        if infermap_groups:
            _disjoint_add(accepted, infermap_groups)
        _disjoint_add(accepted, detect_groups_heuristic(df))
    except Exception as exc:  # fail-open
        logger.warning("field-group detection failed (%s); using explicit only", exc)
    return accepted


def _infermap_canonical_map(df, pack) -> dict:
    """source_col -> canonical name, via infermap. Lazy import (infermap optional)."""
    import infermap
    from infermap.domain_pack import DomainPackTarget
    result = infermap.map(df, DomainPackTarget(pack))
    return {m.source: m.target for m in result.mappings if m.target}


def _pack_groups(pack) -> list[tuple[str, list[str]]]:
    return [(g.name, list(g.members)) for g in getattr(pack, "groups", [])]


def _infermap_fed_groups(df, pack) -> list[GoldenGroupRule]:
    """Map a DomainPack's canonical groups onto real source columns via infermap.
    Fail-open: any error (incl. ImportError) -> []."""
    if pack is None:
        return []
    try:
        canon_map = _infermap_canonical_map(df, pack)            # source -> canonical
        inverse: dict[str, list[str]] = {}
        for src, canon in canon_map.items():
            inverse.setdefault(canon, []).append(src)
        out = []
        for name, members in _pack_groups(pack):
            real_cols = [c for m in members for c in inverse.get(m, [])]
            if len(real_cols) >= 2:
                out.append(GoldenGroupRule(name=name, columns=real_cols, category=name))
        return out
    except Exception as exc:
        logger.warning("infermap-fed group detection failed (%s); skipping", exc)
        return []
