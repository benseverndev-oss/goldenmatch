"""Modal app wrapping the ER-matcher trainer (plan §Phase 3, [gpu]).

Runs OUT-OF-BAND on Modal serverless GPU -- NOT in CI, NOT in the dev sandbox.
Credentials come from a **named Modal Secret** (`modal.Secret.from_name(...)`),
NEVER a pasted token (spec §1 -- a leaked token must be rotated and stored as a
Modal secret, not embedded here).

Prereqs (you, once):
  modal token new                     # authenticate the Modal CLI
  modal secret create er-matcher-hf HF_TOKEN=<your rotated HF token>

Usage:
  # P3a smoke run (GO/NO-GO calibration; cheapest adequate GPU first):
  modal run scripts/er_matcher/modal_train.py --smoke
  #   -> writes smoke_metrics.json to the `er-matcher-out` volume; then locally:
  #   python scripts/er_matcher/perf_report.py --metrics smoke_metrics.json \
  #       --total-steps <N> --gpu-cost-per-hour-usd <rate>
  # P3b full run (only after the P3a gate says GO):
  modal run scripts/er_matcher/modal_train.py

The data (data/er_matcher/*.jsonl from gen_pairs.py) is uploaded from the local
working copy; the merged model + smoke metrics land on a persisted Modal volume
you download with `modal volume get er-matcher-out ...`.

This file is imported by NOTHING (no tests, no CPU path) -- it's executed only by
`modal run`, so the top-level `import modal` is intentional and safe.
"""
from __future__ import annotations

import modal  # noqa: I001 -- only ever run via `modal run`, never imported elsewhere

APP_NAME = "goldenmatch-er-matcher-train"
GPU_SMOKE = "A10G"     # cheapest adequate; the smoke run confirms/updates the tier
GPU_SWEEP = "A100-40GB"  # the benchmark sweep runs here: a 3B model is slow on A10G
                         # (~7.2 s/step); A100 is ~2.5x faster AND ~same cost (per-second
                         # billing), so the ~40-min benchmark is faster + cheaper than A10G.
                         # Full run also uses A100-40GB so step-time extrapolation is
                         # consistent; the cheapest-tier-that-fits is reported as advisory.
GPU_FULL = "A100-40GB"  # right-sized from the P3a peak-mem measurement

# Pin the training stack; flash-attn built against the torch/CUDA in the image.
_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.4.0",
        "transformers==4.44.2",
        "peft==0.12.0",
        "trl==0.9.6",
        "datasets==2.21.0",
        "accelerate==0.33.0",
        "bitsandbytes==0.43.3",
        "pyyaml",
        "huggingface_hub",
        "rich",   # trl 0.9.6's SFTTrainer imports rich.console but doesn't pin it as a hard dep
        "pynvml",  # torch.cuda.utilization() needs it -> the P3a gate's GPU-util signal
    )
    # flash-attn: install the PREBUILT wheel matching the pinned stack
    # (torch 2.4 / cu12x / cp311 / cxx11abiFALSE -- PyPI torch wheels are abiFALSE).
    # The sdist build is avoided on purpose: its setup.py runs `git submodule ...`
    # (no git in debian_slim) and would then need the full CUDA toolkit to compile.
    .pip_install(
        "flash-attn @ https://github.com/Dao-AILab/flash-attention/releases/download/"
        "v2.6.3/flash_attn-2.6.3+cu123torch2.4cxx11abiFALSE-cp311-cp311-linux_x86_64.whl"
    )
    # reduce CUDA allocator fragmentation (recommended by the OOM error itself).
    # MUST precede add_local_* -- Modal forbids build steps after local-file adds.
    .env({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
    # the shared prompt contract + the trainer live in the repo; add the two dirs
    .add_local_dir(
        "packages/python/goldenmatch/goldenmatch/core/er_matcher",
        remote_path="/root/goldenmatch/core/er_matcher",
    )
    .add_local_dir("scripts/er_matcher", remote_path="/root/er_matcher")
    .add_local_dir("data/er_matcher", remote_path="/root/data/er_matcher")
)

app = modal.App(APP_NAME)
_out_vol = modal.Volume.from_name("er-matcher-out", create_if_missing=True)


@app.function(
    image=_image,
    gpu=GPU_SMOKE,
    timeout=60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],  # exposes HF_TOKEN in the env
)
def train_smoke(smoke_steps: int = 200, smoke_rows: int = 4000) -> None:
    _run(["--smoke", "--smoke-steps", str(smoke_steps), "--smoke-rows", str(smoke_rows),
          "--metrics-out", "/out/smoke_metrics.json", "--out-dir", "/out/smoke"])


