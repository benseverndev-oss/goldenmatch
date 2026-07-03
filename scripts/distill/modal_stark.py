"""SP2 STaRK feasibility spike INSIDE a Modal function -- does goldengraph's ingest +
retrieve path run at STaRK scale, and what are the numbers?

Downloads a STaRK KB (PRIME first -- smallest, ~130K nodes), bulk-loads it into the
native store (single batch; chunked fallback on OOM), builds an EntityIndex over ALL
nodes, and runs two retrieval arms over a query sample:
  Arm A (dense)  -- EntityIndex.query, pure vectors, covers every node.
  Arm B (graph)  -- seeds + 1-hop walk THROUGH the store's as_of().query.
Reports: n_nodes/n_edges/n_dropped/n_batches/n_isolated, ingest wall, index-build
wall, per-query latency, peak RSS, and the dense-vs-graph metric table.

The ER moat is NOT exercised (vanilla STaRK is pre-resolved) -- this proves
"structure loads + retrieves at scale," the honest scope. See the spec.

AUTH (Modal creds in Infisical, project a99885f0-c5af-4ae1-9dc8-255cc60aa129, env dev):
    $P = "a99885f0-c5af-4ae1-9dc8-255cc60aa129"
    $env:MODAL_TOKEN_ID     = (infisical.cmd secrets get MODAL_TOKEN_ID     --projectId $P --env dev --plain)
    $env:MODAL_TOKEN_SECRET = (infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId $P --env dev --plain)
    pip install modal
    modal run --detach scripts/distill/modal_stark.py --kb prime --sample 200
    # then: modal volume get gg-bench-cache results/stark_prime.md .
"""
from __future__ import annotations

import pathlib

import modal

_parents = pathlib.Path(__file__).resolve().parents
REPO = _parents[2] if len(_parents) > 2 else _parents[0]
app = modal.App("gg-stark")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "build-essential", "pkg-config", "libssl-dev", "git", "zstd")
    .run_commands("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y")
    .env({"PATH": "/root/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"})
    .pip_install("maturin", "goldenmatch", "numpy", "openai")
    # STaRK data deps only. stark-qa's FULL tree drags in the retrieval-model baselines
    # (colbert-ai/gritlm/mteb/wandb) -- exactly what goldengraph replaces -- and pip backtracks
    # forever resolving them. Install the SKB/QA DATA loaders with --no-deps + just what they need:
    # torch (SKB edge tensors), pandas/hf_hub/gdown (download+parse), PyTDC (PRIME's tdc.resource),
    # ogb (skb/__init__ eagerly imports amazon+mag which need ogb.utils.url regardless of --kb).
    .pip_install("torch", "pandas", "huggingface_hub", "gdown", "requests", "PyTDC", "ogb")
    .pip_install("stark-qa", extra_options="--no-deps")
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
    .add_local_dir(str(REPO / "packages/rust"), "/repo/packages/rust", ignore=["**/target/**"])
    .add_local_dir(str(REPO / "packages/python/goldengraph"), "/repo/packages/python/goldengraph",
                   ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(str(REPO / "packages/python/goldenmatch"), "/repo/packages/python/goldenmatch",
                   ignore=["**/__pycache__/**", "**/.venv/**", "**/target/**", "**/*.pyc"])
)

cache = modal.Volume.from_name("gg-bench-cache", create_if_missing=True)
_BENCH = "/repo/packages/python/goldenmatch/benchmarks/er-kg-bench"


def _peak_rss_gb() -> float:
    """Process high-water RSS in GB (monotonic). Linux ru_maxrss is in KB."""
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


