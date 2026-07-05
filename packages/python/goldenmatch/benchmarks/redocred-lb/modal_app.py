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
        "datasets==3.1.0",  # DocRED distant set for b2 self-training
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
    input_ids, mask, labels, entity_pos, hts, _sp, _ev = collate(feats)
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


@app.function(image=image, gpu="A10G", volumes={DATA: vol}, timeout=600)
def evi_smoke() -> dict:
    """Validate the DREEAM evidence path end to end on GPU: with_evidence prepro ->
    forward with the evidence loss -> backward. Cheap; run before the full evidence train."""
    import sys

    sys.path.insert(0, "/root/rdlb")
    import torch
    from model import DocREModel, make_collate
    from prepro import build_rel2id, read_docred
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    train, dev, test = _load_splits()
    rel2id = build_rel2id(train, dev, test)
    tok = AutoTokenizer.from_pretrained("microsoft/deberta-v3-large")
    feats = read_docred(dev[:6], tok, rel2id, 1024, with_evidence=True)
    cfg = AutoConfig.from_pretrained("microsoft/deberta-v3-large", num_labels=97)
    cfg.transformer_type = "bert"
    cfg.cls_token_id = tok.cls_token_id
    cfg.sep_token_id = tok.sep_token_id
    enc = AutoModel.from_pretrained("microsoft/deberta-v3-large", config=cfg)
    model = DocREModel(cfg, enc, num_labels=4).cuda()
    collate = make_collate(tok.pad_token_id)
    input_ids, mask, labels, entity_pos, hts, sent_pos, evi = collate(feats)
    with torch.autocast("cuda", dtype=torch.bfloat16):
        loss_plain, _ = model(input_ids.cuda(), mask.cuda(), entity_pos, hts, labels.cuda())
        loss_evi, _ = model(input_ids.cuda(), mask.cuda(), entity_pos, hts, labels.cuda(),
                            sent_pos=sent_pos, evidence=evi, evi_lambda=0.1)
    loss_evi.backward()
    n_evi = sum(1 for doc in evi for pair in doc if pair)
    info = {"device": torch.cuda.get_device_name(0),
            "loss_plain": float(loss_plain.item()), "loss_evi": float(loss_evi.item()),
            "pairs_with_evidence": n_evi, "backward_ok": True}
    print("EVI_SMOKE:", info)
    return info


@app.function(image=image, volumes={DATA: vol}, timeout=1800)
def distant_smoke() -> dict:
    """Probe the DocRED distant split's schema so the shaper can be written correctly.
    Tries a few known HF sources; prints the first record's field structure."""
    import json

    from datasets import load_dataset

    errors = {}
    for name, kw in [("docred", {"trust_remote_code": True}),
                     ("thunlp/docred", {"trust_remote_code": True})]:
        try:
            ds = load_dataset(name, **kw)
            splits = list(ds.keys())
            split = next((s for s in splits if "distant" in s), splits[0])
            rec = ds[split][0]
            shape = {k: (type(v).__name__, (v[:1] if isinstance(v, list) else v))
                     for k, v in rec.items()}
            out = {"source": name, "splits": splits, "chosen": split,
                   "n": len(ds[split]), "keys": list(rec.keys()),
                   "sample_shape": str(shape)[:1500]}
            print("DISTANT_SCHEMA:", json.dumps(out)[:1900])
            return out
        except Exception as e:  # noqa: BLE001
            errors[name] = repr(e)[:300]
    print("DISTANT_SMOKE_FAILED:", errors)
    return {"errors": errors}


def _evaluate(model, features, collate, id2rel, gold_docs, train_facts, batch_size):
    import torch
    from model import decode_preds
    from scoring import official_evaluate, to_submission
    from torch.utils.data import DataLoader

    model.eval()
    loader = DataLoader(features, batch_size=batch_size, shuffle=False, collate_fn=collate)
    all_preds_matrix = []
    hts_order = []
    for input_ids, mask, _labels, entity_pos, hts, _sp, _ev in loader:
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            (preds,) = model(input_ids.cuda(), mask.cuda(), entity_pos, hts)
        all_preds_matrix.append(preds.float().cpu().numpy())
        hts_order.extend(hts)
    import numpy as np
    matrix = np.concatenate(all_preds_matrix, axis=0)
    preds_per_doc = decode_preds(matrix, hts_order, id2rel)
    submission = to_submission(preds_per_doc, gold_docs)
    return official_evaluate(submission, gold_docs, train_facts)