@app.function(
    image=_image,
    gpu=GPU_SWEEP,
    timeout=2 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def train_sweep(qlora: bool) -> None:
    """Smoke-scale sweep for one quantization variant (config-matrix benchmark)."""
    name = "qlora-4bit" if qlora else "bf16-lora"
    _run([
        "--sweep",
        "--qlora-4bit" if qlora else "--no-qlora-4bit",
        "--smoke-steps", "200",
        "--smoke-rows", "4000",
        "--metrics-out", f"/out/sweep_metrics_{name}.json",
        "--out-dir", "/out/sweep",
    ])


@app.function(
    image=_image,
    gpu=GPU_FULL,
    timeout=6 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def train_full(qlora: bool = False) -> None:
    _run([
        "--qlora-4bit" if qlora else "--no-qlora-4bit",
        "--out-dir", "/out/model",
    ])


def _run(extra_argv: list[str]) -> None:
    """Invoke the trainer inside the GPU container against the mounted repo copy."""
    import sys

    # the shared prompt package + the trainer scripts are mounted here
    sys.path.insert(0, "/root")          # so `import goldenmatch.core.er_matcher.prompt` resolves
    sys.path.insert(0, "/root/er_matcher")
    import train  # the mounted scripts/er_matcher/train.py

    rc = train.main([
        "--config", "/root/er_matcher/config.yaml",
        "--data-dir", "/root/data/er_matcher",
        *extra_argv,
    ])
    _out_vol.commit()
    if rc != 0:
        raise SystemExit(rc)


@app.local_entrypoint()
def main(smoke: bool = False, smoke_steps: int = 200, smoke_rows: int = 4000) -> None:
    if smoke:
        train_smoke.remote(smoke_steps=smoke_steps, smoke_rows=smoke_rows)
        print("smoke done -> `modal volume get er-matcher-out smoke_metrics.json`, "
              "then feed it to scripts/er_matcher/perf_report.py")
    else:
        train_full.remote()
        print("full run done -> `modal volume get er-matcher-out model/merged` "
              "(quantize + publish per plan §Phase 4)")


@app.local_entrypoint()
def benchmark() -> None:
    """Run the bf16-lora vs qlora-4bit smoke sweep for both config-matrix variants.

    `config_matrix` runs locally (this entrypoint executes on the laptop, not
    in the container), so put the script dir on sys.path before importing it.
    """
    import os
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    import config_matrix

    # spawn() (non-blocking) so BOTH variants run in PARALLEL and both survive
    # `--detach` after the local orchestrator disconnects -- unlike blocking
    # remote() calls, which run sequentially and leave the not-yet-triggered
    # variant unlaunched if the parent process dies mid-sweep.
    handles = []
    for name, cfg in config_matrix.expand_configs({"qlora_4bit": False}):
        print(f"spawning sweep: {name}")
        handles.append(train_sweep.spawn(qlora=cfg["qlora_4bit"]))

    print(f"both sweeps spawned (parallel, detached): {[h.object_id for h in handles]}")
    print("when both finish -> `modal volume get er-matcher-out 'sweep_metrics_*.json' <dir>`"
          " -> `python scripts/er_matcher/run_benchmark.py --sweep-dir <dir>`")


@app.local_entrypoint()
def full(config_name: str = "bf16-lora", gpu: str = GPU_FULL) -> None:
    """Full training run, targeting a human-picked GPU tier for the chosen config.

    `gpu=...` is fixed at `@app.function` decoration time, so retargeting the
    tier at run time goes through `.with_options(gpu=...)` instead of a
    function argument.
    """
    qlora = config_name == "qlora-4bit"
    train_full.with_options(gpu=gpu).remote(qlora=qlora)
    print("full run done -> `modal volume get er-matcher-out model/merged` "
          "(quantize + publish per plan §Phase 4)")
