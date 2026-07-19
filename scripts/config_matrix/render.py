"""Shared config-matrix renderer for every suite package.

One renderer, driven by a per-package `PackageSpec` (see registry.py). It composes
a generated doc block from up to four section types, whichever a package declares:

- **schema**: walk a root pydantic model tree (fields, types, Literal choices,
  defaults) -- goldenmatch/goldencheck/goldenflow/goldenpipe/goldenanalysis.
- **constructor**: render a class/function's keyword surface (name, type, default)
  for packages whose config is call kwargs, not a model -- infermap's `MapEngine`.
- **vocab**: resolve `module:attr` to a sorted value list, whatever its shape --
  frozenset/set/list, Enum, Literal alias, dict (keys), or a zero-arg callable
  (e.g. goldenflow's `list_transforms()`).
- **env**: scan the package's Python + Rust source for its `<PREFIX>_*` env knobs.

Only the text between the markers is generated; the prose around it is hand-authored.
`docs_are_current(spec)` diffs the committed block against a fresh render; CI runs
`scripts/gen_config_matrix.py --check <pkg>`.
"""
from __future__ import annotations

import enum
import importlib
import inspect
import pkgutil
import re
import types
import typing
from pathlib import Path, PurePath

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

ROOT = Path(__file__).resolve().parents[2]

MARKER_START = "{/* config-matrix:generated:start -- DO NOT EDIT. Regenerate: python scripts/gen_config_matrix.py --write */}"
MARKER_END = "{/* config-matrix:generated:end */}"


def _import(target: str):
    """'pkg.mod:attr' -> the attribute."""
    mod, _, attr = target.partition(":")
    obj = importlib.import_module(mod)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _code(text: str) -> str:
    """A value shown inside backticks: MDX treats inline-code content as literal,
    so `<`/`{` are safe; only the table pipe needs escaping."""
    return text.replace("|", r"\|")


def _clean(text: str) -> str:
    """MDX-safe table cell: escape the pipe and the JSX-significant chars."""
    return (
        text.replace("|", r"\|").replace("<", "&lt;").replace("{", "&#123;").replace("}", "&#125;")
    )


# --- type rendering (shared by schema + constructor sections) ---------------


def render_type(ann: object) -> tuple[str, list[str], list[type]]:
    """Return (type_str, literal_choices, nested_pydantic_types) for an annotation."""
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

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

    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return ann.__name__, [], [ann]

    if ann is inspect.Parameter.empty:
        return "any", [], []
    if isinstance(ann, type):
        return ann.__name__, [], []
    return _clean(str(ann).replace("typing.", "")), [], []


def _neutral_repr(val: object) -> str:
    """Platform-neutral repr for a default value. A `pathlib.Path` reprs as
    `WindowsPath(...)` vs `PosixPath(...)` by OS, which would make the generated
    doc differ between a Windows dev box and the Linux CI runner (a flapping gate)."""
    if isinstance(val, PurePath):
        return f"Path({str(val).replace(chr(92), '/')!r})"
    return repr(val)


def _default_repr(default: object, factory=None) -> str:
    if default is PydanticUndefined and factory is None:
        return "**required**"
    if factory is not None:
        try:
            val = factory()
        except Exception:
            return "_(factory)_"
        default = val
    if isinstance(default, BaseModel):
        return f"_(default {type(default).__name__})_"
    if default is inspect.Parameter.empty:
        return "**required**"
    return f"`{_neutral_repr(default)}`"


def _model_row_lines(fname: str, type_str: str, choices, nested, description, alias) -> str:
    notes = ""
    if choices:
        notes = ", ".join(f"`{c}`" for c in choices)
    elif nested:
        notes = ", ".join(f"[`{n.__name__}`](#{n.__name__.lower()})" for n in dict.fromkeys(nested))
    if description:
        desc = _clean(description.strip())
        notes = f"{notes} -- {desc}" if notes else desc
    a = f" _(alias `{alias}`)_" if alias else ""
    return f"| `{fname}`{a} | {_clean(type_str)} | {{default}} | {notes} |"


# --- schema section (pydantic tree) -----------------------------------------


def _reachable_models(root: type[BaseModel]) -> list[type[BaseModel]]:
    ordered, seen, queue = [root], {root}, [root]
    while queue:
        model = queue.pop(0)
        for fi in model.model_fields.values():
            for tp in render_type(fi.annotation)[2]:
                if tp not in seen:
                    seen.add(tp)
                    ordered.append(tp)
                    queue.append(tp)
    return ordered