def _logits_on_test(base_model, ckpt_tag, test_docs, rel2id, max_seq_length=1024,
                    batch_size=8):
    """Load a saved checkpoint and return its raw pre-threshold logits over the test set,
    in the canonical (doc, h, t) pair order, plus that pair order for decoding."""
    import sys

    import numpy as np
    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    sys.path.insert(0, "/root/rdlb")
    from model import DocREModel, make_collate
    from prepro import read_docred

    transformer_type = "roberta" if "roberta" in base_model else "bert"
    tok = AutoTokenizer.from_pretrained(base_model)
    cfg = AutoConfig.from_pretrained(base_model, num_labels=97)
    cfg.transformer_type = transformer_type
    cfg.cls_token_id = tok.cls_token_id
    cfg.sep_token_id = tok.sep_token_id
    enc = AutoModel.from_pretrained(base_model, config=cfg)
    model = DocREModel(cfg, enc, num_labels=4).cuda()
    state = torch.load(f"{DATA}/ckpt/{ckpt_tag}/model.pt", map_location="cuda")
    model.load_state_dict(state)
    model.eval()

    feats = read_docred(test_docs, tok, rel2id, max_seq_length)
    collate = make_collate(tok.pad_token_id)
    loader = DataLoader(feats, batch_size=batch_size, shuffle=False, collate_fn=collate)
    logits_all, hts_order = [], []
    for input_ids, mask, _labels, entity_pos, hts, _sp, _ev in loader:
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            _preds, logits = model(input_ids.cuda(), mask.cuda(), entity_pos, hts,
                                   return_logits=True)
        logits_all.append(logits.float().cpu().numpy())
        hts_order.extend(hts)
    # gold relation matrix Y[pair, 97] (tokenizer-independent); classes 1..96 are the truth
    gold = np.asarray([lab for f in feats for lab in f["labels"]], dtype=np.int8)
    return np.concatenate(logits_all, axis=0), hts_order, gold


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=60 * 60 * 3)
def ensemble_eval(checkpoints: list, num_labels: int = 4) -> dict:
    """Logit-averaging ensemble with dev-selected subset + dev-tuned threshold offset.

    Compute each checkpoint's pre-threshold logits on dev+test ONCE on GPU (and dump them
    to the Volume as .npy for instant future re-sweeps). Then the whole subset x threshold
    search runs in vectorised numpy against a gold label matrix (`_fast_f1`) -- no Python
    per-pair loop, no repeated official-scorer calls. The single selected config is scored
    once with the exact official scorer for the reported number (honest dev-select / test)."""
    import os
    import sys

    import numpy as np

    sys.path.insert(0, "/root/rdlb")
    from itertools import combinations

    from ensemble_ops import fast_f1, predict_np
    from model import decode_preds
    from prepro import build_rel2id
    from scoring import facts_in_train, official_evaluate, to_submission

    train_docs, dev_docs, test_docs = _load_splits()
    rel2id = build_rel2id(train_docs, dev_docs, test_docs)
    id2rel = {v: k for k, v in rel2id.items()}
    train_facts = facts_in_train(train_docs)

    tags = [ck["tag"] for ck in checkpoints]
    dev_logits, test_logits = {}, {}
    hts_test = gold_dev = gold_test = None
    os.makedirs(f"{DATA}/logits", exist_ok=True)
    members = []
    for ck in checkpoints:
        dl, _hd, gd = _logits_on_test(ck["base_model"], ck["tag"], dev_docs, rel2id)
        tl, ht, gt = _logits_on_test(ck["base_model"], ck["tag"], test_docs, rel2id)
        dev_logits[ck["tag"]], test_logits[ck["tag"]] = dl, tl
        hts_test, gold_dev, gold_test = ht, gd, gt
        np.save(f"{DATA}/logits/{ck['tag']}_dev.npy", dl)
        np.save(f"{DATA}/logits/{ck['tag']}_test.npy", tl)
        solo = fast_f1(predict_np(tl, 0.0, num_labels), gold_test)
        members.append({"tag": ck["tag"], "f1": solo})
        print(f"  member {ck['tag']}: TEST F1 {solo:.4f}", flush=True)
    vol.commit()

    deltas = np.round(np.arange(-0.3, 1.01, 0.05), 3)

    def avg(split_logits, subset):
        return sum(split_logits[t] for t in subset) / len(subset)

    # dev-select over every subset (size>=2) x threshold offset -- all vectorised numpy
    best = None
    for r in range(2, len(tags) + 1):
        for subset in combinations(tags, r):
            dev_avg = avg(dev_logits, subset)
            for d in deltas:
                f1 = fast_f1(predict_np(dev_avg, float(d), num_labels), gold_dev)
                if best is None or f1 > best["dev_f1"]:
                    best = {"subset": list(subset), "delta": float(d), "dev_f1": f1}

    # exact official scoring for the reported numbers (once each)
    def official(logits, delta, hts_order, gold_docs):
        preds = predict_np(logits, delta, num_labels)
        sub = to_submission(decode_preds(preds, hts_order, id2rel), gold_docs)
        return official_evaluate(sub, gold_docs, train_facts)

    full_raw = official(avg(test_logits, tags), 0.0, hts_test, test_docs)
    test_best = official(avg(test_logits, best["subset"]), best["delta"], hts_test, test_docs)
    out = {"members": members, "full_raw": full_raw, "best_config": best, "best_test": test_best}
    print(f"FULL ENSEMBLE (delta=0): TEST F1 {full_raw['f1']:.4f} Ign {full_raw['ign_f1']:.4f}", flush=True)
    print(f"DEV-SELECTED subset={best['subset']} delta={best['delta']} (dev F1 {best['dev_f1']:.4f})",
          flush=True)
    print(f"  -> TEST F1 {test_best['f1']:.4f} Ign {test_best['ign_f1']:.4f} "
          f"(P {test_best['precision']:.3f} R {test_best['recall']:.3f})", flush=True)
    _save(out, "manifest_ensemble.json")
    return out


