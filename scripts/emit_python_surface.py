#!/usr/bin/env python3
"""Emit a package's real Python operation surface as JSON: {package, mcp_tools, cli_commands}.
Runtime introspection of the actual registries. Needs the surface-bearing extras installed
(<pkg>[mcp]); a missing extra exits 3 (environment gap), distinct from a code breakage (2).

The surface is UNIFORM across the suite: MCP tools = `<pkg>.mcp.server.TOOLS` (each a Tool with
.name); CLI = the Typer app resolved via typer.main.get_command(app).commands.keys() — which
gives the real names a user types (hyphenation like `mcp-serve`, and sub-app group names),
unlike registered_commands whose .name can be None. Only the CLI module path differs per package.
"""
from __future__ import annotations
import importlib
import json
import sys


def _tool_name(t) -> str:
    # Tool registries vary: mcp-SDK Tool objects expose `.name`; packages with a custom
    # JSON-RPC MCP server (e.g. goldenflow) list plain dicts with a "name" key.
    return t.name if hasattr(t, "name") else t["name"]


def _mcp(package: str):
    def fn() -> list[str]:
        mod = importlib.import_module(f"{package}.mcp.server")  # needs <pkg>[mcp]
        return [_tool_name(t) for t in mod.TOOLS]
    return fn


def _a2a(package: str):
    def fn() -> list[str]:
        import importlib
        mod = importlib.import_module(f"{package}.a2a.server")
        skills = getattr(mod, "_SKILLS", None)
        if skills is None:
            skills = mod.AGENT_CARD["skills"]
        return [s["id"] for s in skills]
    return fn


_A2A_PACKAGES = ("goldenmatch", "goldencheck", "goldenflow", "goldenpipe")


def _cli(cli_module: str):
    def fn() -> list[str]:
        from typer.main import get_command
        app = importlib.import_module(cli_module).app
        names = list(get_command(app).commands.keys())
        if len(names) != len(set(names)):
            raise SystemExit(f"CLI leaf/group name collision in {cli_module} — surface is ambiguous")
        return names
    return fn


def _scorers() -> list[str]:
    # goldenmatch config surface: the accepted comparison-scorer names. Mirrors
    # TS `VALID_SCORERS` (types.ts); intended cross-language deltas are declared
    # in parity/goldenmatch.yaml (e.g. audio_fp/phash/radial are Python-only).
    from goldenmatch.config.schemas import VALID_SCORERS
    return sorted(VALID_SCORERS)


def _transforms() -> list[str]:
    from goldenmatch.config.schemas import VALID_SIMPLE_TRANSFORMS
    return sorted(VALID_SIMPLE_TRANSFORMS)


def _blocking_strategies() -> list[str]:
    # goldenmatch config surface: the accepted blocking-strategy names. Mirrors TS
    # `VALID_BLOCKING_STRATEGIES` (config-edits.ts); intended cross-language deltas
    # are declared in parity/goldenmatch.yaml (lsh/perceptual/simhash are Python-only).
    from typing import get_args

    from goldenmatch.config.schemas import BlockingConfig
    return sorted(get_args(BlockingConfig.model_fields["strategy"].annotation))


def _scorer_kernels() -> list[str]:
    # The scorers with a Rust/arrow-native kernel (the reference fast path) --
    # every OTHER scorer in VALID_SCORERS is a pure-Python fallback. Mirrors TS
    # `WASM_COVERED_SCORERS`; the Python/TS delta (e.g. `date` is native on Python,
    # not yet in the TS WASM kernel) is declared in parity/goldenmatch.yaml.
    from goldenmatch.backends.score_buckets import _NATIVE_SCORER_IDS
    return sorted(_NATIVE_SCORER_IDS)


def _infermap_scorers() -> list[str]:
    # infermap's built-in scorer identities (each scorer class's `.name`). Mirrors
    # TS `SCORER_NAMES` (core/scorers/registry.ts); the api_parity `scorers` surface.
    from infermap.scorers import SCORER_NAMES
    return sorted(SCORER_NAMES)


