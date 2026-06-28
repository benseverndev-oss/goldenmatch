"""Run the goldengraph bench evals INSIDE a Modal GPU function -- ~20-50x faster than GitHub CPU.

The 7B-on-CPU GitHub runs took ~30-40 min each (the measure-iterate bottleneck). On a Modal GPU the
same eval is ~1-2 min. The goldengraph native wheel + the Ollama models are cached on a Modal Volume,
so only the FIRST run pays the build/pull; subsequent runs are warm.

AUTH (Modal creds in Infisical, project a99885f0-c5af-4ae1-9dc8-255cc60aa129, env dev):
    $P = "a99885f0-c5af-4ae1-9dc8-255cc60aa129"
    $env:MODAL_TOKEN_ID     = (infisical.cmd secrets get MODAL_TOKEN_ID     --projectId $P --env dev --plain)
    $env:MODAL_TOKEN_SECRET = (infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId $P --env dev --plain)
    pip install modal
    modal run scripts/distill/modal_bench.py::smoke                       # validate auth + GPU
    modal run scripts/distill/modal_bench.py --eval extraction_f1 --n 20  # a real eval
"""
from __future__ import annotations

import pathlib

import modal

# gg-local-llm repo root (used only LOCALLY for the image mounts). On the Modal container the module
# re-imports from a shallow path, so guard the index -- the value is unused remotely.
_parents = pathlib.Path(__file__).resolve().parents
REPO = _parents[2] if len(_parents) > 2 else _parents[0]
app = modal.App("gg-bench")

# Build-time image: rust (maturin), Ollama, the python deps. The repo source is added so the function
# can build the wheel + install goldengraph/goldenmatch from it at runtime (cached on the Volume).
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "build-essential", "pkg-config", "libssl-dev", "git", "zstd")
    .run_commands("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y")
    .env({"PATH": "/root/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"})
    .pip_install("maturin", "goldenmatch", "datasets", "openai", "numpy", "pytest")
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
    .add_local_dir(str(REPO / "packages/rust"), "/repo/packages/rust", ignore=["**/target/**"])
    .add_local_dir(str(REPO / "packages/python/goldengraph"), "/repo/packages/python/goldengraph",
                   ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(str(REPO / "packages/python/goldenmatch"), "/repo/packages/python/goldenmatch",
                   ignore=["**/__pycache__/**", "**/.venv/**", "**/target/**", "**/*.pyc"])
)

cache = modal.Volume.from_name("gg-bench-cache", create_if_missing=True)

_BENCH = "/repo/packages/python/goldenmatch/benchmarks/er-kg-bench"

#: eval name -> (module, extra args). Chat-only evals need no embedder; retrieval/end_to_end do.
_EVAL = {
    "extraction_f1": ("erkgbench.qa_e2e.run_extraction_eval", ["--configs", "api_json,api_schema"]),
    "synthesis_gold": ("erkgbench.qa_e2e.run_synthesis_eval", []),
    "retrieval_coverage": ("erkgbench.qa_e2e.run_retrieval_eval", []),
    "end_to_end": ("erkgbench.qa_e2e.run_qa_e2e", []),  # full pipeline + localize trace (stdout)
}


@app.function(image=image, gpu="A10G", timeout=600)
def smoke() -> dict:
    """Validate auth + GPU + image. Run this FIRST."""
    import subprocess

    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True)
    info = {"gpu": out.stdout.strip(), "ollama": _which("ollama"), "maturin": _which("maturin")}
    print("smoke:", info)
    return info


def _which(exe):
    import shutil

    return shutil.which(exe)


