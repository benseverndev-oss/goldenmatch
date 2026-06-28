"""Modal training harness for the goldengraph distilled extractor.

Design: docs/superpowers/specs/2026-06-28-goldengraph-distilled-extractor-design.md (Stage 3).
Modal = serverless GPU: the training runs in a `@app.function(gpu=...)`; no VM to manage.

AUTH (Modal creds live in Infisical, project a99885f0-c5af-4ae1-9dc8-255cc60aa129, env dev):
    # PowerShell -- pull the token pair and hand it to Modal, then run:
    $P = "a99885f0-c5af-4ae1-9dc8-255cc60aa129"
    $env:MODAL_TOKEN_ID     = (infisical.cmd secrets get MODAL_TOKEN_ID     --projectId $P --env dev --plain)
    $env:MODAL_TOKEN_SECRET = (infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId $P --env dev --plain)
    modal run scripts/distill/modal_train.py::smoke              # validate auth + GPU image
    modal run scripts/distill/modal_train.py --student rebel \
        --data scripts/distill/data/dataset                      # train (after build_dataset.py)

STUDENTS (chosen by the A/B result; default = the design's fallback):
    rebel  (DEFAULT) -- seq2seq fine-tune of Babelscape/rebel-large on (text -> linearized triplets).
                        Tiny, CPU-inferable, plugs via `GOLDENGRAPH_EXTRACTOR=rebel`.
    lora             -- QLoRA on Qwen2.5-3B-Instruct emitting the extraction JSON; served via Ollama.

This file is the HARNESS (image / GPU / Volume / I/O / dispatch). The trainer bodies are stubbed
until the A/B fixes the student + the dataset is built -- fill them in then (they need GPU iteration).
"""
from __future__ import annotations

import modal

app = modal.App("goldengraph-distill")

# GPU image: torch + HF stack. peft/bitsandbytes/trl only matter for the LoRA path; harmless for rebel.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch",
        "transformers>=4.44",
        "datasets",
        "accelerate",
        "sentencepiece",
        "peft",
        "bitsandbytes",
        "trl",
    )
)

# Persistent Volume: the dataset is uploaded here, artifacts written back here.
vol = modal.Volume.from_name("gg-distill", create_if_missing=True)
DATA_DIR = "/data"


@app.function(image=image, gpu="A10G", volumes={DATA_DIR: vol}, timeout=600)
def smoke() -> dict:
    """Cheap connectivity check: confirms the image builds + a GPU is attached. Run this FIRST."""
    import torch

    info = {
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }
    print("modal smoke:", info)
    return info


@app.function(image=image, gpu="A10G", volumes={DATA_DIR: vol}, timeout=60 * 60 * 3)
def train(student: str = "rebel", *, epochs: int = 3, base_model: str | None = None) -> dict:
    """Fine-tune the chosen student on `/data/{train,val}.jsonl`; write the artifact to `/data/out/`
    and `vol.commit()`. Returns a manifest dict (student, base_model, metrics, artifact path)."""
    if student == "rebel":
        return _train_seq2seq(base_model or "Babelscape/rebel-large", epochs)
    if student == "lora":
        return _train_lora(base_model or "Qwen/Qwen2.5-3B-Instruct", epochs)
    raise ValueError(f"unknown student {student!r} (rebel|lora)")


def _train_seq2seq(base_model: str, epochs: int) -> dict:
    # TODO(impl, post-A/B): load /data/train.jsonl + /data/val.jsonl; linearize each record's triples
    # to REBEL's `<triplet> subj <subj> obj <predicate>` target string (see extract_local.parse_rebel_
    # triplets for the inverse format); HF Seq2SeqTrainer fine-tune from `base_model`; save to
    # /data/out/<run>/ ; vol.commit(); return {"student":"rebel","artifact":..., "val_loss":...}.
    raise NotImplementedError(
        "seq2seq trainer body -- fill in once the A/B picks the student and build_dataset.py has run"
    )


def _train_lora(base_model: str, epochs: int) -> dict:
    # TODO(impl, post-A/B): QLoRA via trl SFTTrainer on (extraction prompt -> extraction-json) pairs;
    # merge the adapter; export GGUF for Ollama (`ollama create`); write to /data/out/<run>/; commit.
    raise NotImplementedError(
        "LoRA trainer body -- fill in once the A/B picks the student and build_dataset.py has run"
    )


@app.local_entrypoint()
def main(student: str = "rebel", data: str = "scripts/distill/data/dataset", epochs: int = 3) -> None:
    """Upload the local dataset dir (train/val/heldout.jsonl) to the Volume, train on GPU, report."""
    import pathlib

    data_dir = pathlib.Path(data)
    files = sorted(data_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"no *.jsonl under {data_dir} -- run build_dataset.py first")
    with vol.batch_upload(force=True) as up:
        for f in files:
            up.put_file(str(f), f"/{f.name}")
    print(f"uploaded {len(files)} file(s) to the gg-distill Volume; training student={student} ...")
    manifest = train.remote(student, epochs=epochs)
    print("done:", manifest)
