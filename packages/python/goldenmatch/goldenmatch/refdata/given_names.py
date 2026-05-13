"""Given-name alias lookup.

Mirrors the surnames module's shape: a small bundled reference table plus
a lookup API. The data file maps canonical English given names to lists
of their common nicknames / short forms; equivalence is symmetric and
transitive within a list.

Public API:

- ``canonical_form(name)`` — formal name for an input (so ``"Bobby"`` → ``"robert"``);
  returns the normalized input itself if the name is its own canonical or OOV.
- ``aliases_of(name)`` — full equivalence class (all known forms of the same name);
  empty set if name is OOV.
- ``are_equivalent(a, b)`` — True iff ``a`` and ``b`` share a canonical (or are
  identical after normalization).
- ``is_available()`` — True iff the bundled file was found at import time.

Lookup is case-insensitive; non-alpha characters are stripped before lookup.
Loading is lazy: parsed on first call and cached.
"""
from __future__ import annotations

import json
import logging
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "given_name_aliases.json"

_lock = Lock()
_state: dict = {
    "loaded": False,
    "available": False,
    # name -> set of canonicals it belongs to. Most forms have ONE canonical,
    # but ambiguous short forms (e.g. "kate" — Catherine, Kathleen, Kaitlyn;
    # "chris" — Christopher, Christine, Christina) belong to several. Storing
    # the full set keeps are_equivalent symmetric.
    "canonicals": {},
    # canonical -> frozenset of all equivalent forms (including canonical).
    "classes": {},
}


def _normalize(name: str) -> str:
    """Strip non-alpha, lower-case. Empty input returns empty string."""
    return "".join(ch for ch in name if ch.isalpha()).lower()


def _load() -> None:
    if _state["loaded"]:
        return
    with _lock:
        if _state["loaded"]:
            return
        canonicals: dict[str, set[str]] = {}
        classes: dict[str, set[str]] = {}
        try:
            with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
                "r", encoding="utf-8"
            ) as f:
                # object_pairs_hook lets us spot duplicate canonical keys.
                # json.load silently last-wins on dupes; an editing accident
                # (the file had "anthony" twice in the v1 ship before the
                # PR #217 review) would otherwise lose alias entries
                # invisibly. Log a WARNING so the data-file editor sees it.
                def _dupe_aware_dict(pairs: list[tuple[str, object]]) -> dict[str, object]:
                    seen: set[str] = set()
                    for k, _ in pairs:
                        if k in seen:
                            logger.warning(
                                "goldenmatch.refdata.given_names: duplicate "
                                "canonical key %r in %s — last value wins, "
                                "earlier alias entries are lost.",
                                k, _DATA_FILE,
                            )
                        seen.add(k)
                    return dict(pairs)

                payload = json.load(f, object_pairs_hook=_dupe_aware_dict)
            aliases_block = payload.get("aliases", {})
            for raw_canonical, raw_aliases in aliases_block.items():
                c = _normalize(raw_canonical)
                if not c:
                    continue
                bucket = classes.setdefault(c, {c})
                bucket.add(c)
                for raw_alias in raw_aliases:
                    a = _normalize(raw_alias)
                    if not a:
                        continue
                    bucket.add(a)
                # Every form in this class belongs to canonical c. A form
                # like "kate" may also belong to other canonicals (Kathleen,
                # Kaitlyn) — we accumulate every canonical it appears under.
                for form in bucket:
                    canonicals.setdefault(form, set()).add(c)
            _state["available"] = bool(canonicals)
            _state["canonicals"] = {k: frozenset(v) for k, v in canonicals.items()}
            _state["classes"] = {k: frozenset(v) for k, v in classes.items()}
        except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "goldenmatch.refdata.given_names: data file unavailable (%s); "
                "lookups will return empty.",
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
        _state["canonicals"] = {}
        _state["classes"] = {}
    _load()


def is_available() -> bool:
    """True iff the bundled given-name alias data was found and parsed."""
    _load()
    return _state["available"]


def canonical_form(name: str | None) -> str | None:
    """*A* canonical formal name for ``name``.

    For unambiguous forms this is the one canonical the form belongs to.
    For ambiguous short forms that belong to several canonicals (e.g.
    "kate" — Catherine / Kathleen / Kaitlyn), returns the
    lexicographically-first canonical for stability; use ``aliases_of``
    or ``are_equivalent`` when full membership matters.

    Returns the normalized input if the name has no known canonical
    (OOV); ``None`` only for ``None`` input.
    """
    if name is None:
        return None
    norm = _normalize(name)
    if not norm:
        return ""
    _load()
    canon_set = _state["canonicals"].get(norm)
    if canon_set is None:
        return norm
    return min(canon_set)


def aliases_of(name: str | None) -> frozenset[str]:
    """Union of all equivalence classes ``name`` belongs to.

    For unambiguous forms this is the single class. For ambiguous short
    forms ("kate") it's the union across every canonical the form
    appears under — i.e. the full set of names known to be interchangeable
    with the input in any sense.

    Empty set if input is None / empty / OOV.
    """
    if name is None:
        return frozenset()
    norm = _normalize(name)
    if not norm:
        return frozenset()
    _load()
    canon_set = _state["canonicals"].get(norm)
    if canon_set is None:
        return frozenset()
    out: set[str] = set()
    for c in canon_set:
        out |= _state["classes"].get(c, frozenset())
    return frozenset(out)


def are_equivalent(a: str | None, b: str | None) -> bool:
    """True iff ``a`` and ``b`` are known forms of the same name.

    Symmetric. Reflexive on non-None inputs (any non-empty string equals
    itself after normalization). Returns False if either side is None or
    normalizes to the empty string.

    Two forms are equivalent if they share at least one canonical.
    """
    if a is None or b is None:
        return False
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    _load()
    canons_a = _state["canonicals"].get(na)
    canons_b = _state["canonicals"].get(nb)
    if not canons_a or not canons_b:
        return False
    return not canons_a.isdisjoint(canons_b)