class _OllamaEmbedder:
    """Minimal Embedder: batched embeddings via the local Ollama OpenAI-compatible
    endpoint. `.embed(texts) -> np.ndarray`. Self-contained (no goldenmatch provider
    wiring) so the spike measures a clean name-embedding path."""

    def __init__(self, model: str, base_url: str, batch: int = 256):
        from openai import OpenAI

        self._cli = OpenAI(api_key="ollama", base_url=base_url)
        self._model = model
        self._batch = batch

    def embed(self, texts):
        import numpy as np

        texts = [t if str(t).strip() else " " for t in texts]  # Ollama 400s on empty input
        out = []
        for i in range(0, len(texts), self._batch):
            chunk = texts[i : i + self._batch]
            resp = self._cli.embeddings.create(model=self._model, input=chunk)
            out.extend(d.embedding for d in resp.data)
        return np.asarray(out, dtype=float)


def _build_native_and_install():
    """Build the goldengraph-native wheel (cached on the Volume) + install the two
    packages editable. Mirrors modal_bench._bench_impl step 1 (clean+force-reinstall
    so a source change always lands)."""
    import os
    import subprocess

    wheels = "/cache/wheels"
    os.makedirs(wheels, exist_ok=True)
    cache.reload()
    if not any(f.endswith(".whl") for f in os.listdir(wheels)):
        print("building goldengraph-native wheel (first run) ...", flush=True)
        subprocess.run(["cargo", "clean", "--manifest-path",
                        "/repo/packages/rust/extensions/goldengraph-native/Cargo.toml"], check=False)
        subprocess.run(["maturin", "build", "--release", "-m",
                        "/repo/packages/rust/extensions/goldengraph-native/Cargo.toml",
                        "--out", wheels], check=True)
        cache.commit()
    subprocess.run(f"pip install --no-deps --force-reinstall {wheels}/*.whl", shell=True, check=True)
    subprocess.run(["pip", "install", "--no-deps", "-e", "/repo/packages/python/goldengraph"], check=True)
    subprocess.run(["pip", "install", "--no-deps", "-e", "/repo/packages/python/goldenmatch"], check=True)


