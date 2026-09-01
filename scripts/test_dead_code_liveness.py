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

from dead_code.liveness import live_modules  # noqa: E402


def test_a_registered_transform_makes_its_module_live():
    live = live_modules()
    assert "goldenflow.transforms.names" in live


def test_a_typer_command_makes_its_module_live():
    live = live_modules()
    assert any(m.startswith("goldenmatch.cli.") for m in live)


def test_the_mcp_surface_is_live():
    live = live_modules()
    assert "goldenmatch.mcp.server" in live


def test_liveness_is_not_trivially_everything():
    """A live set that contains every module would make the detector vacuous --
    it would never report a candidate and would look like a clean bill of
    health."""
    live = live_modules()
    assert 10 < len(live) < 2000
