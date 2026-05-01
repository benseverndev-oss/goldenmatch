"""goldensuite-mcp — one MCP server, all Golden Suite tools.

Aggregates the MCP tool surfaces of:
- goldenmatch (entity resolution)
- goldencheck (data quality)
- goldenflow  (data transformation)
- goldenpipe  (orchestrator)
- infermap    (schema mapping)

into a single Server. Tools register first-wins on name collisions; collisions
are logged at startup so deployers know which tool was shadowed.
"""
from goldensuite_mcp.server import create_server

__version__ = "0.1.0"
__all__ = ["create_server", "__version__"]
