#!/usr/bin/env python3
"""P3 LoRA SFT trainer for the OSS ER-matcher (Qwen2.5-1.5B-Instruct, Apache-2.0).

PERF-GATED (plan §Phase 3): built optimized + instrumented from the start so the
cheap smoke run (``--smoke``) measures the real workload BEFORE the multi-hour
full run. Optimizations baked in for short-sequence ER SFT:
  - sequence packing (no per-example pad-to-max) -- the top lever;
  - ``max_seq_len`` = measured P95 of serialized pairs (NOT a 2k/4k default);
  - bf16 + FlashAttention-2 (QLoRA-4bit only if memory-bound);
  - pre-tokenize once + length-grouped batching + enough dataloader workers ->
    GPU-bound (>90% util), which the P3a gate (perf_report.py) checks;
  - instrumentation: tokens/s, samples/s, GPU util, step time, peak mem ->
    ``smoke_metrics.json`` for the go/no-go gate.

The heavy deps (torch / transformers / peft / trl / datasets) are imported LAZILY
inside ``main`` so this module imports on a CPU box with none of them installed --
the PURE helpers below (config load, seq-len stat, chat-target construction) are
unit-tested without a GPU (scripts/er_matcher/test_train_helpers.py). The actual
fine-tune runs out-of-band on Modal (scripts/er_matcher/modal_train.py).
"""
from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Pure helpers reuse the SHARED runtime contract (one source of truth: the same
# serializer/rubric the data-gen and inference paths use) -- import is CPU-safe.
from goldenmatch.core.er_matcher.prompt import (
    SERIALIZER_VERSION,
    build_chat,
    render_target,
)

# Target confidences for the SFT labels (synthetic labels are categorical; the
# model learns to emit a probability, so we teach a confident-but-not-saturated
# target -- 1.0/0.0 would push logits to extremes and hurt calibration, which the
# P5 reliability curve checks). Overridable via config.
DEFAULT_MATCH_CONF = 0.9
DEFAULT_NOMATCH_CONF = 0.1


# --- config (pure) -----------------------------------------------------------
@dataclass
class TrainConfig:
    base_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    base_revision: str | None = None       # pin the base commit for reproducibility
    serializer_version: str = SERIALIZER_VERSION
    # LoRA
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_target_modules: list[str] = field(
        default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj",
                                 "gate_proj", "up_proj", "down_proj"]
    )
    # optimization
    learning_rate: float = 2e-4
    epochs: float = 2.0
    per_device_batch: int = 16
    grad_accum: int = 2
    warmup_ratio: float = 0.03
    weight_decay: float = 0.0
    lr_scheduler: str = "cosine"
    # sequence handling
    seq_len_percentile: float = 95.0       # measured P95 of serialized-pair tokens
    seq_len_cap: int = 1024                # hard ceiling; the P95 usually lands ~256-384
    seq_len_multiple_of: int = 64          # round the measured len up to this
    packing: bool = True
    group_by_length: bool = True
    # precision
    bf16: bool = True
    flash_attention_2: bool = True
    qlora_4bit: bool = False               # only if memory-bound (bf16-LoRA preferred)
    # data / repro
    match_confidence: float = DEFAULT_MATCH_CONF
    nomatch_confidence: float = DEFAULT_NOMATCH_CONF
    eval_fraction: float = 0.1             # held-out slice for early stop
    seed: int = 20260726
    dataloader_workers: int = 8

    def validate(self) -> None:
        if not 0 < self.seq_len_percentile <= 100:
            raise ValueError("seq_len_percentile must be in (0, 100]")
        if self.seq_len_cap <= 0 or self.seq_len_multiple_of <= 0:
            raise ValueError("seq_len_cap and seq_len_multiple_of must be > 0")
        if self.epochs <= 0:
            raise ValueError("epochs must be > 0")
        if not 0.0 <= self.eval_fraction < 1.0:
            raise ValueError("eval_fraction must be in [0, 1)")
        if self.serializer_version != SERIALIZER_VERSION:
            raise ValueError(
                f"config serializer_version {self.serializer_version!r} != runtime "
                f"{SERIALIZER_VERSION!r} -- the model must be trained on the runtime rendering"
            )
        if not 0.5 <= self.match_confidence <= 1.0:
            raise ValueError("match_confidence must be in [0.5, 1.0]")
        if not 0.0 <= self.nomatch_confidence <= 0.5:
            raise ValueError("nomatch_confidence must be in [0.0, 0.5]")