def render_schema_section(roots: list[str]) -> str:
    lines = ["## Config object reference", "",
             "Every config object in the pydantic tree(s), generated from the "
             "package schema. Nested objects link by name.", ""]
    seen: set[type] = set()
    for target in roots:
        for model in _reachable_models(_import(target)):
            if model in seen:
                continue
            seen.add(model)
            lines.append(f"### `{model.__name__}`")
            doc = (model.__doc__ or "").strip().splitlines()
            if doc:
                lines.append(f"_{doc[0].strip()}_")
            lines.append("")
            lines.append("| Field | Type | Default | Choices / notes |")
            lines.append("|---|---|---|---|")
            for fname, fi in model.model_fields.items():
                type_str, choices, nested = render_type(fi.annotation)
                row = _model_row_lines(fname, type_str, choices, nested, fi.description,
                                       getattr(fi, "alias", None))
                lines.append(row.replace("{default}", _default_repr(fi.default, fi.default_factory)))
            lines.append("")
    return "\n".join(lines)


# --- constructor section (kwargs surface) -----------------------------------


def render_constructor_section(targets: list[str]) -> str:
    lines = ["## Runtime options", "",
             "Config passed as call keyword arguments (this package has no single "
             "config model). Generated from the signature.", ""]
    for target in targets:
        obj = _import(target)
        func = obj.__init__ if isinstance(obj, type) else obj
        label = obj.__name__ + ("(...)" if not isinstance(obj, type) else "")
        lines.append(f"### `{label}`")
        lines.append("")
        lines.append("| Argument | Type | Default | Choices / notes |")
        lines.append("|---|---|---|---|")
        for pname, p in inspect.signature(func).parameters.items():
            if pname in ("self", "args", "kwargs") or p.kind in (
                p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            type_str, choices, nested = render_type(p.annotation)
            row = _model_row_lines(pname, type_str, choices, nested, None, None)
            lines.append(row.replace("{default}", _default_repr(p.default)))
        lines.append("")
    return "\n".join(lines)


# --- CLI section (Typer -> click introspection) -----------------------------


def _opt_display(p) -> str:
    longs = [o for o in getattr(p, "opts", []) if o.startswith("--")]
    if longs:
        return longs[0]
    return (getattr(p, "opts", None) or [getattr(p, "name", "?")])[0]


def render_cli_section(cli_module: str) -> str:
    from typer.main import get_command

    group = get_command(importlib.import_module(cli_module).app)
    lines = ["## CLI", "",
             "Every command and its options/arguments, generated from the Typer app. "
             "`choice`-typed options list their allowed values.", "",
             "| Command | Option | Type | Default | Notes |", "|---|---|---|---|---|"]
    # A command may be a leaf or a sub-group (one level of nesting is enough here).
    def _emit(prefix: str, cmd) -> None:
        sub = getattr(cmd, "commands", None)
        if sub:
            for name in sorted(sub):
                _emit(f"{prefix}{name} ", sub[name])
            return
        for p in cmd.params:
            ptype = getattr(p, "type", None)
            tname = getattr(ptype, "name", "text")
            notes = ""
            choices = getattr(ptype, "choices", None)
            if choices:
                notes = ", ".join(f"`{c}`" for c in choices)
            if getattr(p, "help", None):
                notes = f"{notes} -- {_clean(p.help)}" if notes else _clean(p.help)
            required = getattr(p, "required", False)
            default = "**required**" if required else f"`{_neutral_repr(p.default)}`"
            lines.append(
                f"| `{prefix.strip()}` | `{_clean(_opt_display(p))}` | {tname} | {default} | {notes} |"
            )
    for name in sorted(group.commands):
        _emit(f"{name} ", group.commands[name])
    lines.append("")
    return "\n".join(lines)


# --- MCP section (tool surface) ---------------------------------------------


def tool_field(t, key: str) -> str:
    """A tool's name/description, whether TOOLS holds mcp Tool objects (attrs)
    or plain dicts (goldenflow) -- so coverage + rendering agree either way."""
    val = t.get(key, "") if isinstance(t, dict) else getattr(t, key, "")
    return val or ""


def render_mcp_section(mcp_module: str) -> str:
    try:
        mod = importlib.import_module(mcp_module)
    except ModuleNotFoundError:
        return ""  # [mcp] extra not installed / no server module
    tools = getattr(mod, "TOOLS", None)
    if not tools:
        return ""
    lines = ["## MCP tools", "",
             f"{len(tools)} MCP tool(s) exposed by `{mcp_module}` -- the programmatic / "
             "agent surface. Config-bearing tools take the same knobs as above.", "",
             "| Tool | Description |", "|---|---|"]
    for t in sorted(tools, key=lambda t: tool_field(t, "name")):
        desc = tool_field(t, "description").strip().splitlines()
        lines.append(f"| `{tool_field(t, 'name') or '?'}` | {_clean(desc[0] if desc else '')} |")
    lines.append("")
    return "\n".join(lines)


# --- vocab section (flexible resolver) --------------------------------------


def _literal_field(target: str) -> list[str] | None:
    """If `target` is `module:Model.field` naming a pydantic field whose type is a
    Literal, return its choices; else None. Lets a schema Literal (e.g.
    BlockingConfig.strategy) be a first-class vocab."""
    mod, _, attr = target.partition(":")
    if "." not in attr:
        return None
    model_name, field = attr.split(".", 1)
    model = getattr(importlib.import_module(mod), model_name, None)
    if not (isinstance(model, type) and hasattr(model, "model_fields")) or field not in model.model_fields:
        return None
    ann = model.model_fields[field].annotation
    for a in [ann, *typing.get_args(ann)]:
        if typing.get_origin(a) is typing.Literal:
            return [str(x) for x in typing.get_args(a)]
    return []


def _resolve_vocab(target: str) -> list[str]:
    lit = _literal_field(target)
    if lit is not None:
        return lit
    obj = _import(target)
    if typing.get_origin(obj) is typing.Literal:
        return [str(a) for a in typing.get_args(obj)]
    if typing.get_origin(obj) in (typing.Union, types.UnionType) or isinstance(obj, types.UnionType):
        out: list[str] = []
        for a in typing.get_args(obj):
            if typing.get_origin(a) is typing.Literal:
                out += [str(x) for x in typing.get_args(a)]
        return out
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return [m.value if isinstance(m.value, str) else m.name for m in obj]
    if inspect.isroutine(obj):
        obj = obj()
    if isinstance(obj, dict):
        return [str(k) for k in obj.keys()]
    if isinstance(obj, (set, frozenset, list, tuple)):
        return [_item_name(v) for v in obj]
    return [_item_name(obj)]


def _item_name(v: object) -> str:
    """A vocab value's display token: the string itself, else its `.name`
    attribute (registry records like goldenflow's `TransformInfo`), else repr.
    Never str() an object whose repr embeds a memory address -- that flaps the gate."""
    if isinstance(v, str):
        return v
    name = getattr(v, "name", None)
    if isinstance(name, str):
        return name
    return str(v)


def _resolve_gloss(target: str, gloss) -> dict[str, str]:
    """Return {value: one-line meaning}. `gloss` is None (no glosses), a curated
    {value: text} dict, the string "doc" (derive each value's meaning from its
    implementing object's docstring, e.g. goldenflow transform funcs), or a
    ("doc", {overrides}) tuple (derive, then overlay curated fills for gaps)."""
    curated: dict[str, str] = {}
    derive = False
    if isinstance(gloss, dict):
        curated = gloss
    elif gloss == "doc":
        derive = True
    elif isinstance(gloss, tuple) and gloss and gloss[0] == "doc":
        derive = True
        curated = gloss[1] if len(gloss) > 1 else {}

    derived: dict[str, str] = {}
    if derive:
        obj = _import(target)
        if inspect.isroutine(obj):
            obj = obj()
        for it in obj if isinstance(obj, (list, tuple, set, frozenset)) else []:
            doc = getattr(getattr(it, "func", it), "__doc__", None) or getattr(it, "description", None)
            if doc and doc.strip():
                derived[_item_name(it)] = doc.strip().splitlines()[0].strip()
    return {**derived, **curated}


def _meaning(g) -> str:
    return g if isinstance(g, str) else (g.get("meaning", "") if isinstance(g, dict) else "")


def _render_vocab_section(vocabs) -> str:
    lines = ["## Enumerated vocabularies", "",
             "Allowed values for the `str`-typed / registry-backed fields above.", ""]
    for entry in vocabs:
        title, target, applies = entry[0], entry[1], entry[2]
        gloss = entry[3] if len(entry) > 3 else None
        values = sorted(set(_resolve_vocab(target)))
        glosses = _resolve_gloss(target, gloss)
        # Extra columns come from any per-value dict (e.g. scorers carry range /
        # best_for); deterministic order.
        extra: list[str] = []
        for g in glosses.values():
            if isinstance(g, dict):
                extra += [k for k in g if k != "meaning" and k not in extra]
        extra.sort()
        lines.append(f"### {title}")
        lines.append(f"_`{target.split(':')[-1]}` -- {applies}._")
        lines.append("")
        if extra or any(_meaning(glosses.get(v)) for v in values):
            header = ["Value", "Meaning"] + [c.replace("_", " ").capitalize() for c in extra]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "---|" * len(header))
            for v in values:
                g = glosses.get(v, "")
                cells = [f"`{_code(v)}`", _clean(_meaning(g))]
                cells += [_clean(g.get(c, "") if isinstance(g, dict) else "") for c in extra]
                lines.append("| " + " | ".join(cells) + " |")
        else:
            lines.append(", ".join(f"`{_code(v)}`" for v in values))
        lines.append("")
    return "\n".join(lines)


