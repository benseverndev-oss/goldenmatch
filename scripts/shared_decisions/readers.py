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


def _known_class_names_lower() -> set[str]:
    return {name.lower() for name in config_fields()}


def _looks_like_config_base(base_id: str, class_names_lower: set[str]) -> bool:
    """Does an attribute base name look like it holds a config object?

    Two ways in: the literal substring rule ("config"/"cfg" in the name, e.g.
    `blocking_config`), or -- because this codebase's convention is to name a
    config-holding variable after the config *type* it holds, often shortened
    (e.g. `blocking` for a `BlockingConfig`) -- the name, lowercased with
    underscores stripped, is a PREFIX of some known config class name,
    lowercased. `blocking` prefixes `blockingconfig`, so `blocking.passes`
    counts even though neither "config" nor "cfg" appears in `blocking`.

    This deliberately over-matches (a base that happens to prefix some config
    class name, holding something else, reading an attribute that happens to
    share a field name) rather than under-match: a false entry in this
    report-only, human-triaged inventory costs one triage decision, while a
    missed reader is invisible -- which is exactly how the 1c843c8a5 incident
    shipped undetected.
    """
    low = base_id.lower()
    if "config" in low or "cfg" in low:
        return True
    stripped = low.replace("_", "")
    return any(cls.startswith(stripped) for cls in class_names_lower)


def field_readers(root: Path) -> dict[str, set[str]]:
    """Map each config field name to the modules under `root` that read it.

    An attribute read counts when its base is a plain name that looks like it
    holds a config object -- see `_looks_like_config_base`. That keeps
    `self.keys` and dict `.keys()` out while still catching `blocking.passes`
    (base `blocking`, no "config"/"cfg" substring, but a prefix of the known
    class `BlockingConfig`).
    """
    known = _known_field_names()
    class_names_lower = _known_class_names_lower()
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
            if _looks_like_config_base(base.id, class_names_lower):
                out[node.attr].add(rel)
    return dict(out)


def shared_fields(root: Path) -> dict[str, set[str]]:
    """Fields read by MORE THAN ONE module -- the ones whose readers must agree."""
    return {f: mods for f, mods in field_readers(root).items() if len(mods) > 1}
