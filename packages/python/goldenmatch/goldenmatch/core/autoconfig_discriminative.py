"""Discriminative-power veto for exact/identity matchkeys (#1351).

Cardinality cannot separate a shared identity key (``npi``: records sharing a
value are the SAME entity) from a shared locality attribute (``zip``: records
sharing a value are DIFFERENT people in one area) -- both have moderate
cardinality. This module measures, from the data, whether records that SHARE a
candidate value also AGREE on other identity fields. Low co-agreement => the
value is a locality/attribute, not an identity key => veto its standalone exact
matchkey (demote to blocking-only). Veto-only; never promotes.
"""
from __future__ import annotations

import os
from difflib import SequenceMatcher
from typing import Any

from goldenmatch._polars_lazy import pl

_IDENTITY_BASKET_TYPES = frozenset({"name", "multi_name", "email", "phone", "identifier"})
# Basket types compared FUZZILY (a true duplicate's name is often corrupted --
# "Smith"/"Smyth" -- so exact-equality would read a genuine identity key's
# shared-value pairs as disagreement and wrongly veto it, e.g. soc_sec_id on
# febrl3). Structured ids (email/phone/identifier) are compared exactly: a
# near-miss there means a DIFFERENT entity, not a corruption of the same one.
_NAME_FUZZY_TYPES = frozenset({"name", "multi_name"})

_TAU_DEFAULT = 0.5
_MIN_SHARED_PAIRS = 20
_MAX_PAIRS = 200
# SequenceMatcher ratio at/above which two name strings count as agreement.
_AGREE_THRESHOLD = 0.85


def veto_enabled() -> bool:
    """Kill-switch: GOLDENMATCH_DISCRIMINATIVE_VETO=0 disables the veto."""
    return os.environ.get("GOLDENMATCH_DISCRIMINATIVE_VETO", "1") != "0"


def tau() -> float:
    """Co-agreement floor below which an exact key is vetoed (env-overridable)."""
    raw = os.environ.get("GOLDENMATCH_DISCRIMINATIVE_TAU")
    if raw is None:
        return _TAU_DEFAULT
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return _TAU_DEFAULT
    return val if 0.0 <= val <= 1.0 else _TAU_DEFAULT


def identity_basket(candidate_col: str, profiles: list[Any]) -> list[tuple[str, bool]]:
    """Other identity-typed columns as ``(name, fuzzy)`` pairs (excludes candidate).

    ``fuzzy`` is True for name-typed columns (compared with a similarity
    threshold to tolerate duplicate-record corruption), False for structured
    identity types (email/phone/identifier, compared exactly).
    """
    out: list[tuple[str, bool]] = []
    for p in profiles:
        col_type = getattr(p, "col_type", None)
        if p.name != candidate_col and col_type in _IDENTITY_BASKET_TYPES:
            out.append((p.name, col_type in _NAME_FUZZY_TYPES))
    return out