# --- env section ------------------------------------------------------------

_ENV_RE_TMPL = r'''["'](%s_[A-Z0-9_]+)["']'''


def scan_env_vars(prefix: str, src_dirs: list[str]) -> dict[str, list[str]]:
    rx = re.compile(_ENV_RE_TMPL % re.escape(prefix.rstrip("_")))
    names: set[str] = set()
    for rel in src_dirs:
        root = ROOT / rel
        if not root.exists():
            continue
        for path in list(root.rglob("*.py")) + list(root.rglob("*.rs")):
            parts = path.parts
            if "tests" in parts or path.name.startswith("test_") or path.name.endswith("_test.py"):
                continue
            try:
                names.update(rx.findall(path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    groups: dict[str, list[str]] = {}
    plen = len(prefix.rstrip("_")) + 1
    for name in names:
        token = name[plen:].split("_", 1)[0] or "OTHER"
        groups.setdefault(token, []).append(name)
    return {k: sorted(v) for k, v in sorted(groups.items())}


def _render_env_section(prefix: str, src_dirs: list[str], tuning_link: str | None) -> str:
    groups = scan_env_vars(prefix, src_dirs)
    total = sum(len(v) for v in groups.values())
    detail = f" Details in [Tuning & opt-ins]({tuning_link})." if tuning_link else ""
    lines = ["## Environment variables (index)", "",
             f"{total} `{prefix}*` runtime knob(s) read by the package, scanned "
             f"from source so this is complete.{detail} Grouped by area:", ""]
    if not total:
        lines.append("_(none)_")
    for group, envs in groups.items():
        lines.append(f"- **{group}** ({len(envs)}): " + ", ".join(f"`{e}`" for e in envs))
    lines.append("")
    return "\n".join(lines)


# --- assembly + splice + diff -----------------------------------------------


def _warmup(packages: list[str]) -> None:
    """Deterministically import every submodule of the given packages so lazy
    decorator registries (e.g. goldenflow's `@register_transform`) are fully
    populated regardless of what else happened to be imported first. Without this
    a registry-backed vocab would vary by import order and flap the gate."""
    for pkgname in packages:
        try:
            pkg = importlib.import_module(pkgname)
        except Exception:
            continue
        for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            try:
                importlib.import_module(m.name)
            except Exception:
                continue


def render_generated_block(spec) -> str:
    _warmup(getattr(spec, "vocab_warmup", []))
    parts: list[str] = []
    if spec.schema_roots:
        parts.append(render_schema_section(spec.schema_roots).rstrip())
    if spec.constructors:
        parts.append(render_constructor_section(spec.constructors).rstrip())
    if getattr(spec, "cli_module", None):
        parts.append(render_cli_section(spec.cli_module).rstrip())
    if getattr(spec, "mcp_module", None):
        mcp = render_mcp_section(spec.mcp_module).rstrip()
        if mcp:
            parts.append(mcp)
    if spec.vocabs:
        parts.append(_render_vocab_section(spec.vocabs).rstrip())
    parts.append(_render_env_section(spec.env_prefix, spec.src_dirs, spec.tuning_link).rstrip())
    body = "\n\n".join(parts)
    return f"{MARKER_START}\n\n{body}\n\n{MARKER_END}"


def _doc_path(spec) -> Path:
    return ROOT / spec.doc_path


def _splice(existing: str, block: str) -> str:
    start, end = existing.find(MARKER_START), existing.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("config-matrix markers missing or malformed")
    return existing[:start] + block + existing[end + len(MARKER_END):]


def _rendered_full(spec) -> str:
    return _splice(_doc_path(spec).read_text(encoding="utf-8"), render_generated_block(spec))


def docs_are_current(spec) -> bool:
    p = _doc_path(spec)
    return p.exists() and p.read_text(encoding="utf-8") == _rendered_full(spec)


def write_docs(spec) -> Path:
    p = _doc_path(spec)
    p.write_text(_rendered_full(spec), encoding="utf-8", newline="\n")
    return p
