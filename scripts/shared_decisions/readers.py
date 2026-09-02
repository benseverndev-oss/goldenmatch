"""Which modules touch which config fields.

A field touched by more than one module is a shared decision: those modules
must agree about what it means, and nothing checks that they do. That is
exactly the 1c843c8a5 incident -- score_buckets and blocker.py both read
`blocking_config.passes` and `.keys` and resolved their precedence differently.

"Touch" means READ **or** WRITE, deliberately -- see `field_accessors`. The
module file is still named `readers.py` because the plan's later tasks import
`shared_decisions.readers`; the accurate word for what it measures is
*accessor*, and the public function says so.
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


def _looks_like_config_name(
    name: str,
    targets: tuple[set[str], set[str], set[str]],
    aliases: frozenset[str] = frozenset(),
) -> bool:
    """Does a single name -- a base identifier, or the whole dotted chain it's
    part of -- look like it refers to a config object?

    Three ways in:
    - the literal substring rule ("config"/"cfg" in the name, e.g.
      `blocking_config`, or `config.blocking` as a whole dotted string),
    - a WORD-BOUNDARY match against a known config class name: the name,
      lowercased with underscores and dots stripped, equals the full class
      name, the class name with its trailing "config" removed, or one of the
      class name's CamelCase segments. `blocking` matches `BlockingConfig`
      via its "Blocking" segment. Or
    - the name is a recorded MODULE-LOCAL ALIAS -- a variable this module
      itself assigned from a config-looking expression, e.g.
      `b = config.blocking` records `b`. See `_module_alias_names`.

    Word-boundary equality (not a prefix match) is deliberate. An earlier
    version of this rule matched any base name that PREFIXED a class name,
    which let single-letter loop variables collide with class names that
    happen to start with that letter -- `c.trust` in cli/memory.py matched
    only because "c" prefixes "canopyconfig" (`c` there is a `Correction`
    record, from `for c in corrections:`), and `f.guard` in core/pipeline.py
    matched only because "f" prefixes "fieldtransform" (`f` there is a
    matchkey field, from `for f in mk.fields`). Word-boundary equality
    rejects both. No minimum length is used instead -- a word boundary is
    the principled cut; an arbitrary length floor is a guess.

    Callers should almost always go through `_chain_looks_like_config`, which
    applies this to a whole chain the ONE agreed way.
    """
    low = name.lower()
    if "config" in low or "cfg" in low:
        return True
    stripped = low.replace("_", "").replace(".", "")
    full, trimmed, segments = targets
    if stripped in full or stripped in trimmed or stripped in segments:
        return True
    return stripped in aliases


def _chain_looks_like_config(
    segments: list[str],
    targets: tuple[set[str], set[str], set[str]],
    aliases: frozenset[str] = frozenset(),
) -> bool:
    """THE config-look decision for a Name/Attribute chain, in ONE place.

    A chain (from `_base_chain`) looks like it holds a config object when its
    WHOLE dotted string looks like config, OR when ANY single segment does.
    Both halves earn their place: `blocking_config` matches as a whole,
    `config.blocking` matches on its `config` segment.

    This function exists because that rule used to be written twice and the two
    copies disagreed. The accessor scan tested whole-chain-OR-any-segment;
    `_module_alias_names` tested only the whole dotted string -- so
    `b = profile.blocking` failed to register `b` as a config alias, not for
    any principled reason but because "profile.blocking" carries no literal
    "config"/"cfg" substring, while its `blocking` segment does
    word-boundary-match `BlockingConfig`. Two implementations of one rule,
    silently disagreeing, inside the detector built to find exactly that, is
    the defect class this whole inventory exists to remove. One function, two
    call sites.
    """
    if _looks_like_config_name(".".join(segments), targets, aliases):
        return True
    return any(_looks_like_config_name(seg, targets, aliases) for seg in segments)


def _base_chain(node: ast.expr) -> list[str] | None:
    """Walk a Name/Attribute chain to its segments, e.g. `config.blocking` ->
    ["config", "blocking"].

    Real code writes `config.blocking.passes`, where the base of the
    `.passes` access is the Attribute node `config.blocking`, not a plain Name
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


