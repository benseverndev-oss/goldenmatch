"""Optional MCP server for GoldenAnalysis (the ``[mcp]`` extra).

``goldenanalysis.mcp.server`` exposes module-level ``TOOLS`` + ``HANDLERS`` so the
in-process ``goldensuite-mcp`` aggregator surfaces them transitively, plus a
stdio / Streamable-HTTP server for standalone use (``goldenanalysis mcp-serve``).
"""
