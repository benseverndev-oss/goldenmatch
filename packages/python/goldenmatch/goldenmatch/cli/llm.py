"""``goldenmatch llm`` — local-model convenience commands (D1 Path A).

``serve-local`` launches a llama.cpp OpenAI-compatible server on the pinned
self-hosted model, then prints the two env vars that point the existing LLM
scorer at it (``provider="openai"`` + ``GOLDENMATCH_LLM_BASE_URL``). This is the
zero-core-code path: the scorer's ``_openai_base_url()`` / ``_detect_provider()``
already accept a local base URL with a stub key, so no scoring change is needed —
just a launcher + the model resolution shared with the in-process adapter
(``core/_llm_loader.py``).

Spec: docs/superpowers/specs/2026-07-29-local-model-integration-design.md
"""

from __future__ import annotations

import typer

llm_app = typer.Typer(help="Local-model helpers for the LLM-scorer tier.")


def _llama_server_available() -> bool:
    """Whether llama-cpp-python (the OpenAI-compatible server) is importable.
    Uses find_spec so the check itself never imports the heavy module."""
    import importlib.util

    return importlib.util.find_spec("llama_cpp") is not None


def _launch_server(model_path: str, host: str, port: int) -> None:  # pragma: no cover - execs a server
    """Run llama.cpp's OpenAI-compatible server (blocking). Separated so the
    command's resolve/guard logic is testable without launching anything."""
    import uvicorn
    from llama_cpp.server.app import create_app  # pyright: ignore[reportMissingImports]
    from llama_cpp.server.settings import ModelSettings, ServerSettings

    server_settings = ServerSettings(host=host, port=port)
    model_settings = [ModelSettings(model=model_path)]
    app = create_app(server_settings=server_settings, model_settings=model_settings)
    uvicorn.run(app, host=host, port=port)


@llm_app.command("serve-local", help="Serve the pinned local ER-matcher over an OpenAI-compatible endpoint.")
def serve_local_cmd(
    host: str = typer.Option("127.0.0.1", help="Bind host (loopback by default)."),
    port: int = typer.Option(8081, help="Bind port."),
    model_path: str | None = typer.Option(
        None, "--model-path",
        help="Explicit GGUF path (else the pinned model is resolved / downloaded).",
    ),
) -> None:
    from goldenmatch.core._llm_loader import resolve_model_path

    if model_path is None:
        import os

        if model_path is None:
            # Honor the explicit-override env the loader also reads.
            model_path = os.environ.get("GOLDENMATCH_LOCAL_LLM_PATH")
        if model_path is None:
            model_path = resolve_model_path()

    if not model_path:
        typer.echo(
            "No local model could be resolved. Install the extra "
            "(pip install goldenmatch[local-llm]) so the pinned model can be "
            "downloaded, or pass --model-path /path/to/model.gguf.",
            err=True,
        )
        raise typer.Exit(1)

    if not _llama_server_available():
        typer.echo(
            "llama-cpp-python is not installed. "
            "Install it: pip install goldenmatch[local-llm].",
            err=True,
        )
        raise typer.Exit(1)

    base_url = f"http://{host}:{port}/v1"
    typer.echo(f"Serving {model_path} at {base_url}")
    typer.echo("Point the LLM scorer at it:")
    typer.echo(f"  export GOLDENMATCH_LLM_BASE_URL={base_url}")
    typer.echo("  # then set the scorer provider to 'openai' (a stub key is auto-supplied)")
    _launch_server(model_path, host, port)
