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
    # ogb (skb/__init__ eagerly imports amazon+mag which need ogb.utils.url regardless of --kb);
    # torch_geometric (base SKB knowledge_base.py uses torch_geometric.utils -- pure-Python, no
    # torch-scatter/sparse C++ ext needed for is_undirected/to_undirected).
    .pip_install("torch", "pandas", "huggingface_hub", "gdown", "requests", "PyTDC", "ogb",
                 "torch_geometric")
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


def _stark_impl(kb: str, sample: int, embed_model: str, chunk_edges: int,
                text_mode: str = "names", inject: bool = False, k: int = 3, seed: int = 0,
                bridge: bool = False, bridge_cap: int = 8) -> str:
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
    _mode = (f"INJECT-{'BRIDGE' if bridge else 'ANSWER'} k={k} seed={seed}" if inject
             else f"text_mode={text_mode}")
    lines = [f"# STaRK -- {kb} (sample={sample}, {_mode})", ""]

    # 1. download + map. with_text builds the fair-baseline corpus (each node's intrinsic doc,
    #    add_rel=False -- NO relations). Injection needs the docs too (it fragments them).
    t = time.perf_counter()
    nodes, edges, queries, node_texts = load_stark_kb(
        kb, split="test", limit_queries=sample, with_text=(text_mode == "full" or inject))
    lines.append(f"load_stark_kb: {time.perf_counter() - t:.1f}s  "
                 f"nodes={len(nodes)} edges={len(edges)} queries={len(queries)}")

    if inject:
        return _run_moat(lines, nodes, edges, queries, node_texts, k, seed, kb,
                         embed_model, base_url, ggn, EntityIndex, _BIG,
                         bridge=bridge, bridge_cap=bridge_cap)

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
    #    nodes stay in the dense baseline). names mode embeds the node NAME; full mode embeds the
    #    node's intrinsic DOC (fair dense baseline). canonical_name is just the index's embed text
    #    here -- the store keeps the real name, so display/walk are unaffected. [build wall + RSS]
    embedder = _OllamaEmbedder(embed_model, base_url)
    if text_mode == "full":
        corpus = [(node_texts[i] or name) for i, (sid, name, typ) in enumerate(nodes)]
    else:
        corpus = [name for sid, name, typ in nodes]
    index_entities = [{"entity_id": int(sid), "canonical_name": corpus[i], "typ": typ}
                      for i, (sid, name, typ) in enumerate(nodes)]
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
    if text_mode == "full":
        lines.append("\nNOTE: EntityIndex embeds each node's INTRINSIC doc (name+description, "
                     "add_rel=False -- NO relations), so the graph walk is the only structural "
                     "signal. Compare vs the names-mode run: does the graph delta SURVIVE a strong "
                     "dense baseline? Still not STaRK-leaderboard (their docs add relations).")
    else:
        lines.append("\nNOTE: EntityIndex embeds node NAMES (not full node text), so absolute "
                     "numbers are NOT leaderboard-comparable; the dense-vs-graph DELTA is the signal.")
    text = "\n".join(lines)
    print(text, flush=True)

    os.makedirs("/cache/results", exist_ok=True)
    pathlib.Path(f"/cache/results/stark_{kb}_{text_mode}.md").write_text(text)
    cache.commit()
    return text


