"""Render (and diff) the generated block of ``config-matrix.mdx`` from code.

Three generated parts, all sourced from the live code so they cannot drift:

1. **Config object reference** -- the pydantic ``GoldenMatchConfig`` tree, walked
   in field-declaration order. Every reachable config model gets a table of its
   fields with type, default, and Literal choices / nested-model links.
2. **Enumerated vocabularies** -- the ``VALID_*`` / group-strategy frozensets in
   ``config/schemas.py`` (the allowed values for the ``str`` fields above).
3. **Environment-variable index** -- every ``GOLDENMATCH_*`` name read anywhere
   in the Python or Rust source (quoted string literals with that prefix are
   env-var names and nothing else). Semantics live in ``tuning.mdx``; this index
   only guarantees completeness.

Only the text between the ``config-matrix:generated`` markers is owned by this
generator; the prose around it (intro + the combinations/outcomes matrix) is
hand-authored. ``docs_are_current()`` compares the committed marked block to a
fresh render; CI runs ``scripts/gen_config_matrix.py --check``.
"""
from __future__ import annotations

import re
import types
import typing
from pathlib import Path

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from goldenmatch.config import schemas as S

# ROOT/packages/python/goldenmatch/goldenmatch/core/config_matrix/docgen.py
ROOT = Path(__file__).resolve().parents[6]
DOC_PATH = ROOT / "docs-site" / "goldenmatch" / "config-matrix.mdx"
_PKG_DIR = Path(__file__).resolve().parents[2]  # .../goldenmatch/goldenmatch (inner package)
_RUST_DIR = ROOT / "packages" / "rust" / "extensions"

MARKER_START = "{/* config-matrix:generated:start -- DO NOT EDIT. Regenerate: python scripts/gen_config_matrix.py --write */}"
MARKER_END = "{/* config-matrix:generated:end */}"

_ROOT_MODEL = S.GoldenMatchConfig

# ---------------------------------------------------------------------------
# type rendering
# ---------------------------------------------------------------------------


