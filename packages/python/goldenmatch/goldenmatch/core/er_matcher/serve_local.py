"""Launch a local OpenAI-compatible ER-matcher server (P6, Path A).

Resolves the pinned GGUF (download + sha256-verify) and starts
``llama_cpp.server`` on it, exposing ``/v1/chat/completions`` that the existing
`llm_score_pairs` path already consumes via ``GOLDENMATCH_LLM_BASE_URL``. So the
whole Path-A boost is:

    python -m goldenmatch.core.er_matcher.serve_local --tier 3b --port 8080
    export GOLDENMATCH_LLM_BASE_URL=http://localhost:8080/v1
    # ...then run goldenmatch with the LLM boost enabled -- offline, no API key.

Deliberately NOT a Typer CLI subcommand: that would add to the api_parity
`cli_commands` surface (needing a manifest + TS-parity entry) for a thin,
runtime-optional launcher. A runnable module keeps it off that gate.

``llama-cpp-python[server]`` is an optional runtime dep (the future
`goldenmatch[local-llm]` extra); this module degrades to a clear install hint
when it is absent, so importing it is always safe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_server_argv(model_path: Path, host: str, port: int, n_ctx: int = 2048) -> list[str]:
    """The argv to launch llama_cpp.server on the model. Pure + unit-tested."""
    return [
        sys.executable, "-m", "llama_cpp.server",
        "--model", str(model_path),
        "--host", host,
        "--port", str(port),
        "--n_ctx", str(n_ctx),
        "--chat_format", "chatml",  # Qwen2.5 uses ChatML
    ]


def _have_server() -> bool:
    import importlib.util
    return importlib.util.find_spec("llama_cpp") is not None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Serve the local ER-matcher (Path A).")
    ap.add_argument("--tier", default="3b", help="model tier (3b default, 7b optional)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--n-ctx", type=int, default=2048)
    args = ap.parse_args(argv)

    if not _have_server():
        print(
            "llama-cpp-python is not installed. Install the optional extra:\n"
            "    pip install 'goldenmatch[local-llm]'   # or: pip install 'llama-cpp-python[server]'",
            file=sys.stderr,
        )
        return 2

    from goldenmatch.core.er_matcher.registry import resolve_model
    model_path = resolve_model(args.tier)  # download + sha256-verify (raises if PENDING)
    cmd = build_server_argv(model_path, args.host, args.port, args.n_ctx)
    print(f"Serving {model_path.name} on http://{args.host}:{args.port}/v1")
    print(f"  export GOLDENMATCH_LLM_BASE_URL=http://{args.host}:{args.port}/v1")
    import subprocess
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
