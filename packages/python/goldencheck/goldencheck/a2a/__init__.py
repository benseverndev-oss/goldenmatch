"""GoldenCheck A2A (Agent-to-Agent) server."""
from __future__ import annotations

try:
    from goldencheck.a2a.server import AGENT_CARD, create_a2a_app  # noqa: F401
    __all__ = ["AGENT_CARD", "create_a2a_app"]
except ImportError:
    # aiohttp not installed — agent extras not available
    __all__ = []
