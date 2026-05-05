from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

import typer

from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState


def serve_ui_cmd(
    path: Path | None = typer.Argument(None, help="Optional run dir or project dir."),
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    port: int = typer.Option(5050, help="Port (0 = pick free, default 5050 matches Vite proxy)."),
    dev: bool = typer.Option(False, help="Skip static; expect Vite at :5173."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Launch the local web UI."""
    try:
        import uvicorn
    except ImportError as exc:
        raise typer.BadParameter(
            "goldenmatch[web] extra not installed. Run: pip install 'goldenmatch[web]'"
        ) from exc

    project_dir = (path or Path.cwd()).resolve()
    if not project_dir.is_dir():
        # path was a file (e.g. a lineage.json) — use its parent
        project_dir = project_dir.parent

    state = AppState.from_project_dir(project_dir)
    app = create_app(state)

    if port == 0:
        with socket.socket() as s:
            s.bind((host, 0))
            port = s.getsockname()[1]

    url = f"http://{host}:{port}"
    typer.echo(f"goldenmatch UI -> {url}")
    if open_browser and not dev:
        webbrowser.open(url)

    uvicorn.run(app, host=host, port=port, log_level="info")
