"""Which modules read which config fields.

A field read by more than one module is a shared decision: those readers must
agree about what it means, and nothing checks that they do. That is exactly the
1c843c8a5 incident -- score_buckets and blocker.py both read
`blocking_config.passes` and `.keys` and resolved their precedence differently.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from shared_decisions.fields import config_fields


def _known_field_names() -> set[str]:
    names: set[str] = set()
    for fields in config_fields().values():
        names |= fields
    return names


def field_readers(root: Path) -> dict[str, set[str]]:
    """Map each config field name to the modules under `root` that read it.

    Only attribute reads whose base is a plain name containing "config" or
    "cfg" count. That keeps `self.keys` and dict `.keys()` out: the base has to
    look like a config object, which is what the incident's
    `blocking_config.passes` does.
    """
    known = _known_field_names()
    out: dict[str, set[str]] = defaultdict(set)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in known:
                continue
            base = node.value
            if not isinstance(base, ast.Name):
                continue
            low = base.id.lower()
            if "config" in low or "cfg" in low:
                out[node.attr].add(rel)
    return dict(out)


def shared_fields(root: Path) -> dict[str, set[str]]:
    """Fields read by MORE THAN ONE module -- the ones whose readers must agree."""
    return {f: mods for f, mods in field_readers(root).items() if len(mods) > 1}
