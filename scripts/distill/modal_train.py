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

# GPU image: torch + HF stack, PINNED (can't iterate the trl/peft API locally -- no GPU).
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.5.1",
        "transformers==4.46.2",
        "datasets==3.1.0",
        "accelerate==1.1.1",
        "sentencepiece",
        "peft==0.13.2",
        "bitsandbytes==0.44.1",
        "trl==0.12.1",
    )
)

#: Extraction prompt -- MUST match goldengraph.extract (_PROMPT + the vocab/direction instruction the
#: schema-constrained serving path prepends). The student is trained on this exact prompt so the served
#: model sees in training what it sees at inference. Vocab is the engineered RELATION_SCHEMA.
_VOCAB = "works_at, located_in, acquired, authored, part_of"
_TRAIN_PROMPT = (
    "IMPORTANT: for every relationship, set `predicate` to EXACTLY ONE label, verbatim, from this "
    "closed set -- do NOT paraphrase, pluralize, or invent labels: [" + _VOCAB + "]. If none of these "
    "relations holds between two entities, OMIT that relationship. "
    "DIRECTION MATTERS: `subj` is the entity that the relation acts FROM (the grammatical subject, "
    "stated FIRST), `obj` is the entity it acts ON (stated second). For 'A works_at B', subj=A, "
    "obj=B. Never invert subject and object.\n\n"
    'You extract a knowledge graph from text. Return STRICT JSON only, no prose, in exactly this '
    'shape:\n{"entities": [{"name": "<surface name>", "type": "<coarse type>", "description": '
    '"<one short factual phrase describing the entity>"}], "relationships": [{"subj": <entity index>, '
    '"predicate": "<verb phrase>", "obj": <entity index>}]}\n`subj`/`obj` are 0-based indices into '
    "`entities`. Text:\n{text}"
)


