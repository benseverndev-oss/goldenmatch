"""Heavy-dependency training runtime for the ER-matcher (imported lazily by
train.py's ``main`` so the pure helpers + tests import on a CPU box with no
torch/trl installed).

This is the GPU code path -- it runs out-of-band on Modal (modal_train.py) or on
any CUDA box with ``pip install 'torch' transformers peft trl datasets accelerate
bitsandbytes flash-attn``. It is intentionally thin: all decisions
(config, measured seq_len, chat-target) come from train.py's pure, tested helpers;
this module only wires them to the SFTTrainer + emits the instrumentation the P3a
gate (perf_report.py) consumes.

NOT unit-tested here (needs a GPU); the logic it depends on IS tested in
test_train_helpers.py. Reviewer note: keep behavior-bearing decisions in train.py.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

# Imported at module load, but the MODULE itself is only imported inside
# train.main() -- so a CPU box importing train.py never pulls these in.
import sweep  # sibling module (script dir on sys.path) -- SP2 Task 5/6
import torch  # type: ignore[import-not-found]
from datasets import Dataset  # type: ignore[import-not-found]
from peft import (  # type: ignore[import-not-found]
    LoraConfig,
    get_peft_model,
    get_peft_model_state_dict,
    prepare_model_for_kbit_training,
    set_peft_model_state_dict,
)
from train import (  # sibling module (script dir on sys.path)
    TrainConfig,
    estimate_total_steps,
    example_to_messages,
    measured_max_seq_len,
    read_jsonl,
    serialized_token_lengths,
)
from transformers import (  # type: ignore[import-not-found]
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainerCallback,
)
from trl import SFTConfig, SFTTrainer  # type: ignore[import-not-found]


class _ThroughputCallback(TrainerCallback):
    """Accumulate tokens/s + step time + GPU util for the smoke scorecard."""

    def __init__(self, seq_len: int) -> None:
        self.seq_len = seq_len
        self.t0 = None
        self.t1 = None
        self.steps = 0
        self.util_samples: list[float] = []

    def on_train_begin(self, args, state, control, **kw):  # noqa: ANN001
        self.t0 = time.perf_counter()

    def on_train_end(self, args, state, control, **kw):  # noqa: ANN001
        # Snapshot the end of the training loop so wall_s() excludes the trailing
        # trainer.evaluate()/save that happen AFTER training (otherwise s_per_step
        # is inflated and the gate over-estimates the full-run cost).
        self.t1 = time.perf_counter()

    def on_step_end(self, args, state, control, **kw):  # noqa: ANN001
        self.steps += 1
        if torch.cuda.is_available():
            try:
                self.util_samples.append(torch.cuda.utilization() / 100.0)
            except (RuntimeError, AttributeError, ImportError):
                # GPU utilization is OPTIONAL telemetry. torch.cuda.utilization()
                # needs pynvml/NVML: it raises ModuleNotFoundError (a subclass of
                # ImportError) when pynvml is absent, and RuntimeError/AttributeError
                # on some drivers/older torch. A sampling failure must never
                # interrupt training -- skip this step's sample; the mean is over
                # whatever samples we did collect (0 -> gate reads it as data-bound).
                # (pynvml is installed in the training image, so this is the
                # graceful-degradation path, not the expected one.)
                return

    def wall_s(self) -> float:
        if not self.t0:
            return 0.0
        end = self.t1 if self.t1 is not None else time.perf_counter()
        return end - self.t0

    def mean_util(self) -> float | None:
        # None (not 0.0) when NO samples were collected: that means telemetry was
        # unavailable (pynvml absent), which perf_report reads as "unknown/advisory"
        # rather than "0% util = data-bound". 0.0 is reserved for a genuinely
        # measured all-idle run.
        if not self.util_samples:
            return None
        return sum(self.util_samples) / len(self.util_samples)


def _load_split(data_dir: Path, name: str) -> list[dict[str, Any]]:
    p = data_dir / f"{name}.jsonl"
    return read_jsonl(p) if p.exists() else []


def _build_model_and_tokenizer(cfg: TrainConfig) -> tuple[Any, Any]:
    """Load the tokenizer + base model per ``cfg`` (bf16/fp16, optional
    FlashAttention-2, optional QLoRA-4bit quantization). Shared by the
    single-run path (``run_training``) and the learning-curve sweep
    (``run_sweep``) so the (expensive) base-model load + the precision/
    quantization branch have exactly one source of truth."""
    tok = AutoTokenizer.from_pretrained(cfg.base_model, revision=cfg.base_revision)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model_kwargs: dict[str, Any] = {
        "torch_dtype": torch.bfloat16 if cfg.bf16 else torch.float16,
        "revision": cfg.base_revision,
    }
    if cfg.flash_attention_2:
        model_kwargs["attn_implementation"] = "flash_attention_2"
    if cfg.qlora_4bit:
        from transformers import BitsAndBytesConfig  # type: ignore[import-not-found]
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(cfg.base_model, **model_kwargs)
    return model, tok


def run_training(cfg: TrainConfig, args: Any) -> int:
    torch.manual_seed(cfg.seed)
    model, tok = _build_model_and_tokenizer(cfg)

    train_rows = _load_split(args.data_dir, "train")
    val_rows = _load_split(args.data_dir, "val")
    if not train_rows:
        raise SystemExit(f"no train.jsonl under {args.data_dir} (run gen_pairs.py first)")

    # Measure seq_len + total_steps over the FULL corpus BEFORE any smoke slice.
    # The gate extrapolates the FULL run's cost from the smoke, so total_steps must
    # be the full-corpus step count -- measuring it over the smoke slice (4k rows)
    # under-counts steps by the slice ratio and yields a falsely-cheap GO.
    # Tokenize the CHAT-TEMPLATED messages (what SFTTrainer actually tokenizes),
    # NOT a naive content join, so the P95 includes the per-turn special tokens.
    def _tok_msgs(msgs: list[dict[str, str]]):
        return tok.apply_chat_template(msgs, tokenize=True)

    lengths = serialized_token_lengths(train_rows, _tok_msgs)
    seq_len = measured_max_seq_len(
        lengths, percentile=cfg.seq_len_percentile,
        cap=cfg.seq_len_cap, multiple_of=cfg.seq_len_multiple_of,
    )
    print(f"[train] measured max_seq_len={seq_len} (P{cfg.seq_len_percentile} of "
          f"{len(lengths)} pairs, cap {cfg.seq_len_cap})")

    # Measured (packing-aware) total-step estimate for cfg.epochs over the FULL
    # corpus' token lengths -- NOT rows/batch (packing makes that wrong) and NOT
    # len(trainer.get_train_dataloader()) (packing=True builds a lengthless
    # IterableDataset -- that raises TypeError). Computed over the full corpus in
    # both --smoke and full mode so the emitted total_steps drives a correct
    # full-run cost extrapolation.
    total_steps = estimate_total_steps(
        lengths, seq_len=seq_len, per_device_batch=cfg.per_device_batch,
        grad_accum=cfg.grad_accum, epochs=cfg.epochs,
    )

    # Now (after measuring the full corpus) slice to the smoke rows for a cheap
    # calibration train -- the metrics above still describe the full run.
    if args.smoke:
        train_rows = train_rows[: args.smoke_rows]
        val_rows = val_rows[: max(1, args.smoke_rows // 10)]

    def to_text(rows: list[dict[str, Any]]) -> Dataset:
        recs = [{"messages": example_to_messages(r, cfg)} for r in rows]
        return Dataset.from_list(recs)

    train_ds, val_ds = to_text(train_rows), to_text(val_rows) if val_rows else None

    peft_cfg = LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=cfg.lora_target_modules, bias="none", task_type="CAUSAL_LM",
    )

    max_steps = args.smoke_steps if args.smoke else -1
    sft = SFTConfig(
        output_dir=str(args.out_dir),
        num_train_epochs=cfg.epochs,
        max_steps=max_steps,
        per_device_train_batch_size=cfg.per_device_batch,
        gradient_accumulation_steps=cfg.grad_accum,
        learning_rate=cfg.learning_rate,
        lr_scheduler_type=cfg.lr_scheduler,
        warmup_ratio=cfg.warmup_ratio,
        weight_decay=cfg.weight_decay,
        bf16=cfg.bf16,
        # Gradient checkpointing is the memory lever that makes a 3B LoRA SFT fit
        # (batch x seq activations dominate; the A10G smoke OOM'd without it).
        # use_reentrant=False is required for PEFT + checkpointing.
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        packing=cfg.packing,
        max_seq_length=seq_len,
        group_by_length=cfg.group_by_length,
        dataloader_num_workers=cfg.dataloader_workers,
        dataloader_pin_memory=True,
        logging_steps=10,
        save_strategy="steps" if not args.smoke else "no",
        save_steps=200,
        eval_strategy="steps" if (val_ds is not None and not args.smoke) else "no",
        eval_steps=200,
        report_to=[],
        seed=cfg.seed,
    )

    cb = _ThroughputCallback(seq_len)
    trainer = SFTTrainer(
        model=model, args=sft, train_dataset=train_ds,
        # trl 0.9.6 (pinned in modal_train.py) takes `tokenizer=`; `processing_class=`
        # is the newer trl/transformers rename and is not accepted here.
        eval_dataset=val_ds, tokenizer=tok, peft_config=peft_cfg,
        callbacks=[cb],
    )

    # Resume support: if a prior checkpoint exists under args.out_dir (e.g. the
    # full run was interrupted and re-launched against the same output volume),
    # continue from it instead of restarting -- guarded so a fresh out_dir with
    # no checkpoints behaves exactly as before (resume_from_checkpoint=None).
    resume_from_checkpoint = None
    if not args.smoke and any(Path(args.out_dir).glob("checkpoint-*")):
        resume_from_checkpoint = True
        print(f"[train] found checkpoint(s) under {args.out_dir} -- resuming")

    trainer.train(resume_from_checkpoint=resume_from_checkpoint)

    if args.smoke:
        _emit_smoke_metrics(cfg, args, cb, seq_len, total_steps)
        return 0

    # full run: save the merged fp16 model (registry ships the quantized GGUF of this)
    merged_dir = args.out_dir / "merged"
    trainer.model.merge_and_unload().save_pretrained(str(merged_dir))
    tok.save_pretrained(str(merged_dir))
    print(f"[train] merged model -> {merged_dir}")
    return 0


def _emit_smoke_metrics(cfg: TrainConfig, args: Any, cb: _ThroughputCallback,
                        seq_len: int, total_steps: int) -> None:
    wall = cb.wall_s()
    toks = cb.steps * cfg.per_device_batch * cfg.grad_accum * seq_len
    # Report RESERVED (not just allocated) peak: the caching allocator's reserved
    # pool is what actually presses against capacity, so the gate's memory check
    # (which derates capacity by a headroom factor) reasons about the real ceiling.
    peak_gb = (torch.cuda.max_memory_reserved() / 1e9) if torch.cuda.is_available() else None
    cap_gb = (torch.cuda.get_device_properties(0).total_memory / 1e9
              if torch.cuda.is_available() else None)
    metrics = {
        "gpu_util": cb.mean_util(),
        "peak_mem_gb": peak_gb,
        "gpu_capacity_gb": cap_gb,
        "smoke_steps": cb.steps,
        "smoke_wall_s": wall,
        "tokens_per_s": (toks / wall) if wall else 0.0,
        "seq_len": seq_len,
        # NOTE: key is "total_steps" -- perf_report.evaluate_perf_gate reads
        # metrics.get("total_steps", ...) directly (and the CLI's --total-steps
        # overrides it); this is the MEASURED (estimate_total_steps) count, so
        # the gate no longer needs --total-steps passed by hand.
        "total_steps": total_steps,
        # learning_curve is filled by the sweep driver (10/25/50/100% slices);
        # a single smoke run reports one point -- the driver aggregates.
        "learning_curve": [],
        "note": "total_steps is auto-supplied (measured); feed to perf_report.py with "
                "--gpu-cost-per-hour-usd for the go/no-go gate",
    }
    out = args.metrics_out or (args.out_dir / "smoke_metrics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(metrics, indent=2))
    print(f"[train] smoke metrics -> {out}\n{json.dumps(metrics, indent=2)}")


def run_sweep(cfg: TrainConfig, args: Any) -> int:
    """SP2 Task 6: the GPU-side learning-curve sweep.

    Loads the base model + tokenizer ONCE (the expensive part, shared via
    ``_build_model_and_tokenizer``), wraps it in a single LoRA adapter, then
    for each ``sweep.data_fractions()`` slice:
      1. resets the adapter to its just-initialized state (cheap -- only the
         small LoRA tensors, via ``get/set_peft_model_state_dict``) so every
         fraction trains from the SAME starting point -- isolating the effect
         of data volume rather than warm-starting each slice off the last;
      2. builds a fresh ``SFTTrainer`` against that slice's row prefix
         (``peft_config=None`` -- the model is already a ``PeftModel``, trl's
         documented "bring your own PEFT model" path, so it is used as-is and
         never re-wrapped);
      3. trains ``args.smoke_steps`` max steps and captures
         ``trainer.evaluate()["eval_loss"]``.

    The largest (100%) slice's throughput/memory telemetry and a MEASURED
    ``total_steps`` (the true full-corpus step count, from the SAME
    packing-aware ``estimate_total_steps`` used by ``run_training`` -- see its
    note) are emitted alongside the aggregated ``learning_curve`` -- same
    metrics shape as ``_emit_smoke_metrics`` so ``perf_report.py`` consumes
    either.
    """
    torch.manual_seed(cfg.seed)
    model, tok = _build_model_and_tokenizer(cfg)

    train_rows = _load_split(args.data_dir, "train")
    val_rows = _load_split(args.data_dir, "val")
    if not train_rows:
        raise SystemExit(f"no train.jsonl under {args.data_dir} (run gen_pairs.py first)")
    if not val_rows:
        raise SystemExit("--sweep needs val.jsonl (per-slice eval_loss) -- "
                          "run gen_pairs.py's split first")

    lengths = serialized_token_lengths(
        train_rows, lambda msgs: tok.apply_chat_template(msgs, tokenize=True))
    seq_len = measured_max_seq_len(
        lengths, percentile=cfg.seq_len_percentile,
        cap=cfg.seq_len_cap, multiple_of=cfg.seq_len_multiple_of,
    )
    print(f"[sweep] measured max_seq_len={seq_len} (P{cfg.seq_len_percentile} of "
          f"{len(lengths)} pairs, cap {cfg.seq_len_cap})")

    # Measured over the FULL, unsliced train_rows (the 100% slice) -- see the
    # note on run_training's own estimate_total_steps call.
    total_steps = estimate_total_steps(
        lengths, seq_len=seq_len, per_device_batch=cfg.per_device_batch,
        grad_accum=cfg.grad_accum, epochs=cfg.epochs,
    )

    def to_text(rows: list[dict[str, Any]]) -> Dataset:
        recs = [{"messages": example_to_messages(r, cfg)} for r in rows]
        return Dataset.from_list(recs)

    val_ds = to_text(val_rows)

    # Mirror what trl's own peft_config= path does in run_training (bypassed
    # here since the model is wrapped ourselves so the adapter can be reset
    # per-slice): enable_input_require_grads so grads flow through the frozen
    # base model's embeddings into the LoRA adapter under gradient
    # checkpointing, and (QLoRA only) cast/prep the quantized base for
    # k-bit training. Skipping either is silent -- training "succeeds" but
    # the adapter never learns (a flat learning curve, not a crash).
    model.enable_input_require_grads()
    if cfg.qlora_4bit:
        model = prepare_model_for_kbit_training(model)

    peft_cfg = LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=cfg.lora_target_modules, bias="none", task_type="CAUSAL_LM",
    )
    peft_model = get_peft_model(model, peft_cfg)
    # snapshot the FRESH (just-initialized, untrained) adapter weights once --
    # reset to this before each slice.
    initial_adapter_state = copy.deepcopy(get_peft_model_state_dict(peft_model))

    points: list[tuple[float, float]] = []
    last_cb: _ThroughputCallback | None = None
    for frac in sweep.data_fractions():
        set_peft_model_state_dict(peft_model, initial_adapter_state)

        n = sweep.slice_len(len(train_rows), frac)
        train_ds = to_text(train_rows[:n])

        sft = SFTConfig(
            output_dir=str(Path(args.out_dir) / f"sweep_{frac}"),
            num_train_epochs=cfg.epochs,
            max_steps=args.smoke_steps,
            per_device_train_batch_size=cfg.per_device_batch,
            gradient_accumulation_steps=cfg.grad_accum,
            learning_rate=cfg.learning_rate,
            lr_scheduler_type=cfg.lr_scheduler,
            warmup_ratio=cfg.warmup_ratio,
            weight_decay=cfg.weight_decay,
            bf16=cfg.bf16,
            gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            packing=cfg.packing,
            max_seq_length=seq_len,
            group_by_length=cfg.group_by_length,
            dataloader_num_workers=cfg.dataloader_workers,
            dataloader_pin_memory=True,
            logging_steps=10,
            save_strategy="no",
            # evaluated explicitly (below) after the short slice run rather than
            # on a step cadence -- each slice run is itself only smoke_steps long.
            eval_strategy="no",
            report_to=[],
            seed=cfg.seed,
        )
        cb = _ThroughputCallback(seq_len)
        trainer = SFTTrainer(
            model=peft_model, args=sft, train_dataset=train_ds,
            eval_dataset=val_ds, tokenizer=tok, peft_config=None,
            callbacks=[cb],
        )
        trainer.train()
        eval_loss = trainer.evaluate()["eval_loss"]
        points.append((frac, eval_loss))
        # per-fraction visibility: a spurious plateau (e.g. grads not flowing
        # through a mis-prepped QLoRA base) is diagnosable straight from logs.
        print(f"[sweep] frac={frac} rows={n} eval_loss={eval_loss:.4f}")
        last_cb = cb

    _emit_sweep_metrics(cfg, args, points, last_cb, seq_len, total_steps)
    return 0


def _emit_sweep_metrics(cfg: TrainConfig, args: Any, points: list[tuple[float, float]],
                        cb: _ThroughputCallback, seq_len: int, total_steps: int) -> None:
    wall = cb.wall_s()
    toks = cb.steps * cfg.per_device_batch * cfg.grad_accum * seq_len
    # Report RESERVED (not just allocated) peak: the caching allocator's reserved
    # pool is what actually presses against capacity, so the gate's memory check
    # (which derates capacity by a headroom factor) reasons about the real ceiling.
    peak_gb = (torch.cuda.max_memory_reserved() / 1e9) if torch.cuda.is_available() else None
    cap_gb = (torch.cuda.get_device_properties(0).total_memory / 1e9
              if torch.cuda.is_available() else None)
    metrics = {
        # gpu_util/peak_mem/tokens_per_s measured on the LARGEST (100%) slice.
        "gpu_util": cb.mean_util(),
        "peak_mem_gb": peak_gb,
        "gpu_capacity_gb": cap_gb,
        "smoke_steps": cb.steps,
        "smoke_wall_s": wall,
        "tokens_per_s": (toks / wall) if wall else 0.0,
        "seq_len": seq_len,
        # see the "total_steps" key note on _emit_smoke_metrics -- same
        # contract, auto-supplied to perf_report.evaluate_perf_gate.
        "total_steps": total_steps,
        "learning_curve": sweep.build_learning_curve(points),
        "note": "learning-curve sweep (10/25/50/100% slices); gpu_util/peak_mem_gb/"
                "tokens_per_s measured on the LARGEST (100%) slice; total_steps is "
                "auto-supplied (measured) -- feed to perf_report.py with "
                "--gpu-cost-per-hour-usd for the go/no-go gate",
    }
    out = args.metrics_out or (Path(args.out_dir) / "sweep_metrics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(metrics, indent=2))
    print(f"[sweep] metrics -> {out}\n{json.dumps(metrics, indent=2)}")