def _run_moat(lines, nodes, edges, queries, node_texts, k, seed, kb,
              embed_model, base_url, ggn, EntityIndex, _BIG, bridge=False, bridge_cap=8):
    """The ER-moat experiment: fragment entities into k alias nodes (split doc + edges),
    then compare 3 resolution conditions x 2 arms. Two injection targets:
      - default (Case A): fragment the sampled queries' GOLD ANSWER entities. CONFOUNDED
        -- equivalence scoring gives dense k retrieval chances at the gold (see the
        2026-07-03 verdict); fragmentation HELPS, inverting the signal.
      - bridge=True (Case B): fragment the gold answers' 1-HOP NEIGHBORS, answers INTACT.
        No equivalence inflation (gold single); severs the graph WALK's route to the
        answer -> moat is read on the GRAPH arm, dense = flat control.
    See docs/superpowers/specs/2026-07-02-goldengraph-stark-alias-moat-design.md."""
    import os
    import time

    from erkgbench.stark_adapter import evaluate
    from erkgbench.stark_resolve import resolve_aliases
    from goldengraph.bulk import bulk_load
    from goldengraph.stark_inject import bridge_targets, inject_aliases
    from goldengraph.stark_moat import build_clusters, collapse_for_index, collapse_for_store

    gold_ids = {str(g) for _q, gold in queries for g in gold}
    if bridge:
        target_ids = bridge_targets(edges, gold_ids, cap=bridge_cap)   # Case B: fragment the BRIDGE
    else:
        target_ids = gold_ids                                          # Case A: fragment the ANSWER
    t = time.perf_counter()
    nodes2, texts2, edges2, canon = inject_aliases(nodes, node_texts, edges, target_ids, k=k, seed=seed)
    alias_nodes = [(nid, name) for nid, name, _typ in nodes2 if canon.get(nid) != nid]
    all_ids = [n[0] for n in nodes2]
    lines.append(f"inject: {time.perf_counter() - t:.1f}s  targets={len(target_ids)} k={k}  "
                 f"nodes {len(nodes)}->{len(nodes2)} (aliases={len(alias_nodes)})  "
                 f"edges {len(edges)}->{len(edges2)}")
    lines.append("clean reference (PR #1402, no injection): dense recall@20=0.261 mrr=0.151 ; "
                 "graph recall@20=0.213 mrr=0.150")

    embedder = _OllamaEmbedder(embed_model, base_url)
    rows = []
    for method in ("none", "exact", "er"):
        tc = time.perf_counter()
        clusters = resolve_aliases(alias_nodes, method)
        ordinal_of, ord2canon = build_clusters(canon, clusters, all_ids)
        n_clusters = len(set(ordinal_of.values()))
        index = EntityIndex.build(collapse_for_index(nodes2, texts2, ordinal_of), embedder, top_k=50)
        coll_nodes, coll_edges = collapse_for_store(nodes2, edges2, ordinal_of)
        store = ggn.PyStore()
        bulk_load(store, coll_nodes, coll_edges)
        slice_graph = store.as_of(_BIG, _BIG)
        stark_to_eid = {int(e["source_refs"][0]): e["entity_id"]
                        for e in slice_graph.entities() if e["source_refs"]}
        eid_to_stark = {v: kk for kk, v in stark_to_eid.items()}
        lines.append(f"\n[{method}] clusters={n_clusters} (of {len(alias_nodes)} aliases + "
                     f"{len(all_ids) - len(alias_nodes)} passthrough)  build={time.perf_counter() - tc:.1f}s "
                     f"peak_rss={_peak_rss_gb():.2f}GB")
        for arm in ("dense", "graph"):
            r = evaluate(index, slice_graph, stark_to_eid, eid_to_stark, queries, embedder,
                         arm=arm, id_map=ord2canon)
            rows.append((method, arm, r))
            lines.append(f"  [{method}/{arm}] hit@1={r['hit@1']:.3f} hit@5={r['hit@5']:.3f} "
                         f"recall@20={r['recall@20']:.3f} mrr={r['mrr']:.3f} "
                         f"lat_mean={r['latency_ms_mean']:.1f}ms")

    dr = {m: r["recall@20"] for (m, a, r) in rows if a == "dense"}
    gr = {m: r["recall@20"] for (m, a, r) in rows if a == "graph"}
    lines.append(f"\nDENSE recall@20:  fragmented={dr['none']:.3f}  adhoc={dr['exact']:.3f}  "
                 f"er={dr['er']:.3f}  clean=0.261   |   ER-adhoc={dr['er'] - dr['exact']:+.3f}")
    lines.append(f"GRAPH recall@20:  fragmented={gr['none']:.3f}  adhoc={gr['exact']:.3f}  "
                 f"er={gr['er']:.3f}  clean=0.213   |   ER-adhoc={gr['er'] - gr['exact']:+.3f}   "
                 f"ER-fragmented={gr['er'] - gr['none']:+.3f}")
    if bridge:
        lines.append("\nMOAT read (Case B, bridge-fragmented, answers INTACT): the moat lives on the "
                     "GRAPH arm (dense = control, should stay ~flat since answers aren't touched). "
                     "MOAT CONFIRMED iff graph ER-fragmented > 0 AND graph ER-adhoc > 0 -- ER re-merges "
                     "the severed bridge so the walk reaches the answer, where exact-match can't. If "
                     "graph stays flat across none/exact/er, the 1-hop walk doesn't route through the "
                     "fragmented bridge on these queries (structure not load-bearing here) -> honest "
                     "conclusion that vanilla STaRK can't stage the moat.")
    else:
        lines.append("\nMOAT read (Case A, answer-fragmented): CONFOUNDED -- equivalence scoring gives "
                     "dense k chances at the gold, so fragmentation HELPS (clean-fragmented "
                     f"={0.261 - dr['none']:+.3f}). Use Case B (bridge) instead.")
    text = "\n".join(lines)
    print(text, flush=True)
    os.makedirs("/cache/results", exist_ok=True)
    _suffix = "bridge" if bridge else "inject"
    pathlib.Path(f"/cache/results/stark_{kb}_{_suffix}.md").write_text(text)
    cache.commit()
    return text


@app.function(image=image, gpu="A10G", volumes={"/cache": cache}, timeout=10800, memory=65536)
def run_stark(kb: str = "prime", sample: int = 200, embed_model: str = "nomic-embed-text",
              chunk_edges: int = 0, text_mode: str = "names",
              inject: bool = False, k: int = 3, seed: int = 0,
              bridge: bool = False, bridge_cap: int = 8) -> str:
    return _stark_impl(kb, sample, embed_model, chunk_edges, text_mode, inject, k, seed,
                       bridge, bridge_cap)


@app.local_entrypoint()
def main(kb: str = "prime", sample: int = 200, embed_model: str = "nomic-embed-text",
         chunk_edges: int = 0, text_mode: str = "names", inject: bool = False,
         k: int = 3, seed: int = 0, bridge: bool = False, bridge_cap: int = 8,
         spawn: bool = False) -> None:
    tag = ("bridge" if bridge else "inject") if inject else text_mode
    if spawn:
        call = run_stark.spawn(kb=kb, sample=sample, embed_model=embed_model,
                               chunk_edges=chunk_edges, text_mode=text_mode, inject=inject, k=k,
                               seed=seed, bridge=bridge, bridge_cap=bridge_cap)
        print(f"SPAWNED call_id={call.object_id} -> results/stark_{kb}_{tag}.md on gg-bench-cache")
        return
    print("\n===== RESULT =====\n" + run_stark.remote(
        kb=kb, sample=sample, embed_model=embed_model, chunk_edges=chunk_edges,
        text_mode=text_mode, inject=inject, k=k, seed=seed, bridge=bridge, bridge_cap=bridge_cap))
