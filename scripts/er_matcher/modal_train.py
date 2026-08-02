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
    gpu=GPU_FULL,
    timeout=3 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def train_truncated_eval(k: int = 16, walmart: str = "walmart_amazon", limit: int = 0) -> None:
    """TRUNCATE-AND-ADAPT (generative): take the BASE Qwen2.5-1.5B, keep only layers
    0..k-1, LoRA-SFT it on the SAME corpus + recipe as the full model, then eval
    IN-DISTRIBUTION (test.jsonl) + ZERO-SHOT CROSS-DOMAIN (held-out walmart). k=28
    reproduces the full model as the control, so the F1 delta vs a truncated k
    isolates the TRUNCATION effect (same base, data, recipe -- only depth differs).
    Answers: is the ~70% strippable depth (found in-distribution) GENERAL, or do the
    late layers do the cross-domain generalization?"""
    import json
    import sys
    from pathlib import Path

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    sys.path.insert(0, "/root")
    sys.path.insert(0, "/root/er_matcher")
    import eval as ev
    from _train_runtime import measured_max_seq_len
    from goldenmatch.core.er_matcher.prompt import build_chat
    from sources.magellan import MagellanSource
    from train import example_to_messages, load_config, read_jsonl, serialized_token_lengths

    cfg = load_config(Path("/root/er_matcher/config.yaml"))
    out_name = f"eval_trunc{k}.json"
    print(f"[trunc-sft] base={cfg.base_model} truncate_to_k={k}", flush=True)

    tok = AutoTokenizer.from_pretrained(cfg.base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        cfg.base_model, torch_dtype=torch.bfloat16, attn_implementation="flash_attention_2")
    n_full = model.config.num_hidden_layers
    if k < n_full:  # keep layers 0..k-1; readout (norm+lm_head) realigns via LoRA-SFT
        model.model.layers = model.model.layers[:k]
        model.config.num_hidden_layers = k
    model = model.cuda()

    train_rows = read_jsonl(Path("/root/data/er_matcher/train.jsonl"))
    seq_len = measured_max_seq_len(
        serialized_token_lengths(train_rows, lambda m: tok.apply_chat_template(m, tokenize=True)),
        percentile=cfg.seq_len_percentile, cap=cfg.seq_len_cap, multiple_of=cfg.seq_len_multiple_of)

    def to_ds(rows):
        return Dataset.from_list([{"messages": example_to_messages(r, cfg)} for r in rows])

    peft_cfg = LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=cfg.lora_target_modules, bias="none", task_type="CAUSAL_LM")
    sft = SFTConfig(
        output_dir=f"/out/model_trunc{k}", num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.per_device_batch, gradient_accumulation_steps=cfg.grad_accum,
        learning_rate=cfg.learning_rate, lr_scheduler_type=cfg.lr_scheduler,
        warmup_ratio=cfg.warmup_ratio, weight_decay=cfg.weight_decay, bf16=cfg.bf16,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        packing=cfg.packing, max_seq_length=seq_len, group_by_length=cfg.group_by_length,
        dataloader_num_workers=2, logging_steps=20, save_strategy="no", eval_strategy="no",
        report_to=[], seed=cfg.seed)
    trainer = SFTTrainer(model=model, args=sft, train_dataset=to_ds(train_rows),
                         tokenizer=tok, peft_config=peft_cfg)
    trainer.train()
    model = trainer.model.eval()

    # ---- eval: teacher-force {"match": and read true/false next-token logits ----
    tid = tok.encode("true", add_special_tokens=False)
    fid = tok.encode("false", add_special_tokens=False)
    assert len(tid) == 1 and len(fid) == 1, (tid, fid)
    true_id, false_id = tid[0], fid[0]

    def p_match(a: dict, b: dict) -> float:
        text = tok.apply_chat_template(build_chat(a, b), tokenize=False,
                                       add_generation_prompt=True) + '{"match":'
        inp = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            logits = model(**inp).logits[0, -1, :]
        pair = torch.softmax(torch.stack([logits[true_id], logits[false_id]]), 0)
        return float(pair[0])

    def f1_on(rows) -> dict:
        cache: dict = {}

        def matcher(a, b):
            key = (id(a), id(b))
            if key not in cache:
                pm = p_match(a, b)
                cache[key] = {"match": pm > 0.5, "confidence": pm if pm > 0.5 else 1 - pm}
            return cache[key]

        return ev.run_eval({"test": rows}, matcher)["splits"]["test"]["overall"]

    indist_rows = read_jsonl(Path("/root/data/er_matcher/test.jsonl"))
    if limit:
        indist_rows = indist_rows[:limit]
    indist = f1_on(indist_rows)
    print(f"[trunc-sft] k={k} IN-DIST test F1={indist['f1']:.4f}", flush=True)

    import os
    os.environ["GOLDENMATCH_ALLOW_FETCH"] = "1"
    splits = MagellanSource(walmart, Path(f"/out/magellan/{walmart}")).splits()
    _out_vol.commit()
    wal_test = splits["test"][:limit] if limit else splits["test"]
    wal = f1_on(wal_test)
    print(f"[trunc-sft] k={k} ZERO-SHOT {walmart} F1={wal['f1']:.4f} (full-model ref 0.795)",
          flush=True)

    result = {"k": k, "n_layers_full": n_full, "in_dist_f1": indist["f1"],
              "walmart_f1": wal["f1"], "walmart": walmart,
              "in_dist_overall": indist, "walmart_overall": wal}
    with open(f"/out/{out_name}", "w") as fh:
        json.dump(result, fh, indent=2)
    _out_vol.commit()
    print(f"[done] k={k} -> /out/{out_name}: in-dist {indist['f1']:.3f}, "
          f"walmart {wal['f1']:.3f}", flush=True)


