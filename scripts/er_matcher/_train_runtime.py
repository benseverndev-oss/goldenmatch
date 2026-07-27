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

import json
import time
from pathlib import Path
from typing import Any

# Imported at module load, but the MODULE itself is only imported inside
# train.main() -- so a CPU box importing train.py never pulls these in.
import torch  # type: ignore[import-not-found]
from datasets import Dataset  # type: ignore[import-not-found]
from peft import LoraConfig  # type: ignore[import-not-found]
from train import (  # sibling module (script dir on sys.path)
    TrainConfig,
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
        self.steps = 0
        self.util_samples: list[float] = []

    def on_train_begin(self, args, state, control, **kw):  # noqa: ANN001
        self.t0 = time.perf_counter()

    def on_step_end(self, args, state, control, **kw):  # noqa: ANN001
        self.steps += 1
        if torch.cuda.is_available():
            try:
                self.util_samples.append(torch.cuda.utilization() / 100.0)
            except (RuntimeError, AttributeError):
                # GPU utilization is OPTIONAL telemetry (needs NVML/pynvml, absent
                # on some drivers/older torch). A sampling failure must never
                # interrupt training -- skip this step's sample; the mean is over
                # whatever samples we did collect (0 -> gate reads it as data-bound).
                return

    def wall_s(self) -> float:
        return (time.perf_counter() - self.t0) if self.t0 else 0.0

    def mean_util(self) -> float:
        return sum(self.util_samples) / len(self.util_samples) if self.util_samples else 0.0


def _load_split(data_dir: Path, name: str) -> list[dict[str, Any]]:
    p = data_dir / f"{name}.jsonl"
    return read_jsonl(p) if p.exists() else []


def run_training(cfg: TrainConfig, args: Any) -> int:
    torch.manual_seed(cfg.seed)
    tok = AutoTokenizer.from_pretrained(cfg.base_model, revision=cfg.base_revision)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    train_rows = _load_split(args.data_dir, "train")
    val_rows = _load_split(args.data_dir, "val")
    if not train_rows:
        raise SystemExit(f"no train.jsonl under {args.data_dir} (run gen_pairs.py first)")

    if args.smoke:
        train_rows = train_rows[: args.smoke_rows]
        val_rows = val_rows[: max(1, args.smoke_rows // 10)]

    # measured max_seq_len over the REAL tokenizer (the top memory/speed lever)
    lengths = serialized_token_lengths(train_rows, lambda t: tok(t)["input_ids"])
    seq_len = measured_max_seq_len(
        lengths, percentile=cfg.seq_len_percentile,
        cap=cfg.seq_len_cap, multiple_of=cfg.seq_len_multiple_of,
    )
    print(f"[train] measured max_seq_len={seq_len} (P{cfg.seq_len_percentile} of "
          f"{len(lengths)} pairs, cap {cfg.seq_len_cap})")

    def to_text(rows: list[dict[str, Any]]) -> Dataset:
        recs = [{"messages": example_to_messages(r, cfg)} for r in rows]
        return Dataset.from_list(recs)

    train_ds, val_ds = to_text(train_rows), to_text(val_rows) if val_rows else None

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
    trainer.train()

    if args.smoke:
        _emit_smoke_metrics(cfg, args, cb, seq_len, train_ds)
        return 0

    # full run: save the merged fp16 model (registry ships the quantized GGUF of this)
    merged_dir = args.out_dir / "merged"
    trainer.model.merge_and_unload().save_pretrained(str(merged_dir))
    tok.save_pretrained(str(merged_dir))
    print(f"[train] merged model -> {merged_dir}")
    return 0


def _emit_smoke_metrics(cfg: TrainConfig, args: Any, cb: _ThroughputCallback,
                        seq_len: int, train_ds: Any) -> None:
    wall = cb.wall_s()
    toks = cb.steps * cfg.per_device_batch * cfg.grad_accum * seq_len
    peak_gb = (torch.cuda.max_memory_allocated() / 1e9) if torch.cuda.is_available() else None
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
        # learning_curve is filled by the sweep driver (10/25/50/100% slices);
        # a single smoke run reports one point -- the driver aggregates.
        "learning_curve": [],
        "note": "feed to perf_report.py with --total-steps/--gpu-cost-per-hour-usd for the go/no-go gate",
    }
    out = args.metrics_out or (args.out_dir / "smoke_metrics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(metrics, indent=2))
    print(f"[train] smoke metrics -> {out}\n{json.dumps(metrics, indent=2)}")
