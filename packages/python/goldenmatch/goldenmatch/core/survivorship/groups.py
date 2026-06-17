"""Field-group detection: explicit > infermap-fed > heuristic. Spec section 2."""
from __future__ import annotations

import logging

import polars as pl

from goldenmatch.config.schemas import GoldenGroupRule

logger = logging.getLogger(__name__)

_HEURISTIC_GROUPS = {
    "address": ["street", "address", "addr", "city", "state", "province", "zip", "zipcode", "postal", "postcode"],
    "person_name": ["first_name", "firstname", "given", "last_name", "lastname", "surname", "family", "middle", "suffix"],
    "contact": ["phone", "phone_number", "email", "email_address", "mobile", "fax"],
}


def _match_members(columns, hints):
    low = {c.lower(): c for c in columns}
    return [low[c] for c in low if any(h in c for h in hints)]


def detect_groups_heuristic(df: pl.DataFrame) -> list[GoldenGroupRule]:
    out = []
    for category, hints in _HEURISTIC_GROUPS.items():
        members = _match_members(df.columns, hints)
        if len(members) >= 2:
            out.append(GoldenGroupRule(name=category, columns=members, category=category))
    return out


def _disjoint_add(accepted: list[GoldenGroupRule], candidates: list[GoldenGroupRule]) -> None:
    """Add candidates whose column set is disjoint from already-accepted groups."""
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
            infermap_groups = infermap_fed_groups(df, pack)
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


def infermap_fed_groups(df, pack) -> list[GoldenGroupRule]:
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
