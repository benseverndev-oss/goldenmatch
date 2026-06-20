"""Per-domain synonym alias table (the deterministic fast-path + GS2's training
source). Mirrors the refdata given-name alias loader: a canonical->[aliases] JSON
expanded to a symmetric equivalence lookup, with graceful absence (`empty()`)."""

from __future__ import annotations

import json
import re
from pathlib import Path

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    return _NON_ALNUM.sub("", s.casefold())


class SynonymTable:
    """Symmetric synonym-equivalence over normalized surface forms.

    Built from `{"aliases": {canonical: [alias, ...]}}`. Every member of a group
    (canonical + aliases) is equivalent to every other. Absent file -> `empty()`
    (`are_equivalent` always False, `is_available()` False)."""

    def __init__(self, groups: list[set[str]] | None = None):
        # normalized surface form -> group id
        self._group_of: dict[str, int] = {}
        self._available = bool(groups)
        for gid, group in enumerate(groups or []):
            for member in group:
                self._group_of[member] = gid

    @classmethod
    def empty(cls) -> "SynonymTable":
        return cls(None)

    @classmethod
    def from_json(cls, path: str | Path) -> "SynonymTable":
        p = Path(path)
        if not p.exists():
            return cls.empty()
        payload = json.loads(p.read_text(encoding="utf-8"))
        groups: list[set[str]] = []
        for canonical, aliases in payload.get("aliases", {}).items():
            members = {_normalize(canonical)} | {_normalize(a) for a in aliases}
            members.discard("")
            if len(members) >= 2:
                groups.append(members)
        return cls(groups)

    def is_available(self) -> bool:
        return self._available

    def are_equivalent(self, a: str | None, b: str | None) -> bool:
        if not a or not b:
            return False
        na, nb = _normalize(a), _normalize(b)
        if not na or not nb:
            return False
        ga = self._group_of.get(na)
        return ga is not None and ga == self._group_of.get(nb)
