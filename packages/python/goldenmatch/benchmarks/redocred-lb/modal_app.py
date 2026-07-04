"""Modal training harness for the Re-DocRED leaderboard (ATLOP).

    modal run modal_app.py::smoke                     # image + GPU + data sanity
    modal run --detach modal_app.py --spawn           # full train (survives CLI kill)
    modal volume get redocred-lb /out ./_out          # pull manifest + metrics

Trains the ATLOP relation-extraction model (model.py / losses.py / long_input.py) on the
Re-DocRED train split, evaluates dev + test each epoch with the official DocRED scorer
(scoring.py), and writes the best dev-Ign-F1 checkpoint's metrics to the Volume. The
target is the published ATLOP Re-DocRED number (~76-77 F1) as the reproduction floor, then
a stronger encoder pushes toward the ~80-81 leaderboard top.

Data (train/dev/test_revised.json) is uploaded to the Volume by the local entrypoint; the
tokenizer-dependent preprocessing + rel2id build happen remotely so nothing here needs
torch/transformers locally.
"""
from __future__ import annotations

import pathlib

import modal

HERE = pathlib.Path(__file__).parent

app = modal.App("redocred-lb")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.5.1",
        "transformers==4.46.2",
        "numpy<2",
        "opt_einsum==3.4.0",
        "tqdm==4.67.1",
        "sentencepiece==0.2.0",  # deberta / xlm tokenizers
        "protobuf<5",
    )
    .add_local_dir(
        str(HERE), remote_path="/root/rdlb",
        ignore=["data/*", "tests/*", "__pycache__/*", "*.pyc", "_out/*"],
    )
)

vol = modal.Volume.from_name("redocred-lb", create_if_missing=True)
DATA = "/data"
GPU = "A100-80GB"


def _load_splits():
    import json
    splits = {}
    for s in ("train", "dev", "test"):
        with open(f"{DATA}/{s}_revised.json", encoding="utf-8") as f:
            splits[s] = json.load(f)
    return splits["train"], splits["dev"], splits["test"]


@app.function(image=image, gpu="A10G", volumes={DATA: vol}, timeout=600)
def smoke() -> dict:
    """Confirm image + GPU + that the uploaded data preprocesses and one forward runs."""
    import sys

    sys.path.insert(0, "/root/rdlb")
    import torch
    from model import DocREModel, make_collate
    from prepro import build_rel2id, read_docred
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    train, dev, test = _load_splits()
    rel2id = build_rel2id(train, dev, test)
    tok = AutoTokenizer.from_pretrained("roberta-large")
    feats = read_docred(dev[:4], tok, rel2id, max_seq_length=1024)

    cfg = AutoConfig.from_pretrained("roberta-large", num_labels=97)
    cfg.transformer_type = "roberta"
    cfg.cls_token_id = tok.cls_token_id
    cfg.sep_token_id = tok.sep_token_id
    enc = AutoModel.from_pretrained("roberta-large", config=cfg)
    model = DocREModel(cfg, enc, num_labels=4).cuda()

    collate = make_collate(tok.pad_token_id)
    input_ids, mask, labels, entity_pos, hts = collate(feats)
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        loss, preds = model(input_ids.cuda(), mask.cuda(), entity_pos, hts, labels.cuda())
    info = {
        "torch": torch.__version__,
        "device": torch.cuda.get_device_name(0),
        "n_relations": len(rel2id),
        "dev_docs_prepro": len(feats),
        "pairs_in_batch": int(preds.shape[0]),
        "smoke_loss": float(loss.item()),
    }
    print("SMOKE:", info)
    return info