def load_config(path: Path) -> TrainConfig:
    """Load + validate config.yaml into a TrainConfig (unknown keys rejected so a
    typo can't silently no-op a hyperparameter)."""
    import yaml  # PyYAML is a light, always-present dep (config/loader.py uses it)

    raw = yaml.safe_load(path.read_text()) or {}
    known = set(TrainConfig().__dict__)
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)} (known: {sorted(known)})")
    cfg = TrainConfig(**raw)
    cfg.validate()
    return cfg


# --- sequence length (pure) --------------------------------------------------
def measured_max_seq_len(
    token_lengths: list[int], *, percentile: float, cap: int, multiple_of: int
) -> int:
    """The trained ``max_seq_len``: the Pth percentile of serialized-pair token
    lengths, rounded UP to ``multiple_of``, capped at ``cap`` (and floored at
    ``multiple_of``). Packing means over-length is rare and truncation there is
    acceptable; sizing to the P95 (not a 2k/4k default) is the top memory/speed
    lever for short ER pairs. Pure -- unit-tested."""
    if not token_lengths:
        return multiple_of
    ordered = sorted(token_lengths)
    # nearest-rank percentile (deterministic, no interpolation)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    p = ordered[min(rank, len(ordered)) - 1]
    rounded = int(math.ceil(p / multiple_of) * multiple_of)
    return max(multiple_of, min(rounded, cap))


# --- total-step estimate (pure) ------------------------------------------------
def estimate_total_steps(
    token_lengths: list[int], *, seq_len: int, per_device_batch: int,
    grad_accum: int, epochs: float,
) -> int:
    """Measured (packing-aware) total optimizer-step estimate for ``epochs``
    over ``token_lengths`` (the SAME per-row serialized-pair token lengths fed
    to ``measured_max_seq_len``).

    NOT a rows/batch calculation -- packing concatenates variable-length rows
    into fixed-``seq_len`` packed sequences, so the number of packed sequences
    (and thus steps) is ``sum(token_lengths) / seq_len``, not ``len(rows) /
    batch``. Deliberately avoids depending on trl's packed dataloader object
    (``SFTTrainer.get_train_dataloader()`` raises ``TypeError`` on a packed
    run -- it's a lengthless ``IterableDataset`` -- so this stays a pure,
    torch-free estimate computable straight from the token-length list).
    Floors both the packed-sequence count and steps-per-epoch at 1 so a tiny
    slice/corpus never estimates zero steps. Pure -- unit-tested."""
    packed = max(1, math.ceil(sum(token_lengths) / seq_len))
    steps_per_epoch = max(1, math.ceil(packed / (per_device_batch * grad_accum)))
    return int(steps_per_epoch * epochs)


# --- chat-target construction (pure) -----------------------------------------
def example_to_messages(row: dict[str, Any], cfg: TrainConfig) -> list[dict[str, str]]:
    """Turn a gen_pairs JSONL row ({a, b, label}) into the full chat sequence:
    the shared system+user prompt (build_chat) + the assistant TARGET verdict.

    The target is the exact JSON the model must emit at inference (render_target),
    so training and serving share one contract. Confidence prefers the row's own
    ``confidence`` (the FS-score-driven soft target some corpora carry) and falls
    back to ``cfg.match_confidence``/``cfg.nomatch_confidence`` only when the row
    has none -- an honest-yardstick label beats the fixed 0.9/0.1 constant.
    ``reason`` is a compact, deterministic agreement summary (never-black-box; not
    free-form so the model can't learn to hallucinate prose). Round-trips through
    parse_verdict by construction (asserted in tests)."""
    a, b, label = row["a"], row["b"], row["label"]
    match = label == "match"
    default_conf = cfg.match_confidence if match else cfg.nomatch_confidence
    conf = row.get("confidence", default_conf)
    reason = _auto_reason(a, b, match)
    return [*build_chat(a, b), {"role": "assistant", "content": render_target(match, conf, reason)}]