@app.function(image=image, gpu="A10G", volumes={"/cache": cache}, timeout=3600)
def run_bench(eval: str, n: int = 20, ambiguity: float = 0.6, opts: str = "",
              chat: str = "qwen2.5:7b-instruct", embed: str = "nomic-embed-text") -> str:
    import os
    import subprocess
    import time
    import urllib.request

    if eval not in _EVAL:
        raise ValueError(f"unknown eval {eval!r} (choose from {', '.join(_EVAL)})")

    # 1. goldengraph native wheel -- build once, cache on the Volume.
    wheels = "/cache/wheels"
    os.makedirs(wheels, exist_ok=True)
    if not any(f.endswith(".whl") for f in os.listdir(wheels)):
        print("building goldengraph-native wheel (first run) ...", flush=True)
        subprocess.run(
            ["maturin", "build", "--release", "-m",
             "/repo/packages/rust/extensions/goldengraph-native/Cargo.toml", "--out", wheels],
            check=True,
        )
        cache.commit()
    subprocess.run(f"pip install --no-deps {wheels}/*.whl", shell=True, check=True)
    subprocess.run(["pip", "install", "--no-deps", "-e", "/repo/packages/python/goldengraph"], check=True)
    subprocess.run(["pip", "install", "--no-deps", "-e", "/repo/packages/python/goldenmatch"], check=True)

    # 2. Ollama on the GPU; models cached on the Volume.
    os.environ["OLLAMA_MODELS"] = "/cache/ollama"
    os.makedirs("/cache/ollama", exist_ok=True)
    subprocess.Popen(["ollama", "serve"])
    for _ in range(60):
        try:
            urllib.request.urlopen("http://localhost:11434/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    subprocess.run(["ollama", "pull", chat], check=True)
    if embed:
        subprocess.run(["ollama", "pull", embed], check=True)
    cache.commit()

    # 3. Env for the bench (local Ollama via the OpenAI-compatible endpoint).
    env = {
        **os.environ,
        "OPENAI_API_KEY": "ollama",
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_MODEL": chat,
        "OPENAI_EMBED_MODEL": embed,
        "POLARS_SKIP_CPU_CHECK": "1",
    }
    for line in opts.splitlines():
        if "=" in line and line.strip():
            k, v = line.split("=", 1)
            env[k.strip()] = v

    # 4. Run the eval CLI; return the markdown (or, for end_to_end, the localize trace from stdout).
    out_md = "/tmp/out.md"
    if eval == "end_to_end":
        env["GOLDENGRAPH_QA_TRACE"] = "1"
        env["GOLDENGRAPH_QA_TRACE_LIMIT"] = "0"  # trace every question
        proc = subprocess.run(
            ["python", "-m", "erkgbench.qa_e2e.run_qa_e2e", "--engine", "goldengraph",
             "--corpus", "engineered", "--max-questions", str(n), "--ambiguity", str(ambiguity),
             "--out-md", out_md, "--out-json", "/tmp/e2e.json"],
            cwd=_BENCH, env=env, capture_output=True, text=True, check=True,
        )
        results = pathlib.Path(out_md).read_text() if os.path.exists(out_md) else "(no results md)"
        return _persist(eval, n, proc.stdout + "\n\n===== RESULTS_MD =====\n" + results)
    module, extra = _EVAL[eval]
    subprocess.run(
        ["python", "-m", module, *extra, "--n-questions", str(n),
         "--ambiguity", str(ambiguity), "--out-md", out_md],
        cwd=_BENCH, env=env, check=True,
    )
    return _persist(eval, n, pathlib.Path(out_md).read_text())


def _persist(eval: str, n: int, text: str) -> str:
    """Write the result to the cache Volume so a DETACHED run survives local-CLI death -- pull it
    back later with `modal volume get gg-bench-cache results/<eval>_<n>.md .`."""
    import os

    os.makedirs("/cache/results", exist_ok=True)
    pathlib.Path(f"/cache/results/{eval}_{n}.md").write_text(text)
    cache.commit()
    return text


@app.local_entrypoint()
def main(eval: str = "extraction_f1", n: int = 20, ambiguity: float = 0.6, opts: str = "",
         spawn: bool = False) -> None:
    if spawn:
        # fire-and-forget: queue the call SERVER-SIDE and return instantly, so a local-CLI kill can't
        # cancel it. Result lands on the volume at results/<eval>_<n>.md (pull with `modal volume get`).
        # Pair with `modal run --detach` so the app outlives this process.
        call = run_bench.spawn(eval, n=n, ambiguity=ambiguity, opts=opts)
        print(f"SPAWNED call_id={call.object_id} -> results/{eval}_{n}.md on volume gg-bench-cache")
        return
    md = run_bench.remote(eval, n=n, ambiguity=ambiguity, opts=opts)
    print("\n===== RESULT =====\n" + md)
