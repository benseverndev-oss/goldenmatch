"""CLI command for starting the GoldenMatch MCP server."""

from __future__ import annotations

import typer


def mcp_serve_cmd(
    files: list[str] | None = typer.Argument(None, help="Data files to load"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config YAML file"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio or http"),
    host: str = typer.Option("0.0.0.0", "--host", help="HTTP host (only for http transport)"),
    port: int = typer.Option(8200, "--port", "-p", help="HTTP port (only for http transport)"),
) -> None:
    """Start the GoldenMatch MCP server (for Claude Desktop or hosted deployment)."""
    import asyncio

    if transport == "http":
        from goldenmatch.mcp.server import run_server_http

        asyncio.run(run_server_http(host=host, port=port, file_paths=files, config_path=config))
    else:
        from goldenmatch.mcp.server import run_server

        asyncio.run(run_server(files, config))
