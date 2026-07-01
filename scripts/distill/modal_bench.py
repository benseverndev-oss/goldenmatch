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
    .pip_install("maturin", "goldenmatch", "datasets", "openai", "numpy", "pytest",
                 "lightrag-hku>=1.5,<1.6")  # the LightRAG competitor for the local-7B head-to-head
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
    .add_local_dir(str(REPO / "packages/rust"), "/repo/packages/rust", ignore=["**/target/**"])
    .add_local_dir(str(REPO / "packages/python/goldengraph"), "/repo/packages/python/goldengraph",
                   ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(str(REPO / "packages/python/goldenmatch"), "/repo/packages/python/goldenmatch",
                   ignore=["**/__pycache__/**", "**/.venv/**", "**/target/**", "**/*.pyc"])
)

cache = modal.Volume.from_name("gg-bench-cache", create_if_missing=True)
distill_vol = modal.Volume.from_name("gg-distill", create_if_missing=True)  # the fine-tuned model

_BENCH = "/repo/packages/python/goldenmatch/benchmarks/er-kg-bench"

#: eval name -> (module, extra args). Chat-only evals need no embedder; retrieval/end_to_end do.
_EVAL = {
    "extraction_f1": ("erkgbench.qa_e2e.run_extraction_eval", ["--configs", "api_json,api_schema"]),
    "synthesis_gold": ("erkgbench.qa_e2e.run_synthesis_eval", []),
    "retrieval_coverage": ("erkgbench.qa_e2e.run_retrieval_eval", []),
    "end_to_end": ("erkgbench.qa_e2e.run_qa_e2e", []),  # full pipeline + localize trace (stdout)
    "substrate": ("erkgbench.run_substrate_eval", []),  # substrate-quality A/B ambiguity sweep
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


def _bench_impl(eval: str, n: int, ambiguity: float, opts: str, chat: str, embed: str,
                create_from: str = "", engine: str = "goldengraph",
                corpus: str = "engineered") -> str:
    """Shared bench body -- runs inside the container. Wrapped by run_bench (A10G, for the 7B student)
    and run_bench_big (A100, for the 32B/72B OSS teacher). The teacher ceiling is measured free-to-free:
    a larger same-family OSS model as the distillation target, no paid API."""
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
    if create_from:
        # the fine-tuned student: import the merged HF safetensors dir directly (modern Ollama
        # converts to GGUF internally), no manual llama.cpp step. `chat` is the local model name.
        # COPY the model off the Modal volume first: the volume mounts as a symlink, and Ollama
        # refuses a `FROM` path that escapes via the symlink ("insecure path"). copytree derefs it.
        import shutil

        local_dir = "/tmp/merged_local"
        if not os.path.exists(local_dir):
            print(f"copying {create_from} -> {local_dir} (deref volume symlink) ...", flush=True)
            shutil.copytree(create_from, local_dir, symlinks=False)
        with open("/tmp/Modelfile", "w") as mf:
            mf.write(f"FROM {local_dir}\n")
        print(f"ollama create {chat} from {local_dir} ...", flush=True)
        subprocess.run(["ollama", "create", chat, "-f", "/tmp/Modelfile"], check=True)
    else:
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

    # If a separate synonym-judge model is requested, pull it too (served alongside the chat model;
    # Ollama loads each on demand). Needs a big enough GPU to hold both (use --gpu a100).
    judge_model = env.get("GOLDENGRAPH_DISCOVER_JUDGE_MODEL", "").strip()
    if judge_model and judge_model != chat:
        subprocess.run(["ollama", "pull", judge_model], check=True)
        cache.commit()

    # 4. Run the eval CLI; return the markdown (or, for end_to_end, the localize trace from stdout).
    out_md = "/tmp/out.md"
    if eval == "end_to_end":
        env["GOLDENGRAPH_QA_TRACE"] = "1"
        env["GOLDENGRAPH_QA_TRACE_LIMIT"] = "0"  # trace every question
        # MuSiQue fetches a seeded subset from HF on demand (datasets is in the image); engineered is
        # generated locally (ambiguity dial applies). The corpus is a CLI choice in run_qa_e2e.
        proc = subprocess.run(
            ["python", "-m", "erkgbench.qa_e2e.run_qa_e2e", "--engine", engine,
             "--corpus", corpus, "--max-questions", str(n), "--ambiguity", str(ambiguity),
             "--out-md", out_md, "--out-json", "/tmp/e2e.json"],
            cwd=_BENCH, env=env, capture_output=True, text=True, check=True,
        )
        results = pathlib.Path(out_md).read_text() if os.path.exists(out_md) else "(no results md)"
        # suffix non-default corpora so musique results don't overwrite the engineered file
        csuf = "" if corpus == "engineered" else f"-{corpus}"
        return _persist(eval, n, f"{engine}-{chat}{csuf}",
                        proc.stdout + "\n\n===== RESULTS_MD =====\n" + results)
    if eval == "substrate":
        # Substrate-quality A/B ambiguity sweep (engineered corpus; the `--ambiguity` list overrides the
        # scalar `ambiguity` arg). Level A resolver-isolation vs Level B end-to-end build; A-B gap = the
        # construction ceiling as a number. goldengraph only (needs the native store + resolver).
        _amb = (env.get("GOLDENGRAPH_SUBSTRATE_AMBIGUITY", "").split()
                or ["0.0", "0.3", "0.6"])
        _corpus = env.get("GOLDENGRAPH_SUBSTRATE_CORPUS", "").strip() or "engineered"
        proc = subprocess.run(
            ["python", "-m", "erkgbench.run_substrate_eval",
             "--corpus", _corpus, "--ambiguity", *_amb, "--out-md", out_md],
            cwd=_BENCH, env=env, capture_output=True, text=True, check=True,
        )
        results = pathlib.Path(out_md).read_text() if os.path.exists(out_md) else "(no results md)"
        return _persist(eval, n, f"{engine}-{chat}",
                        proc.stdout + "\n\n===== RESULTS_MD =====\n" + results)
    module, extra = _EVAL[eval]
    subprocess.run(
        ["python", "-m", module, *extra, "--n-questions", str(n),
         "--ambiguity", str(ambiguity), "--out-md", out_md],
        cwd=_BENCH, env=env, check=True,
    )
    return _persist(eval, n, chat, pathlib.Path(out_md).read_text())


@app.function(image=image, gpu="A10G", volumes={"/cache": cache}, timeout=5400,
              secrets=[modal.Secret.from_name("goldengraph-synth")])
def run_bench(eval: str, n: int = 20, ambiguity: float = 0.6, opts: str = "",
              chat: str = "qwen2.5:7b-instruct", embed: str = "nomic-embed-text",
              engine: str = "goldengraph", corpus: str = "engineered") -> str:
    return _bench_impl(eval, n, ambiguity, opts, chat, embed, engine=engine, corpus=corpus)


@app.function(image=image, gpu="A100", volumes={"/cache": cache}, timeout=10800)
def run_bench_big(eval: str, n: int = 20, ambiguity: float = 0.6, opts: str = "",
                  chat: str = "qwen2.5:32b", embed: str = "nomic-embed-text",
                  engine: str = "goldengraph", corpus: str = "engineered") -> str:
    """Bigger GPU for the OSS teacher (32B fits A100-40GB q4; 72B needs A100-80GB)."""
    return _bench_impl(eval, n, ambiguity, opts, chat, embed, engine=engine, corpus=corpus)


@app.function(image=image, gpu="A10G", volumes={"/cache": cache, "/distill": distill_vol},
              timeout=7200)
def run_bench_distilled(merged: str = "auto", eval: str = "end_to_end", n: int = 60,
                        ambiguity: float = 0.0, opts: str = "", embed: str = "nomic-embed-text") -> str:
    """Bench the FINE-TUNED student: import its merged HF dir from the gg-distill volume into Ollama,
    then run the eval. `merged="auto"` (default) discovers the newest `/distill/out/*/merged` SERVER-SIDE
    -- avoids passing a leading-slash path through local Git Bash (MSYS mangles it to a Windows path)."""
    import glob
    import os

    if merged in ("", "auto"):
        cands = sorted(glob.glob("/distill/out/*/merged"), key=os.path.getmtime)
        if not cands:
            raise RuntimeError("no /distill/out/*/merged model found -- train one first")
        merged = cands[-1]
    elif not merged.startswith("/distill"):
        merged = f"/distill/{merged.lstrip('/')}"
    print(f"distilled model dir: {merged}", flush=True)
    return _bench_impl(eval, n, ambiguity, opts, "gg-distilled", embed, create_from=merged)


def _persist(eval: str, n: int, chat: str, text: str) -> str:
    """Write the result to the cache Volume so a DETACHED run survives local-CLI death. Filename is
    tagged with the model so teacher (32B) and student (7B) runs don't collide -- pull it back with
    `modal volume get gg-bench-cache results/<eval>_<n>_<model>.md .`."""
    import os

    os.makedirs("/cache/results", exist_ok=True)
    tag = chat.replace(":", "-").replace("/", "-")
    pathlib.Path(f"/cache/results/{eval}_{n}_{tag}.md").write_text(text)
    cache.commit()
    return text


@app.local_entrypoint()
def main(eval: str = "extraction_f1", n: int = 20, ambiguity: float = 0.6, opts: str = "",
         spawn: bool = False, chat: str = "qwen2.5:7b-instruct",
         embed: str = "nomic-embed-text", gpu: str = "a10g", merged: str = "",
         engine: str = "goldengraph", corpus: str = "engineered") -> None:
    # --merged <path>: bench the fine-tuned student (Ollama-imported from the gg-distill volume).
    if merged:
        tag = "gg-distilled"
        if spawn:
            call = run_bench_distilled.spawn(merged, eval=eval, n=n, ambiguity=ambiguity,
                                             opts=opts, embed=embed)
            print(f"SPAWNED call_id={call.object_id} -> results/{eval}_{n}_{tag}.md on gg-bench-cache")
            return
        print("\n===== RESULT =====\n" + run_bench_distilled.remote(
            merged, eval=eval, n=n, ambiguity=ambiguity, opts=opts, embed=embed))
        return
    fn = run_bench_big if gpu.lower() in ("a100", "a100-80gb", "big") else run_bench
    # head-to-head: end_to_end + substrate tag results by engine (`{eng}-{chat}`), so concurrent engine
    # runs on the same local model don't collide. Non-default corpora get a `-{corpus}` suffix. This MUST
    # mirror the tag `_persist` actually writes, or the printed SPAWNED path points at the wrong file.
    csuf = "" if corpus == "engineered" else f"-{corpus}"
    tag = (f"{engine}-{chat}{csuf}" if eval in ("end_to_end", "substrate") else chat).replace(":", "-").replace("/", "-")
    if spawn:
        # fire-and-forget: queue the call SERVER-SIDE and return instantly, so a local-CLI kill can't
        # cancel it. Result lands at results/<eval>_<n>_<model>.md. Pair with `modal run --detach`.
        call = fn.spawn(eval, n=n, ambiguity=ambiguity, opts=opts, chat=chat, embed=embed,
                        engine=engine, corpus=corpus)
        print(f"SPAWNED call_id={call.object_id} -> results/{eval}_{n}_{tag}.md on volume gg-bench-cache")
        return
    md = fn.remote(eval, n=n, ambiguity=ambiguity, opts=opts, chat=chat, embed=embed,
                   engine=engine, corpus=corpus)
    print("\n===== RESULT =====\n" + md)


@app.function(image=image, gpu="A10G", timeout=600, volumes={"/cache": cache})
def cosine_probe() -> str:
    """Embedding-DISCRIMINATION probe (free, ~2 min): does nomic-embed-text separate true relation
    SYNONYMS (works at / on staff at -> should merge) from DISTINCT relations (acquired / authored ->
    must NOT)? Embeds the 5x3 paraphrase set, prints the within-synonym vs across-relation cosine bands.
    If the bands overlap, NO threshold works and embedding-assisted argctx is dead on this embedder --
    decided WITHOUT a full e2e gamble."""
    import itertools
    import os
    import subprocess
    import time
    import urllib.request

    import numpy as np
    from openai import OpenAI

    PHR = {
        "works_at": ("works at", "is employed at", "is on staff at"),
        "located_in": ("located in", "is based in", "sits within"),
        "acquired": ("acquired", "took over", "bought out"),
        "authored": ("authored", "wrote", "penned"),
        "part_of": ("part of", "belongs to", "is a component of"),
    }
    os.environ["OLLAMA_MODELS"] = "/cache/ollama"
    os.makedirs("/cache/ollama", exist_ok=True)
    subprocess.Popen(["ollama", "serve"])
    for _ in range(60):
        try:
            urllib.request.urlopen("http://localhost:11434/api/version", timeout=2); break
        except Exception:
            time.sleep(1)
    subprocess.run(["ollama", "pull", "nomic-embed-text"], check=True)
    cache.commit()
    cli = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")

    phrases, label = [], []
    for rel, ps in PHR.items():
        for p in ps:
            phrases.append(p); label.append(rel)
    vecs = np.asarray([d.embedding for d in cli.embeddings.create(model="nomic-embed-text", input=phrases).data], dtype=float)
    unit = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12)
    sim = unit @ unit.T

    within, across = [], []
    for i, j in itertools.combinations(range(len(phrases)), 2):
        (within if label[i] == label[j] else across).append((sim[i, j], phrases[i], phrases[j]))
    within.sort(); across.sort(reverse=True)
    lines = ["=== WITHIN-synonym cosines (SHOULD be high -> merge) ==="]
    for s, a, b in within:
        lines.append(f"  {s:.3f}  '{a}' ~ '{b}'")
    lines.append("=== ACROSS-relation cosines, top (should be LOW -> stay apart) ===")
    for s, a, b in across[:10]:
        lines.append(f"  {s:.3f}  '{a}' ~ '{b}'")
    wmin = min(s for s, _, _ in within); amax = max(s for s, _, _ in across)
    sep = wmin - amax
    lines.append(f"\nmin(within-synonym) = {wmin:.3f}   max(across-relation) = {amax:.3f}")
    lines.append(f"separation margin   = {sep:+.3f}   "
                 + ("SEPARABLE -- a threshold exists" if sep > 0 else "OVERLAP -- NO threshold separates synonyms from distinct relations"))
    out = "\n".join(lines)
    print(out)
    return out