@app.local_entrypoint()
def truncate_sft(ks: str = "28,16,12") -> None:
    """Spawn a truncate-and-adapt run per K (detached, parallel). k=28 = control."""
    handles = [train_truncated_eval.spawn(k=int(k)) for k in ks.split(",")]
    print(f"spawned {len(handles)} truncate-SFT runs: {[h.object_id for h in handles]}")
    print("poll: modal volume get er-matcher-out 'eval_trunc*.json' <dir>")


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
    fn = train_full
    if gpu != GPU_FULL:
        # runtime GPU retarget needs modal.Function.with_options (newer modal);
        # fall back to the decorator's GPU_FULL when it's unavailable (modal 1.4.x).
        if hasattr(fn, "with_options"):
            fn = fn.with_options(gpu=gpu)
        else:
            print(f"[warn] runtime GPU override to {gpu!r} needs modal.with_options "
                  f"(unavailable here); using decorator default {GPU_FULL!r}")
    fn.remote(qlora=qlora)
    print("full run done -> `modal volume get er-matcher-out model/merged` "
          "(quantize + publish per plan §Phase 4)")


@app.function(
    image=_image,
    gpu=GPU_FULL,
    timeout=3 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def eval_model(model_path: str = "/out/model/merged", out_name: str = "eval_results.json",
               limit: int = 0, fast: bool = True) -> None:
    """Load the merged model and score match-F1 on the held-out test split, using
    eval.run_eval for overall + per-domain P/R/F1 + calibration.

    model_path/out_name default to the 3B (/out/model/merged -> eval_results.json)
    but are overridable to score a scale variant without clobbering the 3B's card,
    e.g. --model-path /out/model_7b/merged --out-name eval_results_7b.json.

    fast=True (default): teacher-force the compact-JSON verdict prefix `{"match":`
    and read the next-token logits over the `true`/`false` ids (one forward pass per
    pair, ~15x faster than generation). fast=False: full generative inference
    (build_chat -> generate -> parse_verdict) for exact production-decode fidelity."""
    import json
    import sys

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    sys.path.insert(0, "/root")
    sys.path.insert(0, "/root/er_matcher")
    import eval as ev  # the mounted scripts/er_matcher/eval.py (pure metrics module)
    from goldenmatch.core.er_matcher.prompt import build_chat, parse_verdict

    mpath = model_path
    print(f"[eval] model={mpath} -> /out/{out_name}")
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
    # so the matcher only touches the model once per pair.
    cache: dict[tuple[int, int], dict | None] = {}

    if fast:
        # compact JSON (render_target uses separators=(",",":")) -> the value token
        # after `{"match":` is `true`/`false` with NO leading space.
        _tid = tok.encode("true", add_special_tokens=False)
        _fid = tok.encode("false", add_special_tokens=False)
        if len(_tid) != 1 or len(_fid) != 1:
            raise RuntimeError(f"true/false not single tokens: {_tid} {_fid}")
        true_id, false_id = _tid[0], _fid[0]

        def matcher(a: dict, b: dict) -> dict | None:
            key = (id(a), id(b))
            if key in cache:
                return cache[key]
            text = tok.apply_chat_template(
                build_chat(a, b), tokenize=False, add_generation_prompt=True
            ) + '{"match":'
            inputs = tok(text, return_tensors="pt").to(model.device)
            with torch.no_grad():
                logits = model(**inputs).logits[0, -1, :]
            pair = torch.softmax(torch.stack([logits[true_id], logits[false_id]]), dim=0)
            p_match = float(pair[0])
            match = p_match > 0.5
            v = {"match": match, "confidence": p_match if match else 1.0 - p_match}
            cache[key] = v
            return v
    else:
        def matcher(a: dict, b: dict) -> dict | None:
            key = (id(a), id(b))
            if key in cache:
                return cache[key]
            text = tok.apply_chat_template(
                build_chat(a, b), tokenize=False, add_generation_prompt=True
            )
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
    print(f"[eval] scoring {len(rows)} test pairs ({'fast logit' if fast else 'generative'} "
          "matcher)...")
    scorecard = ev.run_eval({"test": rows}, matcher)
    with open(f"/out/{out_name}", "w") as f:
        json.dump(scorecard, f, indent=2)
    _out_vol.commit()
    print("[eval] overall:", json.dumps(scorecard["splits"]["test"]["overall"], indent=2))


@app.local_entrypoint()
def evaluate(limit: int = 0, model_path: str = "/out/model/merged",
             out_name: str = "eval_results.json") -> None:
    eval_model.remote(model_path=model_path, out_name=out_name, limit=limit)
    print(f"eval done -> `modal volume get er-matcher-out {out_name}`")


@app.local_entrypoint()
def evaluate_detached(limit: int = 0, model_path: str = "/out/model/merged",
                      out_name: str = "eval_results.json") -> None:
    """Fire-and-forget in-distribution eval: spawn eval_model and return immediately.
    Run with `modal run --detach ...::evaluate_detached` so the spawned call survives
    the client exit (the generative scan of the full test split takes ~30-60 min).
    Poll `modal volume get er-matcher-out <out_name>` for the result."""
    call = eval_model.spawn(model_path=model_path, out_name=out_name, limit=limit)
    print(f"eval spawned (detached): {call.object_id} -> poll "
          f"`modal volume get er-matcher-out {out_name}`")


@app.function(
    image=_image,
    gpu=GPU_FULL,
    timeout=3 * 60 * 60,
    volumes={"/out": _out_vol},
    secrets=[modal.Secret.from_name("er-matcher-hf")],
)
def zeroshot_eval(dataset: str = "walmart_amazon", allow_fetch: bool = True, limit: int = 0,
                  model_path: str = "/out/model/merged",
                  out_name: str = "zeroshot_eval_results.json") -> None:
    """Zero-shot P(match) via teacher-forced next-token logits (SP3 Task 5).

    model_path/out_name default to the 3B but are overridable to score a scale
    variant on the same held-out benchmark without clobbering the 3B's card,
    e.g. --model-path /out/model_7b/merged --out-name zeroshot_7b_walmart.json.

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

    mpath = model_path
    print(f"[zeroshot_eval] model={mpath} dataset={dataset} -> /out/{out_name}")
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
    with open(f"/out/{out_name}", "w") as f:
        json.dump(results, f, indent=2)
    _out_vol.commit()
    print("[zeroshot_eval] scorecard:", json.dumps(card_json, indent=2))


@app.local_entrypoint()
def zeroshot(dataset: str = "walmart_amazon", limit: int = 0,
             model_path: str = "/out/model/merged",
             out_name: str = "zeroshot_eval_results.json") -> None:
    zeroshot_eval.remote(dataset=dataset, limit=limit, model_path=model_path, out_name=out_name)
    print(f"zeroshot eval done -> `modal volume get er-matcher-out {out_name}`")