def _auto_reason(a: dict[str, Any], b: dict[str, Any], match: bool) -> str:
    """A short, deterministic reason string from field agreement -- lists up to 3
    shared keys that agree (match) or the strongest conflict (no_match). Kept terse
    so the SFT target teaches WHICH evidence drove the verdict without free prose."""
    shared = sorted(set(a) & set(b))
    agree = [k for k in shared if _norm(a.get(k)) and _norm(a.get(k)) == _norm(b.get(k))]
    conflict = [k for k in shared if _norm(a.get(k)) and _norm(b.get(k)) and _norm(a.get(k)) != _norm(b.get(k))]
    if match:
        if agree:
            return "agree on " + ", ".join(agree[:3])
        return "strong overall similarity"
    if conflict:
        return "conflict on " + ", ".join(conflict[:3])
    return "insufficient shared identifying evidence"


def _norm(v: Any) -> str:
    return "" if v is None else str(v).strip().lower()


def serialized_token_lengths(rows: Iterable[dict[str, Any]], tokenize_messages: Any) -> list[int]:
    """Token length of each training example under the SAME tokenization the
    trainer uses -- the chat-templated (system+user+target) message sequence.

    ``tokenize_messages(messages) -> Sequence`` is injected: the GPU caller passes
    ``lambda msgs: tokenizer.apply_chat_template(msgs, tokenize=True)`` so the
    measured P95 includes the per-turn special tokens (``<|im_start|>`` etc.) --
    a naive ``"\\n".join(content)`` omits them and under-measures max_seq_len,
    making truncation more common than intended. Injecting the tokenizer keeps
    this testable with a stub (no transformers)."""
    return [len(tokenize_messages(example_to_messages(row, TrainConfig()))) for row in rows]


# --- IO ----------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- CLI overrides (pure) ------------------------------------------------------
def apply_overrides(cfg: TrainConfig, args: Any) -> TrainConfig:
    """Apply CLI overrides onto a loaded ``TrainConfig``, re-validating if
    anything changed. Currently just ``--qlora-4bit/--no-qlora-4bit``
    (``args.qlora_4bit`` is ``None`` when neither flag was passed, meaning
    "use config.yaml's value" -- only an explicit True/False overrides it).
    Pure, box-safe -- unit-tested without a GPU (test_train_qlora_override.py)."""
    if args.qlora_4bit is not None:
        cfg.qlora_4bit = args.qlora_4bit
        cfg.validate()
    return cfg


# --- training entrypoint (lazy heavy deps) -----------------------------------
def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LoRA SFT the OSS ER-matcher (Qwen2.5-1.5B, Apache-2.0).")
    ap.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    ap.add_argument("--data-dir", type=Path, default=Path("data/er_matcher"))
    ap.add_argument("--out-dir", type=Path, default=Path("data/er_matcher/model"))
    ap.add_argument("--smoke", action="store_true",
                    help="P3a: few-hundred steps on a small slice; emit smoke_metrics.json")
    ap.add_argument("--smoke-steps", type=int, default=200)
    ap.add_argument("--smoke-rows", type=int, default=4000)
    ap.add_argument("--metrics-out", type=Path, default=None)
    ap.add_argument("--sweep", action="store_true",
                    help="SP2 Task 6: run the learning-curve sweep (10/25/50/100%% data "
                         "slices, args.smoke_steps each) instead of a single training run")
    ap.add_argument("--qlora-4bit", action=argparse.BooleanOptionalAction, default=None,
                    help="override config.yaml's qlora_4bit (--qlora-4bit / --no-qlora-4bit); "
                         "default None = use config.yaml's value")
    args = ap.parse_args(list(argv) if argv is not None else None)

    cfg = load_config(args.config)
    cfg = apply_overrides(cfg, args)
    # Heavy deps live in the sibling runtime module, imported here ONLY -- CPU
    # import of THIS module (for the pure helpers + tests) stays clean. The
    # script dir is on sys.path when run as `python scripts/er_matcher/train.py`
    # or from modal_train.py's mounted copy.
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _train_runtime import run_sweep, run_training  # type: ignore[import-not-found]

    if args.sweep:
        return run_sweep(cfg, args)
    return run_training(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