@app.function(image=image, gpu=GPU, volumes={DATA: vol}, timeout=60 * 60 * 10)
def train(base_model: str = "roberta-large", epochs: int = 30, lr: float = 3e-5,
          classifier_lr: float = 1e-4, batch_size: int = 4, seed: int = 66,
          num_labels: int = 4, max_seq_length: int = 1024,
          evidence: bool = False, evi_lambda: float = 0.1, save_ckpt: bool = False,
          tag: str = "") -> dict:
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

    run_tag = (tag or base_model).replace("/", "_")
    transformer_type = "roberta" if "roberta" in base_model else "bert"
    tok = AutoTokenizer.from_pretrained(base_model)
    cfg = AutoConfig.from_pretrained(base_model, num_labels=97)
    cfg.transformer_type = transformer_type
    cfg.cls_token_id = tok.cls_token_id
    cfg.sep_token_id = tok.sep_token_id

    print(f"prepro: {len(train_docs)} train / {len(dev_docs)} dev / {len(test_docs)} test "
          f"| {len(rel2id)} relations | evidence={evidence}", flush=True)
    train_feats = read_docred(train_docs, tok, rel2id, max_seq_length, with_evidence=evidence)
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
        for step, (input_ids, mask, labels, entity_pos, hts, sent_pos, evi) in enumerate(loader):
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss, _ = model(input_ids.cuda(), mask.cuda(), entity_pos, hts, labels.cuda(),
                                sent_pos=sent_pos, evidence=evi, evi_lambda=evi_lambda)
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
            _save({"base_model": base_model, "evidence": evidence, "best": best,
                   "history": history}, _manifest_name(run_tag))
            if save_ckpt:
                ckpt = f"{DATA}/ckpt/{run_tag}"
                os.makedirs(ckpt, exist_ok=True)
                torch.save(model.state_dict(), f"{ckpt}/model.pt")
                vol.commit()

    manifest = {"base_model": base_model, "evidence": evidence, "evi_lambda": evi_lambda,
                "epochs": epochs, "lr": lr, "best": best, "history": history}
    _save(manifest, _manifest_name(run_tag))
    print("DONE best:", json.dumps(best.get("test", {})), flush=True)
    return manifest


def _manifest_name(base_model: str) -> str:
    """Per-run manifest filename so parallel encoder runs on the shared Volume don't
    clobber each other's metrics."""
    return "manifest_" + base_model.replace("/", "_") + ".json"


def _save(manifest: dict, name: str = "manifest.json") -> None:
    import json
    import os
    os.makedirs(f"{DATA}/out", exist_ok=True)
    with open(f"{DATA}/out/{name}", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    vol.commit()


@app.local_entrypoint()
def run_ensemble(tags: str = "deberta-s13,deberta-s41,deberta-evi,roberta-s7"):
    """Ensemble the saved checkpoints named by ``--tags`` (comma-separated). Base model is
    inferred from the tag prefix. Prints per-member + ensemble F1; also writes
    /out/manifest_ensemble.json on the Volume."""
    checkpoints = []
    for t in tags.split(","):
        t = t.strip()
        bm = "roberta-large" if t.startswith("roberta") else "microsoft/deberta-v3-large"
        checkpoints.append({"tag": t, "base_model": bm})
    print("ensembling:", checkpoints)
    print("result:", ensemble_eval.remote(checkpoints))


@app.local_entrypoint()
def main(base_model: str = "roberta-large", epochs: int = 30, spawn: bool = False,
         smoke_only: bool = False, evidence: bool = False, evi_lambda: float = 0.1,
         save_ckpt: bool = False, tag: str = "", seed: int = 66):
    """Upload the Re-DocRED splits to the Volume, then smoke or train.

    `--evidence` turns on DREEAM-style evidence supervision; `--save-ckpt` persists the
    best checkpoint to the Volume (for later ensembling / self-training); `--tag` names
    the run's manifest/checkpoint so parallel variants don't collide."""
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
    kw = dict(base_model=base_model, epochs=epochs, evidence=evidence,
              evi_lambda=evi_lambda, save_ckpt=save_ckpt, tag=tag, seed=seed)
    run_tag = (tag or base_model).replace("/", "_")
    if spawn:
        call = train.spawn(**kw)
        print(f"SPAWNED train call_id={call.object_id} "
              f"-> /out/{_manifest_name(run_tag)} on redocred-lb")
        return
    print("result:", train.remote(**kw))