def _completion_json(rec: dict) -> str:
    """The gold JSON the student should emit for a training record."""
    import json

    return json.dumps(
        {
            "entities": [
                {"name": e["name"], "type": e.get("type", "concept"),
                 "description": e.get("context", "")}
                for e in rec["entities"]
            ],
            "relationships": [
                {"subj": r["subj"], "predicate": r["predicate"], "obj": r["obj"]}
                for r in rec["relationships"]
            ],
        },
        ensure_ascii=False,
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


@app.function(image=image, gpu="A100", volumes={DATA_DIR: vol}, timeout=60 * 60 * 3)
def train(student: str = "lora", *, epochs: int = 3, base_model: str | None = None) -> dict:
    """Fine-tune the chosen student on `/data/{train,val}.jsonl`; write the artifact to `/data/out/`
    and `vol.commit()`. Returns a manifest dict (student, base_model, metrics, artifact path).
    Default student=lora on Qwen2.5-7B-Instruct -- the SAME base we serve, so it is a clean A/B."""
    if student == "rebel":
        return _train_seq2seq(base_model or "Babelscape/rebel-large", epochs)
    if student == "lora":
        return _train_lora(base_model or "Qwen/Qwen2.5-7B-Instruct", epochs)
    raise ValueError(f"unknown student {student!r} (rebel|lora)")


def _train_seq2seq(base_model: str, epochs: int) -> dict:
    # TODO(impl, post-A/B): load /data/train.jsonl + /data/val.jsonl; linearize each record's triples
    # to REBEL's `<triplet> subj <subj> obj <predicate>` target string (see extract_local.parse_rebel_
    # triplets for the inverse format); HF Seq2SeqTrainer fine-tune from `base_model`; save to
    # /data/out/<run>/ ; vol.commit(); return {"student":"rebel","artifact":..., "val_loss":...}.
    raise NotImplementedError(
        "seq2seq trainer body -- fill in once the A/B picks the student and build_dataset.py has run"
    )


def _read_jsonl(path):
    import json

    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _chat_text(tok, rec: dict) -> str:
    """One training example: user=extraction prompt over the text, assistant=gold JSON, rendered
    through the model's chat template (so the served Ollama prompt matches)."""
    msgs = [
        {"role": "user", "content": _TRAIN_PROMPT.replace("{text}", rec["text"])},
        {"role": "assistant", "content": _completion_json(rec)},
    ]
    return tok.apply_chat_template(msgs, tokenize=False)


def _eval_heldout(model, tok, heldout: list[dict]) -> dict:
    """Generate on the heldout set; score predicate-exact and DIRECTION (does the model put the
    canonical subject first?). Direction is scored separately on the reverse-phrased subset -- the
    examples where the text states the edge backwards -- because that is the lesson we are testing."""
    import json

    import torch

    model.eval()
    pred_ok = dir_ok = n = 0
    rev_total = rev_dir_ok = 0
    for rec in heldout:
        prompt = tok.apply_chat_template(
            [{"role": "user", "content": _TRAIN_PROMPT.replace("{text}", rec["text"])}],
            tokenize=False, add_generation_prompt=True,
        )
        ids = tok(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=256, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        text = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        gold_r = rec["relationships"][0]
        gold_pred = gold_r["predicate"]
        gold_subj = rec["entities"][gold_r["subj"]]["name"]
        gold_obj = rec["entities"][gold_r["obj"]]["name"]
        is_rev = rec["text"].split()[0] != gold_subj.split()[0]
        n += 1
        rev_total += int(is_rev)
        try:
            s = text[text.index("{"): text.rindex("}") + 1]
            data = json.loads(s)
            ents = [e.get("name", "") for e in data.get("entities", [])]
            r0 = data.get("relationships", [])[0]
            p_pred = str(r0.get("predicate", ""))
            p_subj = ents[r0["subj"]] if 0 <= r0.get("subj", -1) < len(ents) else ""
            p_obj = ents[r0["obj"]] if 0 <= r0.get("obj", -1) < len(ents) else ""
        except Exception:
            continue
        if p_pred == gold_pred:
            pred_ok += 1
        if p_subj == gold_subj and p_obj == gold_obj:
            dir_ok += 1
            if is_rev:
                rev_dir_ok += 1
    return {
        "n": n,
        "predicate_acc": round(pred_ok / max(n, 1), 4),
        "direction_acc": round(dir_ok / max(n, 1), 4),
        "reverse_direction_acc": round(rev_dir_ok / max(rev_total, 1), 4),
        "reverse_n": rev_total,
    }


def _train_lora(base_model: str, epochs: int) -> dict:
    """QLoRA fine-tune the student on (extraction prompt -> canonical JSON) pairs, then self-eval on
    the heldout split (predicate + direction accuracy). Saves the merged FP16 model to /data/out/ for
    later GGUF/Ollama export. The merge + serve path is a follow step gated on these metrics."""
    import os
    import time

    # cache HF model weights on the Volume so retries don't re-download the 7B (~15GB).
    os.environ.setdefault("HF_HOME", f"{DATA_DIR}/hf_cache")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import DataCollatorForCompletionOnlyLM, SFTConfig, SFTTrainer

    run = f"lora-{base_model.split('/')[-1]}-{int(time.time())}"
    out_dir = f"{DATA_DIR}/out/{run}"

    tok = AutoTokenizer.from_pretrained(base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    train = _read_jsonl(f"{DATA_DIR}/train.jsonl")
    val = _read_jsonl(f"{DATA_DIR}/val.jsonl")
    heldout = _read_jsonl(f"{DATA_DIR}/heldout.jsonl")
    train_ds = Dataset.from_list([{"text": _chat_text(tok, r)} for r in train])
    val_ds = Dataset.from_list([{"text": _chat_text(tok, r)} for r in val])

    bnb = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        base_model, quantization_config=bnb, device_map="auto", torch_dtype=torch.bfloat16,
    )
    model = prepare_model_for_kbit_training(model)
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    # completion-only loss: mask the prompt so the student is graded only on the JSON it must produce.
    collator = DataCollatorForCompletionOnlyLM(
        response_template="<|im_start|>assistant\n", tokenizer=tok,
    )
    cfg = SFTConfig(
        output_dir=out_dir, num_train_epochs=epochs, per_device_train_batch_size=8,
        gradient_accumulation_steps=2, learning_rate=2e-4, bf16=True, logging_steps=10,
        max_seq_length=1024, dataset_text_field="text", packing=False, report_to=[],
        save_strategy="no",
    )
    trainer = SFTTrainer(
        model=model, args=cfg, train_dataset=train_ds, eval_dataset=val_ds,
        peft_config=lora, data_collator=collator,
    )
    trainer.train()

    metrics = _eval_heldout(trainer.model, tok, heldout)
    print("heldout metrics:", metrics, flush=True)

    # merge adapter -> FP16 and persist for later GGUF/Ollama export.
    merged = trainer.model.merge_and_unload()
    merged.save_pretrained(f"{out_dir}/merged", safe_serialization=True)
    tok.save_pretrained(f"{out_dir}/merged")
    manifest = {"student": "lora", "base_model": base_model, "artifact": f"{out_dir}/merged",
                "metrics": metrics}
    # persist the manifest so a DETACHED/spawned run survives local-CLI death (pull with volume get).
    import json as _json
    import os as _os

    _os.makedirs(f"{DATA_DIR}/out", exist_ok=True)
    with open(f"{DATA_DIR}/out/manifest.json", "w", encoding="utf-8") as _f:
        _json.dump(manifest, _f)
    vol.commit()
    return manifest


@app.local_entrypoint()
def main(student: str = "lora", data: str = "scripts/distill/data/dataset", epochs: int = 3,
         spawn: bool = False) -> None:
    """Upload the local dataset dir (train/val/heldout.jsonl) to the Volume, train on GPU, report.
    `--spawn` (with `modal run --detach`) fires the train server-side and writes the manifest to the
    Volume at /out/manifest.json -- survives a local-CLI kill (pull with `modal volume get`)."""
    import json
    import pathlib

    data_dir = pathlib.Path(data)
    files = sorted(data_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"no *.jsonl under {data_dir} -- run build_dataset.py first")
    with vol.batch_upload(force=True) as up:
        for f in files:
            up.put_file(str(f), f"/{f.name}")
    print(f"uploaded {len(files)} file(s) to the gg-distill Volume; training student={student} ...")
    if spawn:
        call = train.spawn(student, epochs=epochs)
        print(f"SPAWNED train call_id={call.object_id} -> manifest at /out/manifest.json on gg-distill")
        return
    manifest = train.remote(student, epochs=epochs)
    print("done:", json.dumps(manifest))