def _norm(v: Any) -> str | None:
    """Normalize a cell for equality: str -> stripped lower; blank/None -> None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _agree(a: str, b: str, fuzzy: bool) -> bool:
    """Whether two normalized cells agree. Exact match always counts; for fuzzy
    (name) fields, a SequenceMatcher ratio >= _AGREE_THRESHOLD also counts."""
    if a == b:
        return True
    if not fuzzy:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _AGREE_THRESHOLD


def discriminative_power(
    df: pl.DataFrame,
    candidate_col: str,
    basket: list[tuple[str, bool]],
    *,
    max_pairs: int = _MAX_PAIRS,
    min_group_size: int = 2,
) -> tuple[float, int]:
    """Mean co-agreement over shared-value pairs, and support (n pairs measured).

    ``basket`` is a list of ``(column, fuzzy)`` pairs. Groups df by
    candidate_col; for value-groups with >=2 rows, forms up to max_pairs
    record-pairs deterministically (row 0 paired with rows 1..k-1 within each
    group, groups visited in sorted-value order). Per pair, agreement =
    (# basket fields where BOTH cells are non-null and :func:`_agree`) /
    (# basket fields where BOTH are non-null); name-typed fields agree fuzzily,
    structured ids exactly. Pairs with no jointly-populated basket field are
    skipped. Returns (mean over measured pairs, count of measured pairs);
    (0.0, 0) when basket empty, candidate absent, or no measurable pair exists.
    """
    from goldenmatch.core.frame import to_frame

    frame = to_frame(df)  # arrow-port: seam columns + select_dicts (row order preserved)
    if not basket or candidate_col not in frame.columns:
        return 0.0, 0
    basket_cols = [(c, fuzzy) for (c, fuzzy) in basket if c in frame.columns]
    if not basket_cols:
        return 0.0, 0
    sub_cols = [candidate_col, *[c for (c, _f) in basket_cols]]

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in frame.select_dicts(sub_cols):
        cv = _norm(row[candidate_col])
        if cv is None:
            continue
        groups.setdefault(cv, []).append(row)

    total = 0.0
    measured = 0
    _floor = max(2, min_group_size)
    for cv in sorted(groups):
        rows = groups[cv]
        if len(rows) < _floor or measured >= max_pairs:
            continue
        anchor = rows[0]
        for other in rows[1:]:
            if measured >= max_pairs:
                break
            agree = 0
            comparable = 0
            for c, fuzzy in basket_cols:
                a, b = _norm(anchor[c]), _norm(other[c])
                if a is None or b is None:
                    continue
                comparable += 1
                if _agree(a, b, fuzzy):
                    agree += 1
            if comparable == 0:
                continue
            total += agree / comparable
            measured += 1
    if measured == 0:
        return 0.0, 0
    return total / measured, measured


def should_veto_exact(
    df: pl.DataFrame | None,
    candidate_col: str,
    profiles: list[Any],
    *,
    min_shared_pairs: int = _MIN_SHARED_PAIRS,
    max_pairs: int = _MAX_PAIRS,
) -> bool:
    """True => demote the proposed standalone exact matchkey on candidate_col.

    Fail-safe = keep (False) on: kill-switch off, df is None, empty identity
    basket, or insufficient shared-value support. Only vetoes a high-density
    column whose shared-value pairs measurably fail to co-agree on other identity
    fields (support >= min_shared_pairs AND power < tau()).
    """
    if not veto_enabled() or df is None:
        return False
    basket = identity_basket(candidate_col, profiles)
    if not basket:
        return False
    power, support = discriminative_power(df, candidate_col, basket, max_pairs=max_pairs)
    if support < min_shared_pairs:
        return False
    return power < tau()


# ---------------------------------------------------------------------------
# Weighted-fuzzy ATTRIBUTE demotion (single-source workplace/locality attributes)
# ---------------------------------------------------------------------------

# Col_types eligible for the group-attribute demotion (an exact OR a weighted use).
# These are all the field kinds that CAN legitimately back a matchkey but can ALSO
# be a shared group/list/facility signal rather than a person identity:
#   address  -- a shared clinic address
#   phone    -- a shared switchboard line (a personal cell is kept: it co-agrees)
#   email    -- a shared role/team inbox (a personal inbox is kept)
#   identifier -- a mailing-list / campaign id, a facility NPI, a placeholder
# ``name`` is eligible ONLY when it is NOT a person name (an org/employer/dept
# name), so a real first/last-name field is never demoted. The group-size-aware
# measure (see should_demote_attribute_field) is what makes broadening to
# ``identifier``/``email`` safe: a real personal id groups only a handful of
# duplicates (no large group -> kept), while a campaign list / facility id / role
# inbox groups many DIFFERENT people (a large group that fails name co-agreement).
_WORKPLACE_ATTRIBUTE_TYPES = frozenset({"address", "phone", "email", "identifier"})

# A shared-value group of at least this many records is "large". The demotion
# measures name co-agreement over LARGE groups only, so a mostly-unique column
# (e.g. tl_id at 0.53 cardinality) whose FEW big values are campaign lists is
# caught, without a handful of small same-person duplicate groups diluting the
# signal (measured on the DERM list: big-group name-power 0.01 vs small-group 0.80).
_LARGE_GROUP_MIN = 10


def attribute_demotion_enabled() -> bool:
    """Whether to demote an EXACT matchkey on a shared group/list/facility value
    (a shared clinic phone line, a campaign identifier, a facility NPI) whose
    large shared-value groups do not co-agree on the person name. Default ON as of
    v2.7.0: the accuracy sweep (scripts/autoconfig_quality) showed zero F1 change
    across the whole corpus (flag-on == flag-off on every dataset) while it fixes
    real-world group-attribute over-merges. Kill-switch:
    GOLDENMATCH_ATTRIBUTE_DEMOTION=0 (or false/no/off/disabled, case-insensitive)
    restores the pre-2.7.0 behavior."""
    return os.environ.get(
        "GOLDENMATCH_ATTRIBUTE_DEMOTION", "1"
    ).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def should_demote_attribute_field(
    df: pl.DataFrame | None,
    candidate_col: str,
    col_type: str | None,
    name_basket: list[tuple[str, bool]],
    *,
    is_person_name: bool,
    min_shared_pairs: int = _MIN_SHARED_PAIRS,
    max_pairs: int = _MAX_PAIRS,
) -> bool:
    """True => demote a matchkey use of candidate_col to blocking-only (applies
    to an exact OR a weighted-fuzzy use).

    A shared GROUP / LIST / FACILITY value -- a clinic ``address``, a shared
    switchboard ``phone`` line, a generic org ``company`` name, a mailing-list /
    campaign ``identifier`` (``tl_id``), a facility NPI, a role ``email`` inbox --
    is NOT person-identity evidence: the DIFFERENT people sharing it do not
    co-agree on the PERSON NAME. As an exact matchkey or a full-weight fuzzy
    feature it collapses them into a mega-cluster.

    Three deliberate design choices make this general and safe:

    * **Scope** -- eligible col_types are the group/list-capable ones
      (:data:`_WORKPLACE_ATTRIBUTE_TYPES`) plus a non-person ``name``. A real
      first/last-name field is never eligible.
    * **Group-size-aware** -- co-agreement is measured over LARGE shared-value
      groups only (``>= _LARGE_GROUP_MIN``). This is what makes broadening to
      ``identifier``/``email`` safe: a real personal id groups only a few
      duplicates (no large group -> insufficient support -> KEPT), while a
      campaign list / facility id / role inbox groups many different people (a
      large group that fails name co-agreement -> demoted). It also stops a
      mostly-unique column (tl_id, 0.53 cardinality) from being rescued by its
      many small same-person groups averaging the co-agreement up.
    * **Person-name basket** -- co-agreement is measured against person-name
      columns only, NOT the broad #1351 identity basket (which on real data is
      polluted by constant dataset-metadata columns mis-typed as ``identifier``
      and by the shared attribute itself). The person name is the clean anchor.

    Data-measured (no name/value allowlist). Fail-safe = keep (False) on: flag
    off, df is None, an ineligible field, an empty name basket (no person-name
    anchor), or no large shared-value group with enough support.
    """
    if not attribute_demotion_enabled() or df is None:
        return False
    is_eligible = col_type in _WORKPLACE_ATTRIBUTE_TYPES or (
        col_type == "name" and not is_person_name
    )
    if not is_eligible:
        return False
    from goldenmatch.core.frame import to_frame

    _dcols = to_frame(df).columns  # arrow-port: pa.Table.columns != names
    basket = [(c, f) for (c, f) in name_basket if c != candidate_col and c in _dcols]
    if not basket:
        return False
    power, support = discriminative_power(
        df, candidate_col, basket, max_pairs=max_pairs, min_group_size=_LARGE_GROUP_MIN,
    )
    if support < min_shared_pairs:
        return False
    return power < tau()