def _evaluate(model, features, collate, id2rel, gold_docs, train_facts, batch_size):
    import torch
    from model import decode_preds
    from scoring import official_evaluate, to_submission
    from torch.utils.data import DataLoader

    model.eval()
    loader = DataLoader(features, batch_size=batch_size, shuffle=False, collate_fn=collate)
    all_preds_matrix = []
    hts_order = []
    for input_ids, mask, _labels, entity_pos, hts in loader:
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            (preds,) = model(input_ids.cuda(), mask.cuda(), entity_pos, hts)
        all_preds_matrix.append(preds.float().cpu().numpy())
        hts_order.extend(hts)
    import numpy as np
    matrix = np.concatenate(all_preds_matrix, axis=0)
    preds_per_doc = decode_preds(matrix, hts_order, id2rel)
    submission = to_submission(preds_per_doc, gold_docs)
    return official_evaluate(submission, gold_docs, train_facts)


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=60 * 60 * 8)
def train(base_model: str = "roberta-large", epochs: int = 30, lr: float = 3e-5,
          classifier_lr: float = 1e-4, batch_size: int = 4, seed: int = 66,
          num_labels: int = 4, max_seq_length: int = 1024) -> dict:
    import json
    import os
    import sys
    import time

    sys.path.insert(0, "/root/rdlb")
    os.environ.setdefault("HF_HOME", f"{DATA}/hf_cache")

    import numpy as np
    import torch
    from model import DocREModel, make_collate
    from prepro import build_rel2id, read_docred
    from scoring import facts_in_train
    from torch.utils.data import DataLoader
    from transformers import AutoConfig, AutoModel, AutoTokenizer, get_linear_schedule_with_warmup

    torch.manual_seed(seed)
    np.random.seed(seed)

    train_docs, dev_docs, test_docs = _load_splits()
    rel2id = build_rel2id(train_docs, dev_docs, test_docs)
    id2rel = {v: k for k, v in rel2id.items()}
    train_facts = facts_in_train(train_docs)

    transformer_type = "roberta" if "roberta" in base_model else "bert"
    tok = AutoTokenizer.from_pretrained(base_model)
    cfg = AutoConfig.from_pretrained(base_model, num_labels=97)
    cfg.transformer_type = transformer_type
    cfg.cls_token_id = tok.cls_token_id
    cfg.sep_token_id = tok.sep_token_id

    print(f"prepro: {len(train_docs)} train / {len(dev_docs)} dev / {len(test_docs)} test "
          f"| {len(rel2id)} relations", flush=True)
    train_feats = read_docred(train_docs, tok, rel2id, max_seq_length)
    dev_feats = read_docred(dev_docs, tok, rel2id, max_seq_length)
    test_feats = read_docred(test_docs, tok, rel2id, max_seq_length)

    enc = AutoModel.from_pretrained(base_model, config=cfg)
    model = DocREModel(cfg, enc, num_labels=num_labels).cuda()
    collate = make_collate(tok.pad_token_id)

    new_layer = ["extractor", "bilinear"]
    grouped = [
        {"params": [p for n, p in model.named_parameters()
                    if not any(k in n for k in new_layer)], "lr": lr},
        {"params": [p for n, p in model.named_parameters()
                    if any(k in n for k in new_layer)], "lr": classifier_lr},
    ]
    optimizer = torch.optim.AdamW(grouped, lr=lr, eps=1e-6)
    loader = DataLoader(train_feats, batch_size=batch_size, shuffle=True,
                        collate_fn=collate, drop_last=True)
    total_steps = len(loader) * epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, int(0.06 * total_steps), total_steps)

    best_dev = -1.0
    best = {}
    history = []
    for epoch in range(epochs):
        model.train()
        t0 = time.time()
        running = 0.0
        for step, (input_ids, mask, labels, entity_pos, hts) in enumerate(loader):
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss, _ = model(input_ids.cuda(), mask.cuda(), entity_pos, hts, labels.cuda())
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()
            running += float(loss.item())
            if step % 100 == 0:
                print(f"  e{epoch} s{step}/{len(loader)} loss {loss.item():.4f}", flush=True)

        dev_m = _evaluate(model, dev_feats, collate, id2rel, dev_docs, train_facts, batch_size * 2)
        line = {"epoch": epoch, "train_loss": running / len(loader),
                "dev_f1": dev_m["f1"], "dev_ign_f1": dev_m["ign_f1"],
                "dev_p": dev_m["precision"], "dev_r": dev_m["recall"],
                "secs": round(time.time() - t0, 1)}
        history.append(line)
        print(f"EPOCH {epoch}: dev F1 {dev_m['f1']:.4f} Ign {dev_m['ign_f1']:.4f} "
              f"(P {dev_m['precision']:.3f} R {dev_m['recall']:.3f}) "
              f"loss {line['train_loss']:.4f} {line['secs']}s", flush=True)

        if dev_m["ign_f1"] > best_dev:
            best_dev = dev_m["ign_f1"]
            test_m = _evaluate(model, test_feats, collate, id2rel, test_docs,
                               train_facts, batch_size * 2)
            best = {"epoch": epoch, "dev": dev_m, "test": test_m}
            print(f"  * new best dev Ign {best_dev:.4f} -> TEST F1 {test_m['f1']:.4f} "
                  f"Ign {test_m['ign_f1']:.4f}", flush=True)
            _save({"base_model": base_model, "best": best, "history": history})

    manifest = {"base_model": base_model, "epochs": epochs, "lr": lr,
                "best": best, "history": history}
    _save(manifest)
    print("DONE best:", json.dumps(best.get("test", {})), flush=True)
    return manifest


def _save(manifest: dict) -> None:
    import json
    import os
    os.makedirs(f"{DATA}/out", exist_ok=True)
    with open(f"{DATA}/out/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    vol.commit()


@app.local_entrypoint()
def main(base_model: str = "roberta-large", epochs: int = 30, spawn: bool = False,
         smoke_only: bool = False):
    """Upload the Re-DocRED splits to the Volume, then smoke or train."""
    data_dir = HERE / "data"
    files = [data_dir / f"{s}_revised.json" for s in ("train", "dev", "test")]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"missing data: {missing} -- fetch the Re-DocRED splits first")
    with vol.batch_upload(force=True) as up:
        for f in files:
            up.put_file(str(f), f"/{f.name}")
    print(f"uploaded {len(files)} split(s) to the redocred-lb Volume")

    if smoke_only:
        print("smoke:", smoke.remote())
        return
    if spawn:
        call = train.spawn(base_model=base_model, epochs=epochs)
        print(f"SPAWNED train call_id={call.object_id} -> /out/manifest.json on redocred-lb")
        return
    print("result:", train.remote(base_model=base_model, epochs=epochs))
