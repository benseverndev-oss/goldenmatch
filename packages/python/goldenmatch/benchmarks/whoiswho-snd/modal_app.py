"""Modal app: run the WhoIsWho SND benchmark on Modal's compute.

The na-v3 *valid* set (80 names / ~46k papers) fits a plain GH runner, but the
co-author signal is CPU-bound and the full v3 (train+test, ~1M papers) wants a
beefy box -- so the GH lane *drives* a Modal run rather than doing the work
itself (see .github/workflows/bench-whoiswho-snd.yml, runner=modal).

Modal has outbound internet, so the corpus downloads on the box (fetch.py hits
AMiner's LFS) and caches to a persistent Volume; results persist there too.

Local dev / dispatch:
    modal run benchmarks/whoiswho-snd/modal_app.py --engine relational --split valid
    modal run benchmarks/whoiswho-snd/modal_app.py --engine all --limit 20

Auth is via MODAL_TOKEN_ID / MODAL_TOKEN_SECRET (GH secrets in the workflow).
"""
from __future__ import annotations

import json
from pathlib import Path

import modal

_HARNESS_DIR = Path(__file__).parent

# published goldenmatch == the in-repo version (2.7.x); the harness only uses
# public surfaces + a few documented internals (core.graph_er, core.evaluate,
# plugins.registry, config.schemas) that ship in the wheel.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("goldenmatch>=2.7.0", "polars>=1.0", "numpy", "rapidfuzz", "pyarrow")
    .env({"GOLDENMATCH_NATIVE": "0", "POLARS_SKIP_CPU_CHECK": "1",
          "WHOISWHO_DATA_DIR": "/data"})
    .add_local_dir(str(_HARNESS_DIR), remote_path="/root/whoiswho-snd")
)

app = modal.App("goldenmatch-whoiswho-snd", image=image)

# persistent cache for the (large) corpus + result json
vol = modal.Volume.from_name("whoiswho-snd-data", create_if_missing=True)

_ENGINES = ["all_singletons", "all_one", "text_only", "coauthor_only", "relational"]


@app.function(volumes={"/data": vol}, timeout=3600, cpu=8.0, memory=32768)
def run_engine(engine: str, split: str = "valid", limit: int | None = None) -> dict:
    import sys

    sys.path.insert(0, "/root/whoiswho-snd")
    from run_snd import run  # noqa: E402

    res = run(split, engine, limit=limit, data_dir="/data")
    slim = {k: v for k, v in res.items() if k != "per_name"}
    # persist per-engine json on the volume for later download
    out = Path("/data") / "results" / f"{split}_{engine}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(slim, indent=2))
    vol.commit()
    return slim


@app.local_entrypoint()
def main(engine: str = "relational", split: str = "valid", limit: int = 0):
    """Run one engine (or ``all``) and print the Pairwise-F1 scoreboard."""
    lim = limit or None
    engines = _ENGINES if engine == "all" else [engine]

    # fan out across Modal containers (one per engine) when running `all`
    results = list(run_engine.starmap([(e, split, lim) for e in engines]))

    print(f"\n== WhoIsWho SND on {split} (Modal) ==")
    print(f"{'engine':16s} {'F1':>7s} {'P':>7s} {'R':>7s} {'wall_s':>8s}")
    for r in results:
        print(f"{r['engine']:16s} {r['pairwise_f1_macro']:7.4f} "
              f"{r['pairwise_precision_macro']:7.4f} {r['pairwise_recall_macro']:7.4f} "
              f"{r['wall_s']:8.1f}")
