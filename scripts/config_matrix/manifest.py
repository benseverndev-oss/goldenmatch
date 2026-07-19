"""Agent-navigation manifest: a structured-JSON view of the SAME source of truth
the config-matrix docs render from.

Motivation: coding agents shouldn't have to grep the tree to answer "what
scorers exist / which MCP tools does goldenpipe expose / what's the type and
default of GoldenMatchConfig.threshold / which env knobs tune goldenmatch". The
config-matrix registry (registry.py) + resolvers (render.py) already extract all
of that from live code, and the docs are CI-gated against drift. This module
reuses those exact resolvers to emit one machine-readable JSON instead of MDX
tables, so the manifest is derived from -- and gated against -- the same code.
It is NOT a second source of truth: `manifest_is_current()` folds into the same
`--check` gate, so the manifest can never silently diverge from the code (or the
docs).

Determinism: every collection is built in a stable order (registry definition
order, deterministic model BFS, sorted vocab values / tool names / CLI commands,
source-sorted env scan) so the committed JSON is byte-stable across a Windows dev
box and the Linux CI runner -- the same discipline the MDX gate needs.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import tomllib
import typing
from pathlib import Path

from pydantic_core import PydanticUndefined

from .registry import REGISTRY, PackageSpec
from .render import (
    ROOT,
    _import,
    _meaning,
    _neutral_repr,
    _reachable_models,
    _resolve_gloss,
    _resolve_vocab,
    _warmup,
    render_type,
    scan_env_vars,
    tool_field,
)

SCHEMA_ID = "goldenmatch.agent-manifest/v1"
MANIFEST_PATH = "docs/agent-manifest.json"
# Byte-identical copy bundled into goldensuite-mcp so the deployed (pip-installed)
# MCP server's `suite_manifest` tool works without the repo's docs/ tree. Gated
# equal to MANIFEST_PATH, so the two can never diverge.
BUNDLED_PATH = "packages/python/goldensuite-mcp/goldensuite_mcp/agent-manifest.json"
_MANIFEST_PATHS = (MANIFEST_PATH, BUNDLED_PATH)
_REGEN = "python scripts/gen_config_matrix.py --manifest"


# --- field / kwarg extraction (shared shape for schema + constructor) --------


def _default(default: object, factory=None) -> tuple[bool, str | None]:
    """(required, default_repr). Mirrors render._default_repr but structured, and
    uses the platform-neutral repr so the JSON doesn't flap by OS."""
    if default is PydanticUndefined and factory is None:
        return True, None
    if factory is not None:
        try:
            default = factory()
        except Exception:
            return False, "(factory)"
    if default is inspect.Parameter.empty:
        return True, None
    return False, _neutral_repr(default)


def _field(name: str, ann: object, description, alias, required: bool, default: str | None) -> dict:
    type_str, choices, nested = render_type(ann)
    out: dict = {"name": name, "type": type_str, "required": required}
    if default is not None:
        out["default"] = default
    if choices:
        out["choices"] = choices
    if nested:
        out["nested"] = list(dict.fromkeys(n.__name__ for n in nested))
    if description:
        out["description"] = description.strip()
    if alias:
        out["alias"] = alias
    return out


def _config_models(roots: list[str]) -> list[dict]:
    models, seen = [], set()
    for target in roots:
        for model in _reachable_models(_import(target)):
            if model in seen:
                continue
            seen.add(model)
            fields = []
            for fname, fi in model.model_fields.items():
                required, default = _default(fi.default, fi.default_factory)
                fields.append(_field(fname, fi.annotation, fi.description,
                                     getattr(fi, "alias", None), required, default))
            entry: dict = {"name": model.__name__, "fields": fields}
            doc = (model.__doc__ or "").strip().splitlines()
            if doc:
                entry["doc"] = doc[0].strip()
            models.append(entry)
    return models


