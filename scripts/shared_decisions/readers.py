"""Which modules read which config fields.

A field read by more than one module is a shared decision: those readers must
agree about what it means, and nothing checks that they do. That is exactly the
1c843c8a5 incident -- score_buckets and blocker.py both read
`blocking_config.passes` and `.keys` and resolved their precedence differently.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from shared_decisions.fields import config_fields

# Splits a CamelCase class name into its words, lowercased, keeping runs of
# capitals (acronyms) as one word: "LSHKeyConfig" -> ["LSH", "Key", "Config"].
_CAMEL_SEGMENT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*")


def _known_field_names() -> set[str]:
    names: set[str] = set()
    for fields in config_fields().values():
        names |= fields
    return names


def _camel_segments_lower(class_name: str) -> set[str]:
    return {seg.lower() for seg in _CAMEL_SEGMENT_RE.findall(class_name)}


def _config_look_targets() -> tuple[set[str], set[str], set[str]]:
    """The three word-boundary forms a base name can match, derived from the
    known config class names: the full lowercased class name, the class name
    with a trailing "config" removed, and each CamelCase segment.
    """
    full: set[str] = set()
    trimmed: set[str] = set()
    segments: set[str] = set()
    for class_name in config_fields():
        low = class_name.lower()
        full.add(low)
        trimmed.add(low[: -len("config")] if low.endswith("config") else low)
        segments |= _camel_segments_lower(class_name)
    return full, trimmed, segments


def _looks_like_config_name(name: str, targets: tuple[set[str], set[str], set[str]]) -> bool:
    """Does a single name -- a base identifier, or the whole dotted chain it's
    part of -- look like it refers to a config object?

    Two ways in:
    - the literal substring rule ("config"/"cfg" in the name, e.g.
      `blocking_config`, or `config.blocking` as a whole dotted string), or
    - a WORD-BOUNDARY match against a known config class name: the name,
      lowercased with underscores and dots stripped, equals the full class
      name, the class name with its trailing "config" removed, or one of the
      class name's CamelCase segments. `blocking` matches `BlockingConfig`
      via its "Blocking" segment.

    This intentionally does NOT match on a mere prefix. An earlier version
    of this rule matched any base name that PREFIXED a class name, which let
    single-letter loop variables collide with class names that happen to
    start with that letter -- `c.trust` in cli/memory.py matched only because
    "c" prefixes "canopyconfig" (`c` there is a `Correction` record, from
    `for c in corrections:`), and `f.guard` in core/pipeline.py matched only
    because "f" prefixes "fieldtransform" (`f` there is a matchkey field, from
    `for f in mk.fields`). Word-boundary equality rejects both: "c" and "f"
    are not equal to any class name, its config-stripped form, or a CamelCase
    segment. No minimum length is used instead -- a word boundary is the
    principled cut; an arbitrary length floor is a guess.
    """
    low = name.lower()
    if "config" in low or "cfg" in low:
        return True
    stripped = low.replace("_", "").replace(".", "")
    full, trimmed, segments = targets
    return stripped in full or stripped in trimmed or stripped in segments


def _base_chain(node: ast.expr) -> list[str] | None:
    """Walk a Name/Attribute chain to its segments, e.g. `config.blocking` ->
    ["config", "blocking"].

    Real code writes `config.blocking.passes`, where the base of the
    `.passes` read is the Attribute node `config.blocking`, not a plain Name
    -- a bare-`ast.Name` check alone misses it. Returns None when the chain
    bottoms out in anything else (a call result, a subscript, ...), which
    this scan cannot attribute to a config object.
    """
    segments: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        segments.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    segments.append(cur.id)
    segments.reverse()
    return segments


def field_readers(root: Path) -> dict[str, set[str]]:
    """Map each config field name to the modules under `root` that read it.

    An attribute read counts when its base -- a bare name (`blocking_config`)
    or a dotted chain (`config.blocking`) -- looks like it holds a config
    object: see `_looks_like_config_name`. Both the whole dotted chain and
    each of its segments are checked, so `config.blocking.passes` matches on
    its `config` segment even though the immediate base of `.passes` is the
    Attribute node `config.blocking`, not a plain Name.
    """
    known = _known_field_names()
    targets = _config_look_targets()
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
            segments = _base_chain(node.value)
            if segments is None:
                continue
            dotted = ".".join(segments)
            if _looks_like_config_name(dotted, targets) or any(
                _looks_like_config_name(seg, targets) for seg in segments
            ):
                out[node.attr].add(rel)
    return dict(out)


def shared_fields(root: Path) -> dict[str, set[str]]:
    """Fields read by MORE THAN ONE module -- the ones whose readers must agree."""
    return {f: mods for f, mods in field_readers(root).items() if len(mods) > 1}