def _infermap_scorer_kernels() -> list[str]:
    # infermap scorers backed by an `infermap-core` Rust kernel (native + wasm).
    # STATIC (does not require the wheel to be built). Mirrors TS `SCORER_KERNELS`;
    # every scorer NOT here is classified in parity/infermap.yaml
    # scorer_kernels_deferred (the check_scorer_coverage floor).
    from infermap.scorers import SCORER_KERNELS
    return sorted(SCORER_KERNELS)


def _goldenflow_transforms() -> list[str]:
    # goldenflow's registered transform identities. `import goldenflow` triggers
    # every transform submodule's registration side-effect (goldenflow/__init__.py
    # imports address/names/email/... ), so list_transforms() sees the full set.
    # Mirrors TS `listTransforms()` (core/transforms/registry.ts); the api_parity
    # `transforms` surface. Intended cross-language deltas (e.g. the TS-only
    # LLM corrector) are declared in parity/goldenflow.yaml.
    import goldenflow  # noqa: F401  (registration side-effects)
    from goldenflow.transforms import list_transforms
    return sorted({t.name for t in list_transforms()})


# The only per-package variance on the Python side is the CLI module path.
_CLI_MODULE = {
    "goldenmatch": "goldenmatch.cli.main",
    "goldencheck": "goldencheck.cli.main",
    "goldenflow": "goldenflow.cli.main",
    "goldenpipe": "goldenpipe.cli.main",
    "goldenanalysis": "goldenanalysis.cli.main",
    "infermap": "infermap.cli",
}

# Each surface -> (callable returning list[str], extra-name for the env-gap message).
REGISTRY = {
    pkg: {"mcp_tools": (_mcp(pkg), "mcp"), "cli_commands": (_cli(mod), None),
          **({"a2a_skills": (_a2a(pkg), "a2a")} if pkg in _A2A_PACKAGES else {}),
          # goldenmatch config surfaces (scorers/transforms/blocking/kernels).
          **({"scorers": (_scorers, None), "transforms": (_transforms, None),
              "blocking_strategies": (_blocking_strategies, None),
              "scorer_kernels": (_scorer_kernels, None)}
             if pkg == "goldenmatch" else {}),
          # infermap's M:N scorer surface + its infermap-core kernel coverage.
          # Declaring both activates check_scorer_coverage for infermap (the same
          # floor that gates goldenmatch), so a new/regressed infermap scorer can't
          # sit un-kernelized or un-declared. Other packages don't yet model a
          # compute surface, so they're skipped.
          **({"scorers": (_infermap_scorers, None),
              "scorer_kernels": (_infermap_scorer_kernels, None)}
             if pkg == "infermap" else {}),
          # goldenflow's transform vocabulary -- the cross-language `transforms`
          # parity surface (mirrors goldenmatch's transforms surface: partition
          # only, no kernel floor). Catches a transform available on one language
          # but not the other; intended deltas live in parity/goldenflow.yaml.
          **({"transforms": (_goldenflow_transforms, None)}
             if pkg == "goldenflow" else {})}
    for pkg, mod in _CLI_MODULE.items()
}


def emit(package: str) -> dict:
    spec = REGISTRY.get(package)
    if spec is None:
        raise SystemExit(f"no parity registry entry for '{package}'")
    out = {"package": package}
    for surface, (fn, extra) in spec.items():
        try:
            out[surface] = sorted(fn())
        except ModuleNotFoundError as e:
            # a surface-bearing OPTIONAL extra is absent -> environment gap, not drift
            sys.stderr.write(f"environment not provisioned for {package}.{surface}: "
                             f"install {package}[{extra}] (missing module: {e.name})\n")
            raise SystemExit(3)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: emit_python_surface.py <package>")
    print(json.dumps(emit(sys.argv[1]), sort_keys=True))