def _is_model(tp: object) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _clean(text: str) -> str:
    """Make generated text safe inside an MDX table cell: escape the table pipe
    and the JSX-significant characters (`<`, `{`, `}`) so a description or type
    string can never break the Mintlify build."""
    return (
        text.replace("|", r"\|")
        .replace("<", "&lt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )


def render_type(ann: object) -> tuple[str, list[str], list[str]]:
    """Return ``(type_str, literal_choices, nested_model_names)`` for an annotation."""
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    # Optional / Union (both typing.Union and the X | Y form)
    if origin is typing.Union or isinstance(ann, types.UnionType):
        parts = [a for a in args if a is not type(None)]
        has_none = len(parts) != len(args)
        rendered, choices, nested = [], [], []
        for part in parts:
            t, c, n = render_type(part)
            rendered.append(t)
            choices += c
            nested += n
        text = " | ".join(rendered) if rendered else "None"
        if has_none:
            text += " | None"
        return text, choices, nested

    if origin is typing.Literal:
        return "Literal", [str(a) for a in args], []

    if origin in (list, set, frozenset):
        inner, c, n = render_type(args[0]) if args else ("", [], [])
        name = {list: "list", set: "set", frozenset: "frozenset"}[origin]
        return f"{name}[{inner}]", c, n

    if origin is dict:
        k = render_type(args[0])[0] if args else ""
        v, c, n = render_type(args[1]) if len(args) > 1 else ("", [], [])
        return f"dict[{k}, {v}]", c, n

    if _is_model(ann):
        return ann.__name__, [], [ann.__name__]

    if isinstance(ann, type):
        return ann.__name__, [], []

    # typing constructs we don't special-case (rare): fall back to a clean str
    return _clean(str(ann).replace("typing.", "")), [], []


def _default_repr(fi) -> str:
    if fi.default is PydanticUndefined and fi.default_factory is None:
        return "**required**"
    if fi.default_factory is not None:
        try:
            val = fi.default_factory()
        except Exception:
            return "_(factory)_"
        if isinstance(val, BaseModel):
            return f"_(default {type(val).__name__})_"
        return f"`{val!r}`"
    if isinstance(fi.default, BaseModel):
        return f"_(default {type(fi.default).__name__})_"
    return f"`{fi.default!r}`"


# ---------------------------------------------------------------------------
# model traversal
# ---------------------------------------------------------------------------


def reachable_models() -> list[type[BaseModel]]:
    """All config models reachable from GoldenMatchConfig, in BFS field order
    (root first, then each nested model the first time it is referenced)."""
    ordered: list[type[BaseModel]] = [_ROOT_MODEL]
    seen: set[type[BaseModel]] = {_ROOT_MODEL}
    queue: list[type[BaseModel]] = [_ROOT_MODEL]
    while queue:
        model = queue.pop(0)
        for fi in model.model_fields.values():
            _, _, nested = render_type(fi.annotation)
            for name in nested:
                tp = getattr(S, name, None)
                if _is_model(tp) and tp not in seen:
                    seen.add(tp)
                    ordered.append(tp)
                    queue.append(tp)
    return ordered


def render_schema_section() -> str:
    lines = ["## Config object reference", "",
             "Every config object in the pydantic `GoldenMatchConfig` tree, "
             "generated from `config/schemas.py`. Nested objects link by name.",
             ""]
    for model in reachable_models():
        lines.append(f"### `{model.__name__}`")
        doc = (model.__doc__ or "").strip().splitlines()
        if doc:
            lines.append(f"_{doc[0].strip()}_")
        lines.append("")
        lines.append("| Field | Type | Default | Choices / notes |")
        lines.append("|---|---|---|---|")
        for fname, fi in model.model_fields.items():
            type_str, choices, nested = render_type(fi.annotation)
            notes = ""
            if choices:
                notes = ", ".join(f"`{c}`" for c in choices)
            elif nested:
                notes = ", ".join(f"[`{n}`](#{n.lower()})" for n in dict.fromkeys(nested))
            if fi.description:
                desc = _clean(fi.description.strip())
                notes = f"{notes} -- {desc}" if notes else desc
            alias = f" _(alias `{fi.alias}`)_" if getattr(fi, "alias", None) else ""
            lines.append(
                f"| `{fname}`{alias} | {_clean(type_str)} | {_default_repr(fi)} | {notes} |"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# enumerated vocabularies
# ---------------------------------------------------------------------------

_VOCABS = [
    ("Scorers", "VALID_SCORERS", "`MatchkeyField.scorer` / `NegativeEvidenceField.scorer`"),
    ("Simple transforms", "VALID_SIMPLE_TRANSFORMS", "`transforms` chains"),
    ("Survivorship strategies", "VALID_STRATEGIES", "`GoldenFieldRule.strategy`"),
    ("Group survivorship strategies", "_GROUP_STRATEGIES", "`GoldenGroupRule.strategy`"),
    ("Standardizers", "VALID_STANDARDIZERS", "`StandardizationConfig.rules`"),
    ("Matchkey types", "_VALID_MK_TYPES", "`MatchkeyConfig.type`"),
]


def render_vocab_section() -> str:
    lines = ["## Enumerated vocabularies", "",
             "The allowed string values for the `str`-typed fields above, from "
             "the frozensets in `config/schemas.py`.", "",
             "| Vocabulary | Constant | Applies to | Values |",
             "|---|---|---|---|"]
    for title, const, applies in _VOCABS:
        values = getattr(S, const)
        rendered = ", ".join(f"`{v}`" for v in sorted(values))
        lines.append(f"| {title} | `{const}` | {applies} | {rendered} |")
    lines.append("")
    lines.append("Extended-grammar transforms (regex-validated, not in the set "
                 "above): `substring:<start>:<end>`, `qgram:<n>`, `bloom_filter"
                 "[:<a>:<b>:<c>]`. Custom survivorship: `custom:<snake_case>`. "
                 "Plugin scorers/transforms resolve via the plugin registry.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# environment-variable index
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"""["'](GOLDENMATCH_[A-Z0-9_]+)["']""")
_ENV_PREFIX_ORDER = [
    "NATIVE", "FRAME", "BUCKET", "COLUMNAR", "FS", "AUTOCONFIG", "PLANNER",
    "PLANNING", "BLOCKING", "CLUSTER", "DISTRIBUTED", "WCC", "GOLDEN", "NE",
    "SUGGEST", "HEAL", "THROUGHPUT", "RECALL", "SIMILARITY", "LLM", "GPU",
    "EMBEDDING", "INHOUSE", "MCP", "API", "AGENT", "WEB", "ALLOWED",
    "DATABASE", "IDENTITY", "SYNC", "VECTOR", "ANALYTICS", "SAIL", "SNOWFLAKE",
    "PREP", "PREPARED", "MATCH", "GOLDEN_FUSED", "BENCH", "CONFIG",
]


def scan_env_vars() -> dict[str, list[str]]:
    """Every ``GOLDENMATCH_*`` name that appears as a quoted string literal in
    the non-test Python and Rust source, grouped by first token. That prefix is
    only ever an env-var name, so quoted literals are a complete, precise scrape."""
    names: set[str] = set()
    roots = [(_PKG_DIR, "*.py"), (_RUST_DIR, "*.rs")]
    for root, glob in roots:
        if not root.exists():
            continue
        for path in root.rglob(glob):
            parts = path.parts
            if "tests" in parts or path.name.startswith("test_") or path.name.endswith("_test.py"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            names.update(_ENV_RE.findall(text))

    groups: dict[str, list[str]] = {}
    for name in names:
        body = name[len("GOLDENMATCH_"):]
        token = next((p for p in _ENV_PREFIX_ORDER if body.startswith(p)), None)
        key = token or "OTHER"
        groups.setdefault(key, []).append(name)
    return {k: sorted(v) for k, v in sorted(groups.items())}


def render_env_section() -> str:
    groups = scan_env_vars()
    total = sum(len(v) for v in groups.values())
    lines = ["## Environment variables (index)", "",
             f"{total} `GOLDENMATCH_*` runtime knobs are read by the engine "
             "(Python + native kernel). This index is scanned from source so it "
             "is complete; **defaults, valid values, and effects live in "
             "[Tuning & opt-ins](/goldenmatch/tuning)**. Grouped by area:", ""]
    for group, envs in groups.items():
        rendered = ", ".join(f"`{e}`" for e in envs)
        lines.append(f"- **{group}** ({len(envs)}): {rendered}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# assembly + splice + diff
# ---------------------------------------------------------------------------


def render_generated_block() -> str:
    body = "\n".join([
        render_schema_section().rstrip(),
        "",
        render_vocab_section().rstrip(),
        "",
        render_env_section().rstrip(),
    ])
    return f"{MARKER_START}\n\n{body}\n\n{MARKER_END}"


def _splice(existing: str, block: str) -> str:
    """Replace the region between the markers (inclusive) with ``block``."""
    start = existing.find(MARKER_START)
    end = existing.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(
            f"{DOC_PATH} is missing the config-matrix generated markers; "
            "the page must contain both MARKER_START and MARKER_END."
        )
    return existing[:start] + block + existing[end + len(MARKER_END):]


def _rendered_full() -> str:
    existing = DOC_PATH.read_text(encoding="utf-8")
    return _splice(existing, render_generated_block())


def docs_are_current() -> bool:
    if not DOC_PATH.exists():
        return False
    return DOC_PATH.read_text(encoding="utf-8") == _rendered_full()


def write_docs() -> Path:
    DOC_PATH.write_text(_rendered_full(), encoding="utf-8", newline="\n")
    return DOC_PATH