def _start_ollama(embed_model: str) -> str:
    import os
    import subprocess
    import time
    import urllib.request

    os.environ["OLLAMA_MODELS"] = "/cache/ollama"
    os.makedirs("/cache/ollama", exist_ok=True)
    subprocess.Popen(["ollama", "serve"])
    for _ in range(60):
        try:
            urllib.request.urlopen("http://localhost:11434/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    subprocess.run(["ollama", "pull", embed_model], check=True)
    cache.commit()
    return "http://localhost:11434/v1"


def _stark_impl(kb: str, sample: int, embed_model: str, chunk_edges: int) -> str:
    import os
    import sys
    import time

    _build_native_and_install()
    base_url = _start_ollama(embed_model)
    os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
    # `pip install -e` ran in a SUBPROCESS; its .pth is only read at interpreter startup, so this
    # already-running process can't import the editable packages. Put the (flat-layout) src roots on
    # sys.path directly -- the native wheel installs into site-packages so it imports fine.
    for _p in ("/repo/packages/python/goldengraph", "/repo/packages/python/goldenmatch", _BENCH):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    from erkgbench.stark_adapter import evaluate, load_stark_kb
    from goldengraph.entity_index import EntityIndex
    from goldengraph_native import _native as ggn

    _BIG = 1 << 62
    lines = [f"# STaRK feasibility -- {kb} (sample={sample})", ""]

    # 1. download + map
    t = time.perf_counter()
    nodes, edges, queries = load_stark_kb(kb, split="test", limit_queries=sample)
    lines.append(f"load_stark_kb: {time.perf_counter() - t:.1f}s  "
                 f"nodes={len(nodes)} edges={len(edges)} queries={len(queries)}")

    # 2. bulk_load -> store  [ingest wall + RSS]. Single batch; chunked on OOM.
    from goldengraph.bulk import bulk_load

    store = ggn.PyStore()
    t = time.perf_counter()
    ce = chunk_edges or None
    try:
        stats = bulk_load(store, nodes, edges, chunk_edges=ce)
    except MemoryError:
        fallback = 500_000
        lines.append(f"** single-batch OOM at nodes={len(nodes)} edges={len(edges)} "
                     f"-> retry chunk_edges={fallback} (FINDING) **")
        store = ggn.PyStore()
        stats = bulk_load(store, nodes, edges, chunk_edges=fallback)
    ingest_s = time.perf_counter() - t
    lines.append(f"bulk_load: {ingest_s:.1f}s  {stats}  peak_rss={_peak_rss_gb():.2f}GB")

    # 3. index over ALL nodes (entity_id=int(stark_id) -> query returns stark ids; isolated
    #    nodes stay in the dense baseline). [index-build wall + RSS]
    embedder = _OllamaEmbedder(embed_model, base_url)
    index_entities = [{"entity_id": int(sid), "canonical_name": name, "typ": typ}
                      for sid, name, typ in nodes]
    t = time.perf_counter()
    index = EntityIndex.build(index_entities, embedder, top_k=50)
    build_s = time.perf_counter() - t
    lines.append(f"EntityIndex.build: {build_s:.1f}s  indexed={len(index)}  peak_rss={_peak_rss_gb():.2f}GB")

    # 4. slice + stark<->slice-eid maps (edge-endpoint nodes only)
    slice_graph = store.as_of(_BIG, _BIG)
    stark_to_eid = {int(e["source_refs"][0]): e["entity_id"]
                    for e in slice_graph.entities() if e["source_refs"]}
    eid_to_stark = {v: k for k, v in stark_to_eid.items()}
    n_isolated = len(nodes) - len(stark_to_eid)
    lines.append(f"slice: endpoint_nodes={len(stark_to_eid)}  isolated_nodes={n_isolated} "
                 f"(absent from as_of -- dense still covers them via the full-node index)")

    # 5. both arms
    for arm in ("dense", "graph"):
        r = evaluate(index, slice_graph, stark_to_eid, eid_to_stark, queries, embedder, arm=arm)
        lines.append(
            f"\n[{arm}] hit@1={r['hit@1']:.3f} hit@5={r['hit@5']:.3f} "
            f"recall@20={r['recall@20']:.3f} mrr={r['mrr']:.3f}  "
            f"lat_mean={r['latency_ms_mean']:.1f}ms lat_p95={r['latency_ms_p95']:.1f}ms "
            f"(n={r['n_queries']}, with_gold={r['n_with_gold']})"
        )

    lines.append(f"\npeak_rss_final={_peak_rss_gb():.2f}GB")
    lines.append("\nNOTE: EntityIndex embeds node NAMES (not STaRK full node text), so absolute "
                 "numbers are NOT leaderboard-comparable; the dense-vs-graph DELTA is the signal.")
    text = "\n".join(lines)
    print(text, flush=True)

    os.makedirs("/cache/results", exist_ok=True)
    pathlib.Path(f"/cache/results/stark_{kb}.md").write_text(text)
    cache.commit()
    return text


@app.function(image=image, gpu="A10G", volumes={"/cache": cache}, timeout=10800, memory=65536)
def run_stark(kb: str = "prime", sample: int = 200, embed_model: str = "nomic-embed-text",
              chunk_edges: int = 0) -> str:
    return _stark_impl(kb, sample, embed_model, chunk_edges)


@app.local_entrypoint()
def main(kb: str = "prime", sample: int = 200, embed_model: str = "nomic-embed-text",
         chunk_edges: int = 0, spawn: bool = False) -> None:
    if spawn:
        call = run_stark.spawn(kb=kb, sample=sample, embed_model=embed_model, chunk_edges=chunk_edges)
        print(f"SPAWNED call_id={call.object_id} -> results/stark_{kb}.md on gg-bench-cache")
        return
    print("\n===== RESULT =====\n" + run_stark.remote(
        kb=kb, sample=sample, embed_model=embed_model, chunk_edges=chunk_edges))