def _module_alias_names(
    tree: ast.Module, targets: tuple[set[str], set[str], set[str]]
) -> frozenset[str]:
    """PASS 1: local variables this module assigns from a config-looking
    expression, e.g. `b = config.blocking` records "b" -- so `b.passes` /
    `b.keys` later in the module are recognized as config field accesses even
    though `b` alone doesn't word-boundary-match any known config class.
    core/config_critique.py does exactly this (`b = config.blocking`, then
    `b.strategy`, `b.passes`, `b.keys`), the same multi_pass precedence
    decision the 1c843c8a5 incident fix added to score_buckets -- without
    alias tracking that module is invisible.

    A target counts when it is a single plain name (`x = ...`, not tuple/
    attribute/subscript unpacking) and its value is a Name/Attribute chain
    that `_chain_looks_like_config` accepts -- the SAME rule the accessor scan
    applies to an access base, not a stricter lookalike of it.

    MODULE SCOPE ONLY, by design -- this is a single flat pass over every
    Assign in the module regardless of which function it's in, so it does
    NOT track control flow, reassignment, or shadowing across functions.
    `b = config.blocking` in one function and an unrelated `b.passes` in a
    different function in the SAME module would be treated as the same
    alias -- a false positive this scan accepts on purpose: it is
    report-only and human-triaged, and over-reporting within a module costs
    one triage decision, while under-reporting is invisible, which is how
    the 1c843c8a5 incident shipped. Cross-module aliasing is never tracked.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        segments = _base_chain(node.value)
        if segments is not None and _chain_looks_like_config(segments, targets):
            aliases.add(node.targets[0].id.lower())
    return frozenset(aliases)


def unparseable_modules(root: Path) -> list[str]:
    """Module paths under `root` that `field_accessors` could not parse.

    `field_accessors` silently `continue`s past an `ast.parse` SyntaxError so
    one bad file cannot crash the whole scan -- but a module that raises IS a
    module invisible to every field this scan reports, the same silence
    `modules_without_coverage_data` exists to surface in the companion
    parity_coverage.py tool. `core/autoconfig_planner.py` and
    `core/execution_plan.py` carried a UTF-8 BOM (`\\ufeff`) that survived a
    plain `encoding="utf-8"` decode and made `ast.parse` raise on both --
    fixed by decoding with `utf-8-sig` instead, but the failure mode (a file
    silently absent from every count) is not fully closed unless something
    ALSO fires when it happens again for a different reason.
    """
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            out.append(path.relative_to(root).as_posix())
    return out


def field_accessors(root: Path) -> dict[str, set[str]]:
    """Map each config field name to the modules under `root` that ACCESS it.

    ACCESS MEANS READ **OR** WRITE, on purpose. `core/pipeline.py` does
    `config.blocking.keys = new_keys` -- an `ast.Store` context -- and this
    scan counts it. For a shared-decision inventory a writer matters MORE than
    a reader, not less: a module that mutates a field shared with ten others
    is precisely the module every reader has to agree with, and the 1c843c8a5
    incident was two modules disagreeing about how that field resolves. So
    writes are not filtered out, and the name says "accessors" rather than
    "readers" so the count cannot be mistaken for a read-only one. (This
    function was called `field_readers` through three fix rounds while
    measuring reads AND writes -- a name that overstates what it measures is
    the same defect class the inventory exists to remove.)

    An access counts when its base -- a bare name (`blocking_config`), a
    dotted chain (`config.blocking`), or a module-local alias of either
    (`b`, from `b = config.blocking`; see `_module_alias_names`) -- looks
    like it holds a config object. That single decision lives in
    `_chain_looks_like_config`.

    Returns field name -> set of module paths (posix, relative to `root`).
    """
    known = _known_field_names()
    targets = _config_look_targets()
    out: dict[str, set[str]] = defaultdict(set)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        aliases = _module_alias_names(tree, targets)  # PASS 1
        for node in ast.walk(tree):  # PASS 2
            if not isinstance(node, ast.Attribute) or node.attr not in known:
                continue
            segments = _base_chain(node.value)
            if segments is None:
                continue
            if _chain_looks_like_config(segments, targets, aliases):
                out[node.attr].add(rel)
    return dict(out)


def shared_fields(root: Path) -> dict[str, set[str]]:
    """Fields ACCESSED -- read or written -- by MORE THAN ONE module.

    These are the shared decisions: every module in the set has to agree about
    what the field means, and (writes included, see `field_accessors`) about
    who is allowed to change it.
    """
    return {f: mods for f, mods in field_accessors(root).items() if len(mods) > 1}
