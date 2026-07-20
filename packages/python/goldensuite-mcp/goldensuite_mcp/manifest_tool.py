"""`suite_manifest` -- a suite-native MCP tool that serves SLICES of the generated
agent-navigation manifest (docs/agent-manifest.json).

The manifest is a ~300 KB structured index of every package's config schema, CLI,
MCP tools, vocabularies, env knobs, and source-file map. Handing an agent the whole
file to answer one question ("which scorer suits names", "where does goldenpipe's
MCP server live") wastes context, so this tool exposes progressive-disclosure
slices: an overview, one package, one section of a package, or a keyword search
across everything -- returning only the matching leaves plus where each is defined.

The manifest is generated + CI-gated by scripts/config_matrix, so what this tool
serves is always in sync with the live code (it never re-derives anything).
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.types import Tool

TOOL_NAME = "suite_manifest"

_SECTIONS = ["overview", "config", "cli", "mcp", "vocab", "env", "source", "rust_crates"]
_PACKAGES = ["goldenmatch", "goldencheck", "goldenflow", "goldenpipe", "infermap", "goldenanalysis"]
_SECTION_KEY = {  # section arg -> manifest package key
    "config": "config_models", "cli": "cli", "mcp": "mcp_tools",
    "vocab": "vocabularies", "env": "env_vars", "source": "source",
}

TOOL = Tool(
    name=TOOL_NAME,
    description=(
        "Look up Golden Suite config / CLI / MCP tools / vocabularies / env knobs / "
        "source-file locations WITHOUT grepping the repo. Serves slices of a generated, "
        "CI-gated manifest so answers always match the live code. Usage: no args -> an "
        "overview (packages + surface counts); `package` -> that package's counts + where "
        "its schema/CLI/MCP live; `package`+`section` -> that slice (section one of "
        "config/cli/mcp/vocab/env/source); `section=rust_crates` -> the Rust crate map; "
        "`query` alone -> a keyword search across every vocabulary value, tool, config "
        "field, env var, CLI command, and crate, each with where it's defined."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "package": {"type": "string", "enum": _PACKAGES,
                        "description": "Restrict to one package."},
            "section": {"type": "string", "enum": _SECTIONS,
                        "description": "Which slice of the package to return."},
            "query": {"type": "string",
                      "description": "Case-insensitive substring. With no package/section, searches everything."},
        },
        "required": [],
    },
)


# --- manifest loading (located, not re-derived) ------------------------------

_CACHE: dict[str, Any] = {}


def _manifest_path() -> Path | None:
    # 1) explicit override; 2) the canonical file in a repo checkout (freshest
    # during active editing); 3) the copy bundled next to this module, which is
    # the only one present in a pip-installed / deployed server. (2) and (3) are
    # byte-identical -- the config-matrix gate keeps them in lockstep.
    env = os.environ.get("GOLDENSUITE_MANIFEST_PATH")
    if env:
        p = Path(env)
        return p if p.exists() else None
    for parent in Path(__file__).resolve().parents:
        cand = parent / "docs" / "agent-manifest.json"
        if cand.exists():
            return cand
    bundled = Path(__file__).resolve().parent / "agent-manifest.json"
    return bundled if bundled.exists() else None


def load_manifest() -> dict | None:
    if "m" not in _CACHE:
        p = _manifest_path()
        _CACHE["m"] = json.loads(p.read_text(encoding="utf-8")) if p else None
    return _CACHE["m"]


# --- slicing -----------------------------------------------------------------


def _overview(m: dict) -> dict:
    pkgs = {}
    for name, p in m["packages"].items():
        pkgs[name] = {
            "nav_group": p.get("nav_group"),
            "env_prefix": p.get("env_prefix"),
            "config_models": len(p.get("config_models", [])),
            "cli_commands": len(p.get("cli", [])),
            "mcp_tools": len(p.get("mcp_tools", [])),
            "vocabularies": [v["title"] for v in p.get("vocabularies", [])],
            "env_groups": len(p.get("env_vars", {})),
        }
    return {"schema": m.get("schema"), "packages": pkgs, "rust_crates": len(m.get("rust_crates", []))}


def _search(m: dict, query: str) -> dict:
    q = query.lower()
    hits: list[dict] = []
    for name, p in m["packages"].items():
        for model in p.get("config_models", []):
            for f in model["fields"]:
                if q in f["name"].lower():
                    hits.append({"kind": "config_field", "package": name,
                                 "value": f"{model['name']}.{f['name']}", "type": f.get("type")})
        for cmd in p.get("cli", []):
            if q in cmd["command"].lower():
                hits.append({"kind": "cli_command", "package": name, "value": cmd["command"]})
        for t in p.get("mcp_tools", []):
            if q in t["name"].lower() or q in t.get("description", "").lower():
                hits.append({"kind": "mcp_tool", "package": name, "value": t["name"],
                             "description": t.get("description")})
        for vocab in p.get("vocabularies", []):
            for val in vocab["values"]:
                if q in val["value"].lower() or q in val.get("meaning", "").lower():
                    hits.append({"kind": "vocab_value", "package": name,
                                 "value": val["value"], "vocab": vocab["title"],
                                 "meaning": val.get("meaning")})
        for group, envs in p.get("env_vars", {}).items():
            for e in envs:
                if q in e.lower():
                    hits.append({"kind": "env_var", "package": name, "value": e, "group": group})
    for c in m.get("rust_crates", []):
        if q in c["name"].lower() or q in (c.get("path") or "").lower():
            hits.append({"kind": "rust_crate", "value": c["name"], "path": c.get("path")})
    return {"query": query, "count": len(hits), "hits": hits}


def _package_slice(m: dict, package: str, section: str, query: str) -> dict:
    p = m["packages"].get(package)
    if p is None:
        return {"error": f"unknown package: {package}", "packages": list(m["packages"])}
    if not section or section == "overview":
        return {"package": package, "nav_group": p.get("nav_group"),
                "env_prefix": p.get("env_prefix"), "source": p.get("source"),
                "counts": _overview(m)["packages"][package]}
    data = p.get(_SECTION_KEY.get(section, section))
    if not query:
        return {"package": package, "section": section, "data": data}
    # Filter the slice by the query on its most obvious text field.
    ql = query.lower()
    if section == "config":
        data = [{**mo, "fields": [f for f in mo["fields"] if ql in json.dumps(f).lower()]}
                for mo in (data or [])]
        data = [mo for mo in data if mo["fields"]]
    elif isinstance(data, list):
        data = [d for d in data if ql in json.dumps(d).lower()]
    elif isinstance(data, dict):
        data = {k: v for k, v in data.items() if ql in json.dumps({k: v}).lower()}
    return {"package": package, "section": section, "query": query, "data": data}


def make_dispatch() -> Callable[[str, dict], dict]:
    def dispatch(name: str, args: dict) -> dict:
        m = load_manifest()
        if m is None:
            return {"error": "agent manifest not found; set GOLDENSUITE_MANIFEST_PATH or run "
                             "from a checkout where docs/agent-manifest.json exists"}
        package = str(args.get("package") or "").strip()
        section = str(args.get("section") or "").strip()
        query = str(args.get("query") or "").strip()
        if section == "rust_crates":
            crates = m.get("rust_crates", [])
            if query:
                ql = query.lower()
                crates = [c for c in crates if ql in c["name"].lower() or ql in (c.get("path") or "").lower()]
            return {"section": "rust_crates", "count": len(crates), "crates": crates}
        if package:
            return _package_slice(m, package, section, query)
        if query:
            return _search(m, query)
        return _overview(m)

    return dispatch
