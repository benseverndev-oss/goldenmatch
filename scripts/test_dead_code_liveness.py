"""Registry-resolved liveness.

The codebase has 1,089 `getattr(` call sites, so a static reference scan will
condemn code that is only ever reached dynamically. These tests pin the
inversion: enumerate what the registries can dispatch to, and treat THAT as
live regardless of what references exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.liveness import (  # noqa: E402
    _cli_modules,
    _connector_registry_modules,
    _entry_hub_modules,
    _entry_point_modules,
    _mcp_modules,
    _transform_modules,
    live_modules,
)
from dead_code.static import unimported_modules  # noqa: E402


def test_a_registered_transform_makes_its_module_live():
    live = live_modules()
    assert "goldenflow.transforms.names" in live


def test_a_typer_command_module_uniquely_found_by_the_command_walk_is_live():
    """`goldenmatch.cli.main` alone is not evidence the typer command-tree walk
    (_cli_modules) ran: _entry_point_modules() supplies it too, straight from
    the console_scripts target in pyproject.toml, with zero command-tree
    introspection. A real witness has to be a module none of the OTHER
    resolvers would ever supply -- so if _cli_modules were removed from the
    `live_modules()` tuple, this assertion would actually go false instead of
    passing on a module a different resolver already covers.
    """
    cli_only = _cli_modules() - (
        _transform_modules()
        | _mcp_modules()
        | _entry_point_modules()
        | _entry_hub_modules()
        | _connector_registry_modules()
    )
    assert cli_only, (
        "no CLI module is uniquely supplied by the typer command-tree walk -- "
        "this test can no longer witness _cli_modules()"
    )
    victim = sorted(cli_only)[0]
    assert victim in live_modules()


def test_the_mcp_surface_is_live():
    live = live_modules()
    assert "goldenmatch.mcp.server" in live


def test_liveness_is_not_trivially_everything():
    """A live set that contains every module would make the detector vacuous --
    it would never report a candidate and would look like a clean bill of
    health."""
    live = live_modules()
    assert 10 < len(live) < 2000


def test_every_connector_registry_module_is_live():
    """Every module `load_connector()`'s `_BUILTIN` dict can dispatch to must be
    live -- that dict is a fifth registry (alongside transforms/cli/mcp/entry
    points) this detector resolves rather than allowlists."""
    registry = _connector_registry_modules()
    assert len(registry) >= 10, (
        "the connector registry AST scan found suspiciously few modules -- "
        "this test can no longer witness the resolution"
    )
    live = live_modules()
    missing = registry - live
    assert not missing, f"connector-registry module(s) not resolved live: {sorted(missing)}"


def test_a_module_reached_only_via_an_entry_hub_is_live():
    """A module that is itself an entry hub (launched externally -- an MCP/A2A/
    REST server bootstrap, a package's own console-script target, ...) has no
    in-repo importer by construction, so the static "no importer" signal alone
    would always flag it. Picked dynamically: a real hub module that is
    statically unimported and would stay a false candidate without the
    entry-hub resolver.
    """
    hub_victims = sorted(_entry_hub_modules() & unimported_modules())
    assert hub_victims, (
        "no entry-hub module is statically unimported -- this test can no "
        "longer witness the entry-hub resolver"
    )
    victim = hub_victims[0]
    assert victim in live_modules()
