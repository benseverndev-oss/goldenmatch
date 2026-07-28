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

import os

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
)

# The training corpus (data/er_matcher/*.jsonl from gen_pairs.py) is consumed only by
# train_*/eval_model. zeroshot_eval fetches unseen benchmarks fresh into the volume, so
# the corpus need not exist just to build the shared image -- mount it only when present
# (lets the zero-shot path run from a fresh worktree with no local training data).
if os.path.isdir("data/er_matcher"):
    _image = _image.add_local_dir("data/er_matcher", remote_path="/root/data/er_matcher")

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


@app.function(
    image=_image,
    gpu=GPU_FULL,
    timeout=3 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def eval_model(limit: int = 0) -> None:
    """Load the merged model and score match-F1 on the held-out test split via
    generative inference (build_chat -> generate -> parse_verdict), using
    eval.run_eval for overall + per-domain P/R/F1 + calibration."""
    import json
    import sys

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    sys.path.insert(0, "/root")
    sys.path.insert(0, "/root/er_matcher")
    import eval as ev  # the mounted scripts/er_matcher/eval.py (pure metrics module)
    from goldenmatch.core.er_matcher.prompt import build_chat, parse_verdict

    mpath = "/out/model/merged"
    tok = AutoTokenizer.from_pretrained(mpath)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = (
        AutoModelForCausalLM.from_pretrained(
            mpath, torch_dtype=torch.bfloat16, attn_implementation="flash_attention_2",
        )
        .cuda()
        .eval()
    )

    # run_eval scores each pair once for "overall" AND again per-domain -- memoize
    # so a slow generative matcher only does one generation per pair.
    cache: dict[tuple[int, int], dict | None] = {}

    def matcher(a: dict, b: dict) -> dict | None:
        key = (id(a), id(b))
        if key in cache:
            return cache[key]
        text = tok.apply_chat_template(build_chat(a, b), tokenize=False, add_generation_prompt=True)
        inputs = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=64, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        gen = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        v = parse_verdict(gen)
        cache[key] = v
        return v

    rows = [json.loads(ln) for ln in open("/root/data/er_matcher/test.jsonl") if ln.strip()]
    if limit:
        rows = rows[:limit]
    print(f"[eval] scoring {len(rows)} test pairs (memoized generative matcher)...")
    scorecard = ev.run_eval({"test": rows}, matcher)
    with open("/out/eval_results.json", "w") as f:
        json.dump(scorecard, f, indent=2)
    _out_vol.commit()
    print("[eval] overall:", json.dumps(scorecard["splits"]["test"]["overall"], indent=2))


@app.local_entrypoint()
def evaluate(limit: int = 0) -> None:
    eval_model.remote(limit=limit)
    print("eval done -> `modal volume get er-matcher-out eval_results.json`")


@app.function(
    image=_image,
    gpu=GPU_FULL,
    timeout=3 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def zeroshot_eval(dataset: str = "walmart_amazon", allow_fetch: bool = True, limit: int = 0) -> None:
    """Zero-shot P(match) via teacher-forced next-token logits (SP3 Task 5).

    Unlike `eval_model` (which generates full JSON and parses it), this scores
    each pair by teacher-forcing the exact JSON prefix the SFT target starts
    with and reading off P(true) vs P(false) at the next token -- a single
    forward pass per pair, and a real-valued confidence usable for temperature
    calibration. Fits T on `val`, reports F1 (via eval.run_eval, the same
    machinery as the rest of the harness) + raw/calibrated ECE on `test`.
    """
    import json
    import os
    import sys
    from pathlib import Path

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    sys.path.insert(0, "/root")
    sys.path.insert(0, "/root/er_matcher")
    import calibration as calib  # the mounted scripts/er_matcher/calibration.py (pure)
    import eval as ev  # the mounted scripts/er_matcher/eval.py (pure metrics module)
    from goldenmatch.core.er_matcher.prompt import build_chat
    from sources.magellan import MagellanSource

    mpath = "/out/model/merged"
    tok = AutoTokenizer.from_pretrained(mpath)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = (
        AutoModelForCausalLM.from_pretrained(
            mpath, torch_dtype=torch.bfloat16, attn_implementation="flash_attention_2",
        )
        .cuda()
        .eval()
    )

    # Resolve the two value-token ids ONCE, before the loop. The SFT target
    # (prompt.render_target) emits COMPACT json.dumps(..., separators=(",", ":"))
    # -- confirmed by test_render_target_is_compact_json's literal
    # '{"match":true,...}' -- so the token right after the teacher-forced
    # `{"match":` prefix has NO leading space (unlike json's default ": "
    # separator). Using " true"/" false" here would silently resolve the wrong
    # token ids and corrupt every P(match) this function produces.
    true_id_list = tok.encode("true", add_special_tokens=False)
    false_id_list = tok.encode("false", add_special_tokens=False)
    if len(true_id_list) != 1:
        raise RuntimeError(
            f"expected 'true' to tokenize to a single token id, got {true_id_list} "
            f"(decoded: {[tok.decode([t]) for t in true_id_list]!r}) -- the "
            "teacher-forced logit contract assumes a single-token value"
        )
    if len(false_id_list) != 1:
        raise RuntimeError(
            f"expected 'false' to tokenize to a single token id, got {false_id_list} "
            f"(decoded: {[tok.decode([t]) for t in false_id_list]!r}) -- the "
            "teacher-forced logit contract assumes a single-token value"
        )
    true_id = true_id_list[0]
    false_id = false_id_list[0]

    if allow_fetch:
        os.environ["GOLDENMATCH_ALLOW_FETCH"] = "1"
    root = Path(f"/out/magellan/{dataset}")
    splits = MagellanSource(dataset, root).splits()
    _out_vol.commit()  # persist the freshly-fetched dataset on the volume

    # run_eval scores each test pair once for "overall" AND again per-domain;
    # the val-split temperature fit also revisits pairs -- memoize the single
    # forward pass per (a, b) like eval_model's generative cache.
    cache: dict[tuple[int, int], tuple[float, float]] = {}

    def score(a: dict, b: dict) -> tuple[float, float]:
        """Return (p_match, z) via one teacher-forced forward pass."""
        key = (id(a), id(b))
        if key in cache:
            return cache[key]
        text = (
            tok.apply_chat_template(build_chat(a, b), tokenize=False, add_generation_prompt=True)
            + '{"match":'
        )
        inputs = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model(**inputs)
        logits = out.logits[0, -1, :]
        pair = torch.softmax(torch.stack([logits[true_id], logits[false_id]]), dim=0)
        p_match = float(pair[0])
        z = calib.logit(p_match)
        cache[key] = (p_match, z)
        return p_match, z

    val_rows = splits["val"]
    if limit:
        val_rows = val_rows[:limit]
    z_list: list[float] = []
    val_labels: list[bool] = []
    for row in val_rows:
        _, z = score(row["a"], row["b"])
        z_list.append(z)
        val_labels.append(row["label"] == "match")
    T = calib.fit_temperature(z_list, val_labels)
    print(f"[zeroshot_eval] fitted temperature T={T:.4f} on {len(val_rows)} val pairs")

    test_rows = splits["test"]
    if limit:
        test_rows = test_rows[:limit]

    verdicts: dict[tuple[int, int], dict] = {}
    p_raw: list[float] = []
    p_cal: list[float] = []
    y: list[bool] = []
    true_probs: list[float] = []
    non_probs: list[float] = []
    for row in test_rows:
        a, b = row["a"], row["b"]
        p_match, z = score(a, b)
        match = p_match > 0.5
        confidence = p_match if match else 1.0 - p_match
        verdicts[(id(a), id(b))] = {"match": match, "confidence": confidence}
        p_raw.append(p_match)
        p_cal.append(calib.apply_temperature(z, T=T))
        is_match = row["label"] == "match"
        y.append(is_match)
        (true_probs if is_match else non_probs).append(p_match)

    def matcher(a: dict, b: dict) -> dict | None:
        # run_eval calls this with the SAME dict objects from test_rows, so
        # identity keys are stable -- avoids re-running the forward pass.
        return verdicts[(id(a), id(b))]

    print(f"[zeroshot_eval] scoring {len(test_rows)} test pairs ({dataset})...")
    scorecard_test = ev.run_eval({"test": test_rows}, matcher)
    f1 = scorecard_test["splits"]["test"]["overall"]["f1"]

    raw_ece = calib.ece_from_probs(p_raw, y)
    calibrated_ece = calib.ece_from_probs(p_cal, y)
    n = len(y)

    card = ev.build_zeroshot_scorecard(
        {dataset: {"f1": f1, "raw_ece": raw_ece, "calibrated_ece": calibrated_ece, "n": n}}
    )
    # build_zeroshot_scorecard's "gate" entry is a GateResult dataclass, not a
    # plain dict -- convert before json.dump.
    card_json = {
        name: {**row, "gate": {"passed": row["gate"].passed, "checks": row["gate"].checks}}
        for name, row in card.items()
    }

    # WATCH-ITEM (informational, does not gate): if the token-id resolution
    # above were wrong, this separation would collapse toward ~0.5/0.5.
    mean_true = sum(true_probs) / len(true_probs) if true_probs else 0.0
    mean_non = sum(non_probs) / len(non_probs) if non_probs else 0.0
    print(
        f"[zeroshot_eval] separation: mean P(match) true={mean_true:.4f} "
        f"non={mean_non:.4f} (must be true>non or token-id resolution is wrong)"
    )

    results = {
        "dataset": dataset,
        "f1": f1,
        "temperature": T,
        "raw_ece": raw_ece,
        "calibrated_ece": calibrated_ece,
        "n": n,
        "separation": {"mean_p_match_true": mean_true, "mean_p_match_non": mean_non},
        "scorecard": card_json,
    }
    with open("/out/zeroshot_eval_results.json", "w") as f:
        json.dump(results, f, indent=2)
    _out_vol.commit()
    print("[zeroshot_eval] scorecard:", json.dumps(card_json, indent=2))


@app.local_entrypoint()
def zeroshot(dataset: str = "walmart_amazon", limit: int = 0) -> None:
    zeroshot_eval.remote(dataset=dataset, limit=limit)
    print("zeroshot eval done -> `modal volume get er-matcher-out zeroshot_eval_results.json`")