def _constructors(targets: list[str]) -> list[dict]:
    out = []
    for target in targets:
        obj = _import(target)
        func = obj.__init__ if isinstance(obj, type) else obj
        fields = []
        for pname, p in inspect.signature(func).parameters.items():
            if pname in ("self", "args", "kwargs") or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            required = p.default is inspect.Parameter.empty
            default = None if required else _neutral_repr(p.default)
            fields.append(_field(pname, p.annotation, None, None, required, default))
        out.append({"name": obj.__name__, "fields": fields})
    return out


# --- CLI (Typer -> click), structured mirror of render.render_cli_section ----


def _cli(cli_module: str) -> list[dict]:
    from typer.main import get_command

    group = get_command(importlib.import_module(cli_module).app)
    commands: list[dict] = []

    def _emit(prefix: str, cmd) -> None:
        sub = getattr(cmd, "commands", None)
        if sub:
            for name in sorted(sub):
                _emit(f"{prefix}{name} ", sub[name])
            return
        options = []
        for p in cmd.params:
            ptype = getattr(p, "type", None)
            opt: dict = {
                "name": _opt_name(p),
                "type": getattr(ptype, "name", "text"),
                "required": bool(getattr(p, "required", False)),
            }
            if not opt["required"]:
                opt["default"] = _neutral_repr(p.default)
            choices = getattr(ptype, "choices", None)
            if choices:
                opt["choices"] = list(choices)
            if getattr(p, "help", None):
                opt["help"] = p.help
            options.append(opt)
        commands.append({"command": prefix.strip(), "options": options})

    for name in sorted(group.commands):
        _emit(f"{name} ", group.commands[name])
    return commands


def _opt_name(p) -> str:
    longs = [o for o in getattr(p, "opts", []) if o.startswith("--")]
    if longs:
        return longs[0]
    return (getattr(p, "opts", None) or [getattr(p, "name", "?")])[0]


# --- MCP tools ---------------------------------------------------------------


def _mcp_tools(mcp_module: str) -> list[dict]:
    try:
        mod = importlib.import_module(mcp_module)
    except ModuleNotFoundError:
        return []
    tools = getattr(mod, "TOOLS", None) or []
    out = []
    for t in sorted(tools, key=lambda t: tool_field(t, "name")):
        desc = tool_field(t, "description").strip().splitlines()
        out.append({"name": tool_field(t, "name"), "description": desc[0] if desc else ""})
    return out


# --- vocabularies (values + meaning + any decision columns) ------------------


def _vocabularies(vocabs) -> list[dict]:
    out = []
    for entry in vocabs:
        title, target, applies = entry[0], entry[1], entry[2]
        gloss = entry[3] if len(entry) > 3 else None
        glosses = _resolve_gloss(target, gloss)
        values = []
        for v in sorted(set(_resolve_vocab(target))):
            g = glosses.get(v, "")
            item: dict = {"value": v}
            meaning = _meaning(g)
            if meaning:
                item["meaning"] = meaning
            if isinstance(g, dict):
                for k in sorted(g):
                    if k != "meaning":
                        item[k] = g[k]
            values.append(item)
        out.append({"title": title, "target": target, "applies": applies, "values": values})
    return out


# --- structural map (the "where does it live" half of don't-grep) -----------
# Derived from authoritative sources only: the Python import system resolves a
# module to its actual file (so it can't name a path that doesn't exist), the
# package's pyproject declares its entry points, and each crate's Cargo.toml
# names itself. Nothing here is hand-maintained.


def _rel(path: str | None) -> str | None:
    """Absolute filesystem path -> repo-relative POSIX path (deterministic across
    a Windows dev box and the Linux CI runner)."""
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _module_file(target: str) -> str | None:
    """'pkg.mod:attr' or 'pkg.mod' -> the module's source file, repo-relative."""
    mod = target.partition(":")[0]
    try:
        spec = importlib.util.find_spec(mod)
    except (ImportError, ValueError):
        return None
    return _rel(spec.origin) if spec else None


