"""Given-name alias lookup.

Maps canonical English given names to lists of common nicknames /
short forms; equivalence is symmetric and transitive within a list.

Public API:

- ``canonical_form(name)`` — *a* canonical form for the input (lex-first
  for ambiguous short forms; see docstring). Returns the normalized
  input for OOV names; ``None`` only for ``None`` input.
- ``aliases_of(name)`` — union of every equivalence class the name
  belongs to; empty set if OOV.
- ``are_equivalent(a, b)`` — True iff the two normalized forms share
  at least one canonical.
- ``is_available()`` — True iff the bundled file loaded.

Lookup is case-insensitive; non-alpha characters are stripped before
lookup. Loading is lazy: parsed on first call and cached.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "given_name_aliases.json"


@dataclass(frozen=True)
class _GivenNameState:
    """Loaded state. Frozen for atomic-swap semantics on ``_reload``.

    ``canonicals`` maps each form to the set of canonicals it belongs
    to. Most forms have one canonical, but ambiguous short forms
    ("kate" — Catherine, Kathleen, Kaitlyn; "chris" — Christopher,
    Christine, Christina) belong to several. Storing the full set keeps
    ``are_equivalent`` symmetric.

    ``classes`` is canonical → frozenset of all equivalent forms,
    including the canonical itself.
    """

    canonicals: Mapping[str, frozenset[str]]
    classes: Mapping[str, frozenset[str]]


_lock = Lock()
_state: _GivenNameState | None = None


def _normalize(name: str) -> str:
    return "".join(ch for ch in name if ch.isalpha()).lower()


def _build_state_from_file() -> _GivenNameState | None:
    canonicals: dict[str, set[str]] = {}
    classes: dict[str, set[str]] = {}
    with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
        "r", encoding="utf-8"
    ) as f:
        # object_pairs_hook surfaces duplicate canonical keys at parse time;
        # json.load otherwise last-wins and silently drops earlier entries.
        # An editing accident on the bundled file ("anthony" appeared
        # twice in v1) would otherwise lose alias entries invisibly.
        def _dupe_aware_dict(pairs: list[tuple[str, object]]) -> dict[str, object]:
            seen: set[str] = set()
            for k, _ in pairs:
                if k in seen:
                    logger.warning(
                        "goldenmatch.refdata.given_names: duplicate canonical "
                        "key %r in %s — last value wins, earlier alias entries "
                        "are lost.",
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
        for form in bucket:
            canonicals.setdefault(form, set()).add(c)
    if not canonicals:
        return None
    return _GivenNameState(
        canonicals={k: frozenset(v) for k, v in canonicals.items()},
        classes={k: frozenset(v) for k, v in classes.items()},
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
                "goldenmatch.refdata.given_names: data file unavailable (%s); "
                "lookups will return empty.",
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


def canonical_form(name: str | None) -> str | None:
    """*A* canonical formal name for ``name``.

    Lex-first when the form belongs to multiple canonicals (e.g. "kate"
    → "catherine"). Use ``aliases_of`` or ``are_equivalent`` when the
    full multi-canonical relationship matters. Returns the normalized
    input for OOV names; ``None`` only for ``None`` input.
    """
    if name is None:
        return None
    norm = _normalize(name)
    if not norm:
        return ""
    _load()
    if _state is None:
        return norm
    canon_set = _state.canonicals.get(norm)
    if canon_set is None:
        return norm
    return min(canon_set)


def aliases_of(name: str | None) -> frozenset[str]:
    """Union of every equivalence class ``name`` belongs to."""
    if name is None:
        return frozenset()
    norm = _normalize(name)
    if not norm:
        return frozenset()
    _load()
    if _state is None:
        return frozenset()
    canon_set = _state.canonicals.get(norm)
    if canon_set is None:
        return frozenset()
    out: set[str] = set()
    for c in canon_set:
        out |= _state.classes.get(c, frozenset())
    return frozenset(out)


def are_equivalent(a: str | None, b: str | None) -> bool:
    """True iff ``a`` and ``b`` share at least one canonical.

    Symmetric. Reflexive on non-None inputs.
    """
    if a is None or b is None:
        return False
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    _load()
    if _state is None:
        return False
    canons_a = _state.canonicals.get(na)
    canons_b = _state.canonicals.get(nb)
    if not canons_a or not canons_b:
        return False
    return not canons_a.isdisjoint(canons_b)