def _entry_points(pyproject: Path) -> dict:
    if not pyproject.exists():
        return {}
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    eps = data.get("project", {}).get("entry-points", {})
    return {group: dict(sorted(entries.items())) for group, entries in sorted(eps.items())}


def _source(spec: PackageSpec) -> dict:
    import_name = spec.src_dirs[0].rsplit("/", 1)[-1]
    modules: dict[str, str] = {}
    if spec.schema_roots:
        f = _module_file(spec.schema_roots[0])
        if f:
            modules["config_schema"] = f
    if spec.constructors:
        f = _module_file(spec.constructors[0])
        if f:
            modules["constructor"] = f
    if getattr(spec, "cli_module", None):
        f = _module_file(spec.cli_module)
        if f:
            modules["cli"] = f
    if getattr(spec, "mcp_module", None):
        f = _module_file(spec.mcp_module)
        if f:
            modules["mcp_server"] = f
    out: dict = {"import_name": import_name, "root": spec.src_dirs[0]}
    if modules:
        out["modules"] = modules
    eps = _entry_points(ROOT / spec.src_dirs[0].rsplit("/", 1)[0] / "pyproject.toml")
    if eps:
        out["entry_points"] = eps
    return out


def _rust_crates() -> list[dict]:
    """Every Rust crate under packages/rust/extensions, name + path, read from the
    Cargo.toml `[package].name` -- the authoritative crate registry."""
    ext = ROOT / "packages" / "rust" / "extensions"
    crates: list[dict] = []
    for cargo in ext.rglob("Cargo.toml"):
        if "target" in cargo.parts:
            continue
        try:
            data = tomllib.loads(cargo.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = data.get("package", {}).get("name")
        if name:
            crates.append({"name": name, "path": _rel(str(cargo.parent))})
    return sorted(crates, key=lambda c: c["name"])


# --- assembly ----------------------------------------------------------------


def build_package(spec: PackageSpec) -> dict:
    _warmup(getattr(spec, "vocab_warmup", []))
    pkg: dict = {"nav_group": spec.nav_group, "env_prefix": spec.env_prefix, "doc_path": spec.doc_path}
    pkg["source"] = _source(spec)
    if spec.schema_roots:
        pkg["config_models"] = _config_models(spec.schema_roots)
    if spec.constructors:
        pkg["constructors"] = _constructors(spec.constructors)
    if getattr(spec, "cli_module", None):
        pkg["cli"] = _cli(spec.cli_module)
    if getattr(spec, "mcp_module", None):
        tools = _mcp_tools(spec.mcp_module)
        if tools:
            pkg["mcp_tools"] = tools
    if spec.vocabs:
        pkg["vocabularies"] = _vocabularies(spec.vocabs)
    pkg["env_vars"] = scan_env_vars(spec.env_prefix, spec.src_dirs)
    return pkg


def build_manifest() -> dict:
    return {
        "schema": SCHEMA_ID,
        "note": (
            "Generated from scripts/config_matrix (same registry + resolvers as the "
            f"CI-gated config-matrix docs). DO NOT EDIT. Regenerate: {_REGEN}"
        ),
        "packages": {name: build_package(spec) for name, spec in REGISTRY.items()},
        "rust_crates": _rust_crates(),
    }


def manifest_json() -> str:
    """Canonical serialization -- the exact bytes the gate compares."""
    return json.dumps(build_manifest(), indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_manifest() -> list[Path]:
    """Write the canonical manifest AND its bundled copy (identical bytes)."""
    body = manifest_json()
    written = []
    for rel in _MANIFEST_PATHS:
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
        written.append(p)
    return written


def manifest_is_current() -> bool:
    body = manifest_json()
    return all((ROOT / rel).exists() and (ROOT / rel).read_text(encoding="utf-8") == body
               for rel in _MANIFEST_PATHS)
