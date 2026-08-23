"""Modal app: Layer-1 mechanistic interpretability of the 1.5B ER-matcher.

Locks the correlational finding from ``decision_geometry.py`` (the same-entity
decision is a low-dim linear structure in the FINAL layer) into a CAUSAL,
per-layer, dictionary-learned result -- the part the llama.cpp box cannot do
because it needs the fp16 model with forward hooks on GPU.

Runs OUT-OF-BAND on Modal serverless GPU (NOT CI, NOT the dev sandbox). Uses the
SAME merged fp16 fine-tune the GGUF was quantized from (``/out/model_1p5b/merged``
on the ``er-matcher-out`` volume) so the geometry is the production model's.

Three stages (each a Modal function; run in order):

  1. capture_probe_layers -- ``output_hidden_states`` residual stream at the
     decision token, over hard + random pairs; the three geometric probes (linear
     separability / held-out direction / low-rank) PER LAYER. Answers *where
     across depth* the match primitive forms. -> interp/layer_probes.json
  2. train_sae -- a sparse autoencoder on layer-L residual activations
     (dictionary learning: decompose the residual under superposition into an
     overcomplete monosemantic basis). Ranks features by decision-token match
     correlation. -> interp/sae_layer{L}.pt + interp/sae_features_layer{L}.json
  3. causal_validate -- THE LOCK. Forward hooks that steer/ablate along (a) the
     diff-of-means direction and (b) the top SAE feature decoder directions at
     layer L; sweep the coefficient, measure the P(match) logit shift. A direction
     is a real primitive only if moving along it moves the verdict.
     -> interp/causal_layer{L}.json

Later stages (added as the thread progressed) translate and stress-test the
locked result: ``layer2_abstraction`` (field decomposition), ``layer_early_exit``
/ ``truncate_adapt`` (depth stripping), ``leniency_dial`` (steering as a P/R
knob), and ``faithfulness_eval`` (how much of the model's ACTUAL verdict the
shipped per-field weights explain).

Usage:
  modal run scripts/er_matcher/interp/modal_interp.py::probe_layers
  modal run scripts/er_matcher/interp/modal_interp.py::sae --layer 14
  modal run scripts/er_matcher/interp/modal_interp.py::causal --layer 14
  modal run scripts/er_matcher/interp/modal_interp.py::faithfulness

Imported by NOTHING (executed only via `modal run`), so the top-level
`import modal` is intentional and safe -- mirrors modal_train.py.
"""
from __future__ import annotations

import modal  # noqa: I001 -- only ever run via `modal run`

APP_NAME = "goldenmatch-er-matcher-interp"
GPU = "A10G"  # 1.5B fp16 inference + a tiny SAE fit comfortably on A10G
MODEL_PATH = "/out/model_1p5b/merged"

_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.4.0",
        "transformers==4.44.2",
        "numpy<2",
        "scikit-learn==1.5.1",
        "polars==1.6.0",
        "pyarrow==17.0.0",
        "jellyfish==1.1.0",
    )
    .env({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
    # the shared prompt contract (build_chat) + our probe helpers
    .add_local_dir(
        "packages/python/goldenmatch/goldenmatch/core/er_matcher",
        remote_path="/root/goldenmatch/core/er_matcher",
    )
    .add_local_dir("scripts/er_matcher/interp", remote_path="/root/interp")
    # Magellan/DeepMatcher loaders, so the messy-domain (product) runs can read the
    # already-fetched walmart_amazon tables off the volume -- pure parse, no network.
    .add_local_dir("scripts/er_matcher/sources", remote_path="/root/sources")
    # the labeled person data the probe pairs are mined from
    .add_local_file(
        "scripts/autoconfig_quality/vendored/historical_50k.parquet",
        remote_path="/root/data/historical_50k.parquet",
    )
)

app = modal.App(APP_NAME)
_out_vol = modal.Volume.from_name("er-matcher-out", create_if_missing=True)

DATA = "/root/data/historical_50k.parquet"
FIELDS = ["first_name", "surname", "dob", "birth_place", "postcode_fake", "occupation"]


# --------------------------------------------------------------------------- #
# shared in-container helpers                                                  #
# --------------------------------------------------------------------------- #
def _load_model():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODEL_PATH)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "left"  # so the decision token is always position -1 in a batch
    model = (
        AutoModelForCausalLM.from_pretrained(
            MODEL_PATH, torch_dtype=torch.float16, attn_implementation="sdpa"
        )
        .cuda()
        .eval()
    )
    return tok, model


def _mine(per_class: int, negatives: str, seed: int):
    """Mine probe pairs + return (pairs, rows) using the committed pure helper."""
    import sys

    import jellyfish
    import polars as pl
    import pyarrow.parquet as pq

    sys.path.insert(0, "/root/interp")
    from decision_geometry import mine_probe_pairs

    raw = pl.from_arrow(pq.read_table(DATA))
    gold = raw["cluster"].to_list()
    rows = {i: {f: (raw[f][i] or "") for f in FIELDS} for i in range(len(gold))}
    surname_key = [jellyfish.soundex(str(raw["surname"][i] or "")) for i in range(len(gold))]
    pairs = mine_probe_pairs(gold, surname_key, per_class, negatives=negatives, seed=seed)
    return pairs, rows


def _load_product_pairs(dataset: str, limit: int = 0):
    """Magellan/DeepMatcher product pairs -> (train, test, rows, fields).

    The MESSY-domain counterpart to ``_mine``: real product records (noisy
    titles, missing brands/model numbers) with DeepMatcher's own pre-labeled
    train/test splits, so no negative mining or cluster-disjoint split is needed
    -- the benchmark's canonical splits ARE the honest split. Reads the copy
    already fetched onto the volume at ``/out/magellan/<dataset>``; the parse is
    pure (no network).

    ``fields`` is the sorted union of the two tables' columns minus ``id``.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, "/root")
    from sources.magellan import MagellanSource

    root = Path(f"/out/magellan/{dataset}")
    if not root.exists():
        raise RuntimeError(
            f"{root} missing -- fetch it once via modal_train.py::zeroshot "
            f"(cite-only license, so it is never committed)"
        )
    splits = MagellanSource(dataset, root).splits()

    rows: dict[int, dict] = {}
    key_of: dict[str, int] = {}
    fields: set[str] = set()

    def _pairs(rs):
        out = []
        for r in rs:
            for side in ("a", "b"):
                fields.update(k for k in r[side] if k != "id")
            ids = []
            for side, eid in (("a", r["eid_a"]), ("b", r["eid_b"])):
                if eid not in key_of:
                    key_of[eid] = len(rows)
                    rows[len(rows)] = dict(r[side])
                ids.append(key_of[eid])
            out.append((ids[0], ids[1], 1 if r["label"] == "match" else 0))
        return out

    tr = _pairs(splits["train"])
    te = _pairs(splits["test"])
    flds = sorted(fields - {"id"})
    for i in rows:
        rows[i] = {f: (rows[i].get(f) or "") for f in flds}
    if limit:
        tr, te = tr[:limit], te[:limit]
    return tr, te, rows, flds


def _prompt(tok, a: dict, b: dict) -> str:
    import sys

    sys.path.insert(0, "/root")
    from goldenmatch.core.er_matcher.prompt import build_chat

    return tok.apply_chat_template(build_chat(a, b), tokenize=False, add_generation_prompt=True)


def _true_false_ids(tok) -> tuple[int, int]:
    t = tok.encode("true", add_special_tokens=False)
    f = tok.encode("false", add_special_tokens=False)
    if len(t) != 1 or len(f) != 1:
        raise RuntimeError(f"true/false not single tokens: {t} {f}")
    return t[0], f[0]


# --------------------------------------------------------------------------- #
# stage 1: per-layer residual capture + geometric probes                      #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def capture_probe_layers(per_class: int = 200, negatives: str = "hard", seed: int = 0,
                         batch_size: int = 16) -> None:
    """Decision-token residual at EVERY layer -> the three probes per layer."""
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")
    from decision_geometry import (
        probe_held_out_direction,
        probe_linear_separability,
        probe_low_rank,
    )

    tok, model = _load_model()
    n_layers = model.config.num_hidden_layers
    print(f"[capture] model layers={n_layers} hidden={model.config.hidden_size}")

    pairs, rows = _mine(per_class, negatives, seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) for a, b, _ in pairs]

    # per-layer decision-token hidden states: hidden_states[L][:, -1, :]
    per_layer: list[list[np.ndarray]] = [[] for _ in range(n_layers + 1)]
    for i in range(0, len(prompts), batch_size):
        chunk = prompts[i : i + batch_size]
        enc = tok(chunk, return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            out = model(**enc, output_hidden_states=True)
        for L, hs in enumerate(out.hidden_states):  # length n_layers+1
            per_layer[L].append(hs[:, -1, :].float().cpu().numpy())
        if (i // batch_size) % 5 == 0:
            print(f"[capture] {i + len(chunk)}/{len(prompts)}", flush=True)

    results = []
    for L in range(n_layers + 1):
        X = np.concatenate(per_layer[L], axis=0)
        # L0 is the token embedding: the decision token is always "\n", so every
        # pair's last-token vector is identical (RoPE is applied inside attention,
        # not at the embedding) -> zero class-mean difference. Skip such degenerate
        # layers rather than divide by a zero-norm direction.
        class_gap = float(np.linalg.norm(X[y == 1].mean(0) - X[y == 0].mean(0)))
        if class_gap < 1e-6:
            results.append({"layer": L, "degenerate": True, "class_gap": class_gap})
            print(f"[probe] L{L:2d}  DEGENERATE (class_gap={class_gap:.2e}) -- skipped",
                  flush=True)
            continue
        acc = probe_linear_separability(X, y)
        mean_auc, std_auc = probe_held_out_direction(X, y, seed=seed)
        low = probe_low_rank(X, y)
        results.append({
            "layer": L, "sep_acc": acc, "dir_auc": mean_auc, "dir_auc_std": std_auc,
            "low_rank": low,
        })
        print(f"[probe] L{L:2d}  sep={acc:.3f}  dir_auc={mean_auc:.3f}+/-{std_auc:.3f}  "
              f"top1={low.get(1):.3f} top8={low.get(8):.3f}", flush=True)

    scored = [r for r in results if not r.get("degenerate")]
    best = max(scored, key=lambda r: r["dir_auc"])
    payload = {
        "model": MODEL_PATH, "n_pairs": len(pairs), "negatives": negatives, "seed": seed,
        "n_layers": n_layers, "layers": results,
        "best_layer_by_dir_auc": best["layer"], "best_dir_auc": best["dir_auc"],
    }
    os.makedirs("/out/interp", exist_ok=True)
    with open("/out/interp/layer_probes.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] best layer by dir AUC = L{best['layer']} ({best['dir_auc']:.3f}) "
          "-> /out/interp/layer_probes.json")


# --------------------------------------------------------------------------- #
# stage 2: sparse autoencoder (dictionary learning) on layer-L residuals      #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=90 * 60, volumes={"/out": _out_vol})
def train_sae(layer: int = 14, n_pairs: int = 3000, expansion: int = 16, l1: float = 4e-3,
              steps: int = 4000, lr: float = 1e-3, seed: int = 0, batch_tokens: int = 4096) -> None:
    """Train an SAE on ALL-position residuals at ``layer`` (many activations), then
    rank features by how their DECISION-TOKEN activation correlates with the match
    label. Standard tied-bias SAE: z=ReLU(W_enc(x-b_dec)+b_enc); x_hat=W_dec z+b_dec;
    loss=||x-x_hat||^2 + l1*||z||_1 with unit-norm decoder columns."""
    import json
    import os
    import sys

    import numpy as np
    import torch
    import torch.nn.functional as F

    sys.path.insert(0, "/root/interp")

    tok, model = _load_model()
    d = model.config.hidden_size
    m = expansion * d
    print(f"[sae] layer={layer} d={d} features={m} l1={l1} steps={steps}")

    # capture: all non-pad token residuals at `layer` (train set) + decision-token
    # residuals with labels (analysis set).
    pairs, rows = _mine(n_pairs // 2, "hard", seed)
    prompts = [_prompt(tok, rows[a], rows[b]) for a, b, _ in pairs]
    y = np.array([t for *_, t in pairs])

    acts: list[np.ndarray] = []
    dec_tok: list[np.ndarray] = []
    bs = 16
    for i in range(0, len(prompts), bs):
        enc = tok(prompts[i : i + bs], return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            out = model(**enc, output_hidden_states=True)
        hs = out.hidden_states[layer]  # (b, seq, d)
        mask = enc["attention_mask"].bool()
        acts.append(hs[mask].float().cpu().numpy())
        dec_tok.append(hs[:, -1, :].float().cpu().numpy())
        if (i // bs) % 10 == 0:
            print(f"[sae capture] {i}/{len(prompts)}", flush=True)
    A = np.concatenate(acts, axis=0)
    D = np.concatenate(dec_tok, axis=0)
    # normalize activations to mean L2 norm sqrt(d) (standard SAE hygiene): makes
    # the L1 coefficient meaningful + transferable and gives a sparse code instead
    # of a dense one. A uniform scalar does NOT rotate the space, so the learned
    # unit-norm decoder directions are unchanged -- only the recon/L1 balance is.
    norm_scale = float(np.linalg.norm(A, axis=1).mean() / (A.shape[1] ** 0.5))
    A = A / norm_scale
    D = D / norm_scale
    print(f"[sae] train activations={A.shape} decision-token={D.shape} "
          f"norm_scale={norm_scale:.3f}")

    torch.manual_seed(seed)
    dev = model.device
    At = torch.tensor(A, dtype=torch.float32, device=dev)
    b_dec = At.mean(0).clone()
    W_enc = torch.randn(d, m, device=dev) * (1.0 / d**0.5)
    b_enc = torch.zeros(m, device=dev)
    W_dec = torch.randn(m, d, device=dev)
    W_dec = W_dec / W_dec.norm(dim=1, keepdim=True)
    for p in (W_enc, b_enc, W_dec, b_dec):
        p.requires_grad_(True)
    opt = torch.optim.Adam([W_enc, b_enc, W_dec, b_dec], lr=lr)

    n = At.shape[0]
    for step in range(steps):
        idx = torch.randint(0, n, (batch_tokens,), device=dev)
        x = At[idx]
        z = F.relu((x - b_dec) @ W_enc + b_enc)
        x_hat = z @ W_dec + b_dec
        # per-sample sums (batch-averaged) is the standard SAE convention: this
        # puts the L1 coefficient at the usual scale. Averaging over features
        # (the earlier bug) divided the per-sample penalty by m=#features, so L1
        # was ~1e4x too weak and the code stayed dense (l0 ~ m/2).
        recon = ((x_hat - x) ** 2).sum(1).mean()
        sparsity = z.abs().sum(1).mean()
        loss = recon + l1 * sparsity
        opt.zero_grad()
        loss.backward()
        with torch.no_grad():  # keep decoder columns unit-norm (standard SAE constraint)
            W_dec.grad -= (W_dec.grad * W_dec).sum(1, keepdim=True) * W_dec
        opt.step()
        with torch.no_grad():
            W_dec /= W_dec.norm(dim=1, keepdim=True)
        if step % 500 == 0 or step == steps - 1:
            with torch.no_grad():
                l0 = (z > 1e-6).float().sum(1).mean().item()
            print(f"[sae] step {step} recon={recon.item():.4f} l0={l0:.1f} "
                  f"sparsity={sparsity.item():.4f}", flush=True)

    # feature analysis at the decision token: correlate each feature activation w/ y
    with torch.no_grad():
        Dt = torch.tensor(D, dtype=torch.float32, device=dev)
        Z = F.relu((Dt - b_dec) @ W_enc + b_enc).cpu().numpy()  # (n_pairs, m)
    yv = y.astype(np.float64)
    yc = yv - yv.mean()
    corr = np.zeros(Z.shape[1])
    for j in range(Z.shape[1]):
        zc = Z[:, j] - Z[:, j].mean()
        denom = np.sqrt((zc**2).sum() * (yc**2).sum())
        corr[j] = (zc * yc).sum() / denom if denom > 1e-9 else 0.0
    order = np.argsort(-np.abs(corr))
    top = [{"feature": int(j), "match_corr": float(corr[j]),
            "fire_rate": float((Z[:, j] > 1e-6).mean())} for j in order[:40]]
    print("[sae] top match-correlated features:",
          json.dumps(top[:8], indent=2), flush=True)

    os.makedirs("/out/interp", exist_ok=True)
    torch.save({"W_enc": W_enc.detach().cpu(), "b_enc": b_enc.detach().cpu(),
                "W_dec": W_dec.detach().cpu(), "b_dec": b_dec.detach().cpu(),
                "layer": layer, "d": d, "m": m, "norm_scale": norm_scale},
               f"/out/interp/sae_layer{layer}.pt")
    with open(f"/out/interp/sae_features_layer{layer}.json", "w") as fh:
        json.dump({"layer": layer, "d": d, "m": m, "l1": l1, "steps": steps,
                   "norm_scale": norm_scale, "n_train_acts": int(A.shape[0]),
                   "top_features": top}, fh, indent=2)
    _out_vol.commit()
    print(f"[done] SAE -> /out/interp/sae_layer{layer}.pt + sae_features_layer{layer}.json")


# --------------------------------------------------------------------------- #
# stage 5: strip parameters that don't influence the outcome (layer early-exit)#
# --------------------------------------------------------------------------- #
def _prf_from_pred(pred, y):
    return _pr_from_pred(pred, y)[2]


def _pr_from_pred(pred, y):
    tp = sum(1 for p, t in zip(pred, y) if p and t)
    fp = sum(1 for p, t in zip(pred, y) if p and not t)
    fn = sum(1 for p, t in zip(pred, y) if not p and t)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if prec + rec else 0.0
    return prec, rec, f1


# --------------------------------------------------------------------------- #
# stage 7: the LENIENCY DIAL -- steer the causal decision axis as a P/R knob   #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def leniency_dial(per_class: int = 500, seed: int = 0, lo: int = 8, hi: int = 20,
                  coeffs: str = "-6,-4,-3,-2,-1,-0.5,0,0.5,1,2,3,4,6") -> None:
    """Turn the causally-validated match direction into a usable PRECISION/RECALL
    knob, and measure whether steering the model's INTERNAL decision axis traces a
    better P/R frontier than a plain threshold on its output.

    Per-layer diff-of-means directions from a record-DISJOINT TRAIN split; on TEST:
      - THRESHOLD baseline: unsteered P(match), sweep the decision threshold.
      - STEERING dial: fix threshold 0.5, add c*gap_L*dir_L across layers [lo,hi]
        (the validated multi-layer recipe), sweep c.
    Both trace a P/R curve; if steering's frontier dominates, the model's own
    certainty axis is a better leniency control than a squashed-output cut."""
    import json
    import os
    import sys

    import jellyfish
    import numpy as np
    import polars as pl
    import pyarrow.parquet as pq
    import torch

    sys.path.insert(0, "/root/interp")
    from decision_geometry import mine_probe_pairs

    tok, model = _load_model()
    true_id, false_id = _true_false_ids(tok)
    dev = model.device
    window = list(range(lo, hi + 1))
    cvals = [float(c) for c in coeffs.split(",")]

    raw = pl.from_arrow(pq.read_table(DATA))
    gold = raw["cluster"].to_list()
    rows = {i: {f: (raw[f][i] or "") for f in FIELDS} for i in range(len(gold))}
    surname_key = [jellyfish.soundex(str(raw["surname"][i] or "")) for i in range(len(gold))]

    by_cluster: dict = {}
    for i, g in enumerate(gold):
        by_cluster.setdefault(g, []).append(i)
    clusters = sorted(by_cluster)
    train_ids = {i for c in clusters[::2] for i in by_cluster[c]}
    test_ids = {i for c in clusters[1::2] for i in by_cluster[c]}
    pool = mine_probe_pairs(gold, surname_key, per_class * 3, negatives="hard", seed=seed)
    tr = [(a, b, t) for a, b, t in pool if a in train_ids and b in train_ids][: per_class * 2]
    te = [(a, b, t) for a, b, t in pool if a in test_ids and b in test_ids][: per_class * 2]
    yte = np.array([t for *_, t in te])
    print(f"[dial] train={len(tr)} test={len(te)} window={lo}..{hi}", flush=True)

    def prompts_of(pairs):
        return [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    def dec_resid(pairs, layer):
        reps = []
        for i in range(0, len(pairs), 16):
            enc = tok(prompts_of(pairs[i : i + 16]), return_tensors="pt",
                      padding=True).to(dev)
            with torch.no_grad():
                out = model(**enc, output_hidden_states=True)
            reps.append(out.hidden_states[layer][:, -1, :].float().cpu().numpy())
        return np.concatenate(reps, 0)

    ytr = np.array([t for *_, t in tr])
    layer_dirs = {}
    for L in window:
        R = dec_resid(tr, L)
        d = R[ytr == 1].mean(0) - R[ytr == 0].mean(0)
        g = float(np.linalg.norm(d))
        layer_dirs[L] = (torch.tensor(d / (g + 1e-9), dtype=torch.float16, device=dev), g)

    ctrl = {"coeff": 0.0}

    def make_hook(L):
        u, g = layer_dirs[L]

        def hook(_m, _i, output):
            hidden = output[0] if isinstance(output, tuple) else output
            if ctrl["coeff"]:
                hidden[:, -1, :] = hidden[:, -1, :] + ctrl["coeff"] * g * u
            return (hidden, *output[1:]) if isinstance(output, tuple) else hidden

        return hook

    hooks = [model.model.layers[L - 1].register_forward_hook(make_hook(L)) for L in window]

    te_prompts = prompts_of(te)

    def p_match_all(coeff):
        ctrl["coeff"] = coeff
        ps = []
        for i in range(0, len(te_prompts), 16):
            enc = tok(te_prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            pair = torch.softmax(torch.stack([logits[:, true_id], logits[:, false_id]], 1), 1)
            ps.append(pair[:, 0].float().cpu().numpy())
        ctrl["coeff"] = 0.0
        return np.concatenate(ps)

    base_p = p_match_all(0.0)  # unsteered probabilities for the threshold baseline
    thr_frontier = []
    for ti in range(5, 100, 5):
        T = ti / 100
        p, r, f1 = _pr_from_pred(base_p >= T, yte)
        thr_frontier.append({"threshold": T, "precision": p, "recall": r, "f1": f1})

    steer_frontier = []
    for c in cvals:
        pc = p_match_all(c)
        p, r, f1 = _pr_from_pred(pc >= 0.5, yte)
        steer_frontier.append({"coeff": c, "precision": p, "recall": r, "f1": f1})
        print(f"[dial] steer c={c:+.1f}: P={p:.3f} R={r:.3f} F1={f1:.3f}", flush=True)

    for h in hooks:
        h.remove()

    best_thr = max(thr_frontier, key=lambda d: d["f1"])
    best_steer = max(steer_frontier, key=lambda d: d["f1"])
    # is the steering frontier at least as good as thresholding at matched recall?
    dominates = 0
    for s in steer_frontier:
        near = [t for t in thr_frontier if abs(t["recall"] - s["recall"]) <= 0.03]
        if near and s["precision"] >= max(t["precision"] for t in near) - 1e-9:
            dominates += 1
    print(f"[dial] best threshold F1={best_thr['f1']:.3f}  best steer F1={best_steer['f1']:.3f}  "
          f"steer>=thr at matched recall: {dominates}/{len(steer_frontier)}", flush=True)

    payload = {"window": [lo, hi], "n_test": len(te), "coeffs": cvals,
               "threshold_frontier": thr_frontier, "steering_frontier": steer_frontier,
               "best_threshold": best_thr, "best_steer": best_steer,
               "steer_ge_threshold_at_recall": dominates, "n_steer_points": len(cvals)}
    os.makedirs("/out/interp", exist_ok=True)
    with open("/out/interp/leniency_dial.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print("[done] leniency_dial -> /out/interp/leniency_dial.json")


@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def layer_early_exit(per_class: int = 400, seed: int = 0, k_min: int = 6) -> None:
    """Strip parameters that don't influence the ER verdict, measured directly.

    Layer 1 said the decision is FORMED by ~L13 and only COMMITTED thereafter. If
    that's causal, the late layers are dead weight for ER. This tests it: read the
    verdict out of the layer-K residual (final norm + lm_head applied to
    hidden_states[K] -- i.e. DELETE layers > K and pass the residual straight to the
    readout, the 'logit lens'), sweep K, and compare to the full-model verdict + the
    gold labels. The smallest K that preserves the decision tells us how many layers
    can be stripped. (Logit-lens caveat: the final RMSNorm is trained for the last
    layer's scale; it is scale-robust but this is an activation-space estimate of
    strippability, to be confirmed by a fine-tune-free truncated-model eval.)"""
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")

    tok, model = _load_model()
    true_id, false_id = _true_false_ids(tok)
    n_layers = model.config.num_hidden_layers

    pairs, rows = _mine(per_class, "hard", seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    # collect per-layer decision-token P(match) via the logit lens in one pass set
    norm = model.model.norm
    head = model.lm_head
    per_layer_p = {K: [] for K in range(k_min, n_layers + 1)}
    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            out = model(**enc, output_hidden_states=True)
            for K in range(k_min, n_layers + 1):
                h = out.hidden_states[K][:, -1, :]          # residual after layer K
                logits = head(norm(h))                       # delete layers>K -> readout
                pair = torch.softmax(
                    torch.stack([logits[:, true_id], logits[:, false_id]], 1), dim=1)
                per_layer_p[K].append(pair[:, 0].float().cpu().numpy())
        if (i // 16) % 5 == 0:
            print(f"[strip] {i + 16}/{len(prompts)}", flush=True)

    full_p = np.concatenate(per_layer_p[n_layers])
    full_pred = full_p > 0.5
    full_f1 = _prf_from_pred(full_pred, y)
    print(f"[strip] full model ({n_layers} layers): F1={full_f1:.3f}", flush=True)

    rows_out = []
    for K in range(k_min, n_layers + 1):
        pK = np.concatenate(per_layer_p[K])
        predK = pK > 0.5
        agree = float((predK == full_pred).mean())        # verdict agreement vs full
        f1K = _prf_from_pred(predK, y)
        rows_out.append({"K": K, "verdict_agree_vs_full": agree, "f1_vs_gold": f1K,
                         "mean_p_match": float(pK.mean())})
        print(f"[strip] exit@L{K:2d}: agree_vs_full={agree:.3f}  f1={f1K:.3f}", flush=True)

    tol = 0.02
    by_k = {r["K"]: r for r in rows_out}
    # (a) STRICT: smallest K with >=99% verdict agreement AND F1 within tol (reproduces
    #     even the borderline flips the late layers make -- an upper bound on caution).
    k_verdict = min((r["K"] for r in rows_out
                     if r["verdict_agree_vs_full"] >= 0.99 and r["f1_vs_gold"] >= full_f1 - tol),
                    default=n_layers)
    # (b) F1-SATURATION (what "influences the OUTCOME" means): smallest K such that EVERY
    #     layer >= K keeps F1 within tol of full -- the depth beyond which no ER
    #     correctness is added (late layers only shuffle borderline verdicts).
    k_f1 = n_layers
    for K in range(k_min, n_layers + 1):
        if all(by_k[k2]["f1_vs_gold"] >= full_f1 - tol for k2 in range(K, n_layers + 1)):
            k_f1 = K
            break
    strip_v, strip_f1 = n_layers - k_verdict, n_layers - k_f1
    print(f"[strip] STRICT verdict-agreement K*={k_verdict} -> strip {strip_v}/{n_layers} "
          f"(~{strip_v / n_layers * 100:.0f}%)", flush=True)
    print(f"[strip] F1-SATURATION K*={k_f1} -> the last {strip_f1}/{n_layers} layers "
          f"(~{strip_f1 / n_layers * 100:.0f}% of block params) add NO ER F1 "
          f"(full {full_f1:.3f}, exit@L{k_f1} {by_k[k_f1]['f1_vs_gold']:.3f}, "
          f"verdict-agree {by_k[k_f1]['verdict_agree_vs_full']:.3f}). Logit-lens = a "
          f"no-retrain LOWER bound on strippability; confirm via truncate-and-adapt.",
          flush=True)

    payload = {
        "n_layers": n_layers, "n_pairs": len(pairs), "full_f1": full_f1, "tol": tol,
        "k_star_verdict_agreement": k_verdict, "strippable_layers_verdict": strip_v,
        "k_star_f1_saturation": k_f1, "strippable_layers_f1": strip_f1,
        "strippable_block_param_fraction_f1": strip_f1 / n_layers,
        "method_caveat": "logit-lens (reuses the final untrained readout on mid-layer "
                         "residuals) -> a no-retrain LOWER bound; truncate-and-adapt "
                         "would confirm/extend",
        "sweep": rows_out,
    }
    os.makedirs("/out/interp", exist_ok=True)
    with open("/out/interp/layer_early_exit.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print("[done] layer_early_exit -> /out/interp/layer_early_exit.json")


# --------------------------------------------------------------------------- #
# stage 6: truncate-and-adapt -- how few layers survive a TRAINED readout      #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=90 * 60, volumes={"/out": _out_vol})
def truncate_adapt(per_class: int = 600, seed: int = 0,
                   ks: str = "8,10,12,14,16,18,21,24,28") -> None:
    """Turn the layer early-exit LOWER bound into a real number: truncate the model
    at layer K (keep layers 0..K-1) and train a FRESH readout on the layer-K
    decision-token residual, instead of reusing the untrained final head. A frozen
    truncated backbone + a trained linear match head = the cheapest valid adaptation
    (a full LoRA-SFT could only do better). Record-DISJOINT train/test split (by
    cluster parity) so the head can't memorize. Reports F1(K) vs the full-backbone
    linear head F1(28) -> the smallest K that preserves ER F1 is the truly
    strippable depth WITH adaptation."""
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")
    import jellyfish
    import polars as pl
    import pyarrow.parquet as pq
    from decision_geometry import mine_probe_pairs
    from sklearn.linear_model import LogisticRegression

    tok, model = _load_model()
    n_layers = model.config.num_hidden_layers
    k_list = [int(k) for k in ks.split(",") if int(k) <= n_layers]

    raw = pl.from_arrow(pq.read_table(DATA))
    gold = raw["cluster"].to_list()
    rows = {i: {f: (raw[f][i] or "") for f in FIELDS} for i in range(len(gold))}
    surname_key = [jellyfish.soundex(str(raw["surname"][i] or "")) for i in range(len(gold))]

    # record-disjoint split: clusters alternate train/test so no record leaks
    by_cluster: dict = {}
    for i, g in enumerate(gold):
        by_cluster.setdefault(g, []).append(i)
    clusters = sorted(by_cluster)
    train_ids = {i for c in clusters[::2] for i in by_cluster[c]}
    test_ids = {i for c in clusters[1::2] for i in by_cluster[c]}

    pool = mine_probe_pairs(gold, surname_key, per_class * 3, negatives="hard", seed=seed)
    train_pairs = [(a, b, t) for a, b, t in pool if a in train_ids and b in train_ids][
        : per_class * 2]
    test_pairs = [(a, b, t) for a, b, t in pool if a in test_ids and b in test_ids][
        : per_class * 2]
    print(f"[trunc] train={len(train_pairs)} test={len(test_pairs)} pairs "
          f"(record-disjoint), layers={n_layers}", flush=True)

    def reps_for(pairs):
        prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]
        y = np.array([t for *_, t in pairs])
        per_k = {K: [] for K in k_list}
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(model.device)
            with torch.no_grad():
                out = model(**enc, output_hidden_states=True)
            for K in k_list:
                per_k[K].append(out.hidden_states[K][:, -1, :].float().cpu().numpy())
        return {K: np.concatenate(v, 0) for K, v in per_k.items()}, y

    Xtr, ytr = reps_for(train_pairs)
    Xte, yte = reps_for(test_pairs)

    sweep = []
    for K in k_list:
        clf = LogisticRegression(max_iter=2000, C=0.5).fit(
            (Xtr[K] - Xtr[K].mean(0)) / (Xtr[K].std(0) + 1e-8), ytr)
        pred = clf.predict((Xte[K] - Xtr[K].mean(0)) / (Xtr[K].std(0) + 1e-8))
        f1 = _prf_from_pred(pred.astype(bool), yte)
        sweep.append({"K": K, "f1_trained_readout": f1})
        print(f"[trunc] truncate@L{K:2d} + trained head: F1={f1:.3f}", flush=True)

    full = next(r["f1_trained_readout"] for r in sweep if r["K"] == n_layers)
    tol = 0.02
    k_star = min((r["K"] for r in sweep if r["f1_trained_readout"] >= full - tol),
                 default=n_layers)
    strip = n_layers - k_star
    print(f"[trunc] full-backbone linear head F1={full:.3f}; earliest K*={k_star} within "
          f"{tol} -> strip {strip}/{n_layers} layers (~{strip / n_layers * 100:.0f}% of "
          f"block params) with ER F1 preserved AFTER adaptation.", flush=True)

    payload = {
        "n_layers": n_layers, "train_pairs": len(train_pairs), "test_pairs": len(test_pairs),
        "full_backbone_linear_f1": full, "k_star": k_star, "strippable_layers": strip,
        "strippable_block_param_fraction": strip / n_layers, "tol": tol,
        "note": "record-disjoint split; frozen truncated backbone + trained linear "
                "readout (cheapest valid adaptation); in-distribution historical_50k. "
                "Held-out product-domain (walmart) generalization is the next step.",
        "sweep": sweep,
    }
    os.makedirs("/out/interp", exist_ok=True)
    with open("/out/interp/truncate_adapt.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print("[done] truncate_adapt -> /out/interp/truncate_adapt.json")


# --------------------------------------------------------------------------- #
# stage 3: causal validation -- steering / ablation (THE LOCK)                #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def causal_validate(layer: int = 14, lo: int = 8, hi: int = 20, per_class: int = 150,
                    seed: int = 0, coeffs: str = "-4,-2,-1,0,1,2,4",
                    n_sae_features: int = 5) -> None:
    """MULTI-LAYER causal test (the lock). The match direction is redundantly
    encoded across depth (present from layer 1), so a single-site intervention is
    re-derived downstream -- an earlier single-layer run barely moved the verdict.
    This steers/ablates the PER-LAYER diff-of-means direction at the decision token
    across a WINDOW of layers [lo, hi] at once, in natural gap-units (c=1 adds
    exactly the class-mean difference at each layer). The direction is CAUSAL iff
    P(match) moves monotonically with c and ablation-across-the-window collapses the
    decision toward chance. SAE feature directions (trained at ``layer``) are also
    tested SINGLE-LAYER as a secondary, redundancy-limited probe."""
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")

    tok, model = _load_model()
    true_id, false_id = _true_false_ids(tok)
    dev = model.device
    cvals = [float(c) for c in coeffs.split(",")]
    window = list(range(lo, hi + 1))

    pairs, rows = _mine(per_class, "hard", seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    def batched_logits_pmatch() -> float:
        ps = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            pair = torch.softmax(torch.stack([logits[:, true_id], logits[:, false_id]], 1), dim=1)
            ps.append(pair[:, 0].float().cpu().numpy())
        return float(np.concatenate(ps).mean())

    # one capture pass -> per-layer decision-token residual for the whole window
    accum: dict[int, list] = {L: [] for L in window}
    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
        with torch.no_grad():
            out = model(**enc, output_hidden_states=True)
        for L in window:
            accum[L].append(out.hidden_states[L][:, -1, :].float().cpu().numpy())
    layer_dirs: dict[int, tuple] = {}  # hidden-idx L -> (unit_dir fp16 tensor, gap)
    for L in window:
        R = np.concatenate(accum[L], axis=0)
        d = R[y == 1].mean(0) - R[y == 0].mean(0)
        g = float(np.linalg.norm(d))
        layer_dirs[L] = (torch.tensor(d / (g + 1e-9), dtype=torch.float16, device=dev), g)
    print(f"[causal] window={lo}..{hi}  per-layer class-gap norms: "
          f"{ {L: round(layer_dirs[L][1], 2) for L in window} }", flush=True)

    # multi-layer hooks: hidden_states[L] is the output of decoder block L-1.
    ctrl: dict = {"mode": None, "coeff": 0.0, "single": None}

    def make_hook(L: int):
        def hook(_m, _i, output):
            if ctrl["single"] is not None and ctrl["single"] != L:
                return output
            # read the direction/gap at CALL time (not registration) so the SAE
            # secondary can repoint layer_dirs[L] to a feature direction per test.
            u, g = layer_dirs[L]
            hidden = output[0] if isinstance(output, tuple) else output
            if ctrl["mode"] == "ablate":
                coeff = (hidden[:, -1, :] * u).sum(-1, keepdim=True)
                hidden[:, -1, :] = hidden[:, -1, :] - coeff * u
            elif ctrl["mode"] == "steer":
                hidden[:, -1, :] = hidden[:, -1, :] + ctrl["coeff"] * g * u
            return (hidden, *output[1:]) if isinstance(output, tuple) else hidden

        return hook

    hooks = [model.model.layers[L - 1].register_forward_hook(make_hook(L)) for L in window]

    def measure(mode=None, coeff=0.0, single=None) -> float:
        ctrl["mode"], ctrl["coeff"], ctrl["single"] = mode, coeff, single
        v = batched_logits_pmatch()
        ctrl["mode"], ctrl["coeff"], ctrl["single"] = None, 0.0, None
        return v

    base = measure()
    print(f"[causal] baseline mean P(match) = {base:.4f}", flush=True)

    # PRIMARY: multi-layer diff-of-means steer + ablate across the window
    sweep = {c: measure("steer", coeff=c) for c in cvals}
    ablated_all = measure("ablate")
    mono = _monotonic([sweep[c] for c in sorted(cvals)])
    print(f"[causal] diff_of_means[MULTI {lo}..{hi}]: swing "
          f"{sweep[min(cvals)]:.3f}->{sweep[max(cvals)]:.3f} monotonic={mono}  "
          f"ablated_all={ablated_all:.3f} (base {base:.3f})", flush=True)

    out: dict = {
        "layer_window": [lo, hi], "sae_layer": layer, "baseline_p_match": base,
        "coeffs": cvals, "coeff_units": "multiples of per-layer class-gap",
        "per_layer_gap_norm": {L: layer_dirs[L][1] for L in window},
        "diff_of_means_multilayer": {
            "sweep": {str(c): sweep[c] for c in cvals}, "monotonic": mono,
            "delta_full_swing": sweep[max(cvals)] - sweep[min(cvals)],
            "ablated_all_p_match": ablated_all, "ablation_delta": ablated_all - base,
        },
        "sae_features_singlelayer": {},
    }

    # SECONDARY: SAE feature directions, steered SINGLE-LAYER at `layer` only
    # (they are trained there; single-site => redundancy-limited, reported as such)
    sae_path = f"/out/interp/sae_layer{layer}.pt"
    if os.path.exists(sae_path) and n_sae_features > 0 and layer in layer_dirs:
        sae = torch.load(sae_path, map_location="cpu")
        with open(f"/out/interp/sae_features_layer{layer}.json", encoding="utf-8") as fh:
            top = json.load(fh)["top_features"][:n_sae_features]
        gap_l = layer_dirs[layer][1]
        for f in top:
            j = f["feature"]
            wj = sae["W_dec"][j].numpy()
            u = torch.tensor(wj / (np.linalg.norm(wj) + 1e-9), dtype=torch.float16, device=dev)
            # temporarily repoint this layer's hook direction to the feature dir
            layer_dirs[layer] = (u, gap_l)
            sw = {c: measure("steer", coeff=c, single=layer) for c in cvals}
            abl = measure("ablate", single=layer)
            out["sae_features_singlelayer"][f"feat_{j}"] = {
                "match_corr": f["match_corr"],
                "sweep": {str(c): sw[c] for c in cvals},
                "monotonic": _monotonic([sw[c] for c in sorted(cvals)]),
                "ablated_p_match": abl,
            }
            print(f"[causal] sae_feat_{j}[SINGLE L{layer}] swing "
                  f"{sw[min(cvals)]:.3f}->{sw[max(cvals)]:.3f} corr={f['match_corr']:.2f}",
                  flush=True)

    for h in hooks:
        h.remove()
    os.makedirs("/out/interp", exist_ok=True)
    with open(f"/out/interp/causal_multilayer_{lo}_{hi}.json", "w") as fh:
        json.dump(out, fh, indent=2)
    _out_vol.commit()
    print(f"[done] causal -> /out/interp/causal_multilayer_{lo}_{hi}.json")


def _monotonic(vals: list[float], tol: float = 1e-3) -> bool:
    inc = all(vals[i + 1] >= vals[i] - tol for i in range(len(vals) - 1))
    dec = all(vals[i + 1] <= vals[i] + tol for i in range(len(vals) - 1))
    return inc or dec


# --------------------------------------------------------------------------- #
# local entrypoints                                                           #
# --------------------------------------------------------------------------- #
@app.local_entrypoint()
def probe_layers(per_class: int = 200, negatives: str = "hard", seed: int = 0) -> None:
    capture_probe_layers.remote(per_class=per_class, negatives=negatives, seed=seed)
    print("done -> `modal volume get er-matcher-out interp/layer_probes.json`")


# --------------------------------------------------------------------------- #
# stage 4 (LAYER 2): abstract the locked direction into human field signals    #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def layer2_abstraction(layer: int = 14, per_class: int = 400, seed: int = 0,
                       n_sae_features: int = 12) -> None:
    """Translate the causally-validated match direction at ``layer`` into
    human-readable field signals: decompose the projection onto field-agreement
    features, and label the top SAE features by the field they track. See
    ``field_attribution.py`` (the pure, tested logic) for the method."""
    import json
    import os
    import sys

    import numpy as np
    import torch
    import torch.nn.functional as F

    sys.path.insert(0, "/root/interp")
    sys.path.insert(0, "/root")
    from field_attribution import (
        attribute_direction,
        attribute_direction_grouped,
        field_agreements,
        field_rollup,
        label_sae_features,
        richer_field_features,
    )
    from goldenmatch.core.er_matcher.explainer import (
        FIELD_SIGNAL_NAMES,
        field_agreement,
        field_signal_vector,
    )

    tok, model = _load_model()
    pairs, rows = _mine(per_class, "hard", seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) for a, b, _ in pairs]

    # decision-token residual at `layer`
    reps = []
    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            out = model(**enc, output_hidden_states=True)
        reps.append(out.hidden_states[layer][:, -1, :].float().cpu().numpy())
    R = np.concatenate(reps, axis=0)

    # the proven match direction (diff-of-means) + each pair's projection onto it
    d = R[y == 1].mean(0) - R[y == 0].mean(0)
    d = d / (np.linalg.norm(d) + 1e-9)
    projections = R @ d

    # human field-agreement signals (the primitives to translate INTO)
    field_feats = field_agreements(rows, pairs, FIELDS, agreement=field_agreement)
    decomp = attribute_direction(projections, field_feats, FIELDS)
    print(f"[layer2] direction R^2 by field signals = {decomp['r2']:.3f}")
    for e in decomp["ranking"]:
        print(f"[layer2]   {e['field']:<14} coef={e['coef']:+.3f}", flush=True)

    # SAME derivation, RICHER basis: regress the proven direction onto the six
    # shipped per-field signals instead of one agreement scalar. Provenance is
    # identical to the table above (the causally-validated direction), so the
    # resulting weights are comparable in kind -- just finer-grained.
    X, sig_names = richer_field_features(
        rows, pairs, FIELDS, signal_fn=field_signal_vector,
        signal_names=FIELD_SIGNAL_NAMES,
    )
    rich = attribute_direction(projections, X, sig_names)
    print(f"[layer2] direction R^2 by RICHER signals = {rich['r2']:.3f} "
          f"({len(sig_names)} signals)", flush=True)
    for e in rich["ranking"][:10]:
        print(f"[layer2]   {e['field']:<26} coef={e['coef']:+.3f}", flush=True)
    print(f"[layer2] dense rollup: "
          f"{[f for f, _ in field_rollup(rich['coefficients'])]}", flush=True)

    # Does SPARSITY buy back readability? The dense 36-signal fit scores well but
    # ranks fields backwards (collinearity spreads weight over redundant signals).
    # L1 should drop the redundant ones; the test is whether the per-field rollup
    # starts agreeing with what ablation says the model needs.
    grouped = attribute_direction_grouped(projections, X, sig_names, FIELDS)
    print(f"[layer2] GROUPED (two-stage) R^2={grouped['r2']:.3f} "
          f"rollup={[e['field'] for e in grouped['ranking']]}", flush=True)
    for e in grouped["ranking"]:
        print(f"[layer2]   {e['field']:<14} a={e['coef']:+.3f}", flush=True)

    sparse_fits = {}
    for alpha in (0.005, 0.01, 0.02, 0.05, 0.1):
        sp = attribute_direction(projections, X, sig_names, l1_alpha=alpha)
        roll = [f for f, _ in field_rollup(sp["coefficients"])]
        sparse_fits[str(alpha)] = {**sp, "rollup": roll}
        print(f"[layer2] L1 a={alpha:<6} R^2={sp['r2']:.3f} "
              f"nnz={sp['n_nonzero']:>2}/{len(sig_names)} rollup={roll}", flush=True)

    # label the top SAE features by the field their activation tracks
    sae_labels = []
    sae_path = f"/out/interp/sae_layer{layer}.pt"
    if os.path.exists(sae_path):
        sae = torch.load(sae_path, map_location="cpu")
        with open(f"/out/interp/sae_features_layer{layer}.json", encoding="utf-8") as fh:
            top = json.load(fh)["top_features"][:n_sae_features]
        dev = model.device
        ns = float(sae.get("norm_scale", 1.0))
        Dn = torch.tensor(R / ns, dtype=torch.float32, device=dev)  # SAE trained normalized
        W_enc = sae["W_enc"].to(dev)
        b_enc = sae["b_enc"].to(dev)
        b_dec = sae["b_dec"].to(dev)
        cols = [f["feature"] for f in top]
        with torch.no_grad():
            Z = F.relu((Dn - b_dec) @ W_enc + b_enc)[:, cols].cpu().numpy()
        labels = label_sae_features(Z, field_feats, FIELDS)
        for f, lab in zip(top, labels):
            lab["feature"] = f["feature"]
            lab["match_corr"] = f["match_corr"]
            sae_labels.append(lab)
            print(f"[layer2] SAE feat {f['feature']:5d} (match_corr {f['match_corr']:+.2f}) "
                  f"-> tracks {lab['top_field']} (r={lab['corr']:+.2f})", flush=True)

    payload = {
        "layer": layer, "n_pairs": len(pairs), "fields": FIELDS,
        "direction_field_decomposition": decomp,
        "direction_richer_decomposition": rich,
        "direction_richer_sparse": sparse_fits,
        "direction_richer_grouped": grouped,
        "richer_signal_names": sig_names,
        "sae_feature_labels": sae_labels,
    }
    os.makedirs("/out/interp", exist_ok=True)
    with open(f"/out/interp/layer2_abstraction_L{layer}.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] layer2 -> /out/interp/layer2_abstraction_L{layer}.json")


# --------------------------------------------------------------------------- #
# stage 7: faithfulness -- how much of the model's ACTUAL verdict do the        #
#          per-field weights explain? (the shipped explainer's honesty number)  #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def faithfulness_eval(per_class: int = 400, seed: int = 0, negatives: str = "hard",
                      split: str = "cluster", link: str = "linear",
                      dataset: str = "person", corruption_matched: bool = False) -> None:
    """Measure the per-field explanation against the model's ACTUAL P(match).

    The Layer-2 number (``layer2_abstraction``, R^2 ~= 0.51) regresses onto the
    diff-of-means PROJECTION -- a lossy 1-D shadow of the ~8-D decision. For a
    per-decision *explainer* the right target is the model's own verdict
    probability. This stage measures that, on a CLUSTER-DISJOINT test split
    (same recipe as ``leniency_dial``, so no entity is shared across the split):

      - ``fixed``  -- the SHIPPED ``PERSON_FIELD_IMPORTANCE`` weights, FROZEN;
                      only an intercept+scale link is fit on train. This is the
                      number the shipped explainer may honestly cite.
      - ``simple`` -- the same 6 agreement features, refit on train (a ceiling
                      for ``fixed``: same basis, free weights).
      - ``richer`` -- 36 features (agreement/exact/missing/conflict/len/edit).
      - ``gbm``    -- gradient boosting on the richer features (upper bound,
                      least legible).

    The gap between ``fixed`` and ``simple`` is the cost of freezing the weights;
    the gap between ``simple`` and ``gbm`` is the legibility/faithfulness
    frontier. -> interp/faithfulness.json
    """
    import sys

    import jellyfish
    import polars as pl
    import pyarrow.parquet as pq

    sys.path.insert(0, "/root/interp")
    sys.path.insert(0, "/root")
    from decision_geometry import mine_probe_pairs
    from field_attribution import (
        corruption_matched_pairs,
        record_disjoint_split,
    )

    tok, model = _load_model()
    true_id, false_id = _true_false_ids(tok)
    dev = model.device

    if dataset != "person":
        # messy domain: DeepMatcher's canonical train/test splits ARE the honest
        # split (pre-labeled pairs, no clusters to mine or leak across)
        tr, te, rows, fields = _load_product_pairs(dataset, limit=per_class * 2)
        split = "deepmatcher-train/test"
        print(f"[faith] dataset={dataset} train={len(tr)} test={len(te)} "
              f"fields={len(fields)} split={split} link={link}", flush=True)
        _faithfulness_core(
            tok, model, dev, true_id, false_id, tr, te, rows, fields,
            dataset=dataset, split=split, negatives="n/a", seed=seed, link=link,
        )
        return

    fields = FIELDS
    raw = pl.from_arrow(pq.read_table(DATA))
    gold = raw["cluster"].to_list()
    rows = {i: {f: (raw[f][i] or "") for f in fields} for i in range(len(gold))}
    surname_key = [jellyfish.soundex(str(raw["surname"][i] or "")) for i in range(len(gold))]

    pool = mine_probe_pairs(gold, surname_key, per_class * 3, negatives=negatives, seed=seed)
    if split == "cluster":
        # no ENTITY (hence no record) is shared train<->test -- the honest split
        by_cluster: dict = {}
        for i, g in enumerate(gold):
            by_cluster.setdefault(g, []).append(i)
        clusters = sorted(by_cluster)
        train_ids = {i for c in clusters[::2] for i in by_cluster[c]}
        test_ids = {i for c in clusters[1::2] for i in by_cluster[c]}
        tr = [(a, b, t) for a, b, t in pool if a in train_ids and b in train_ids]
        te = [(a, b, t) for a, b, t in pool if a in test_ids and b in test_ids]
    elif split == "record":
        # WEAKER: records disjoint but the same ENTITY can straddle the split.
        # Kept to quantify how much that leak inflates the number.
        tr_i, te_i = record_disjoint_split(pool, seed=seed)
        tr = [pool[i] for i in tr_i]
        te = [pool[i] for i in te_i]
    else:
        raise ValueError(f"split must be 'cluster' or 'record', got {split!r}")
    if corruption_matched:
        # Strip the corruption shortcut: probe matches are corrupted copies of one
        # entity and non-matches are different entities, so mean edit_norm alone
        # correlates ~-0.90 with the label and a wide fit can score well without
        # using field-specific evidence. Pair each match with a non-match at the
        # same corruption level so that channel carries no label information.
        sys.path.insert(0, "/root")
        from goldenmatch.core.er_matcher.explainer import field_signal_vector as _fsv

        tr, dtr = corruption_matched_pairs(rows, tr, fields, signal_fn=_fsv)
        te, dte = corruption_matched_pairs(rows, te, fields, signal_fn=_fsv)
        print(f"[faith] corruption-matched: train {int(dtr['n_in'])}->{int(dtr['n_out'])} "
              f"(corr {dtr['corr_before']:+.3f}->{dtr['corr_after']:+.3f})  "
              f"test {int(dte['n_in'])}->{int(dte['n_out'])} "
              f"(corr {dte['corr_before']:+.3f}->{dte['corr_after']:+.3f})", flush=True)
    tr, te = tr[: per_class * 2], te[: per_class * 2]
    if not tr or not te:
        raise RuntimeError(f"empty split: train={len(tr)} test={len(te)}")
    print(f"[faith] train={len(tr)} test={len(te)} negatives={negatives} "
          f"split={split}-disjoint link={link}", flush=True)

    _faithfulness_core(
        tok, model, dev, true_id, false_id, tr, te, rows, fields,
        dataset=dataset,
        split=f"{split}-disjoint" + ("-corrmatched" if corruption_matched else ""),
        negatives=negatives, seed=seed, link=link,
    )


def _faithfulness_core(tok, model, dev, true_id, false_id, tr, te, rows, fields,
                       *, dataset: str, split: str, negatives: str, seed: int,
                       link: str) -> None:
    """Score both splits, fit the four bases, write the artifact.

    Shared by the person and product paths so the two datasets are measured by
    identical code -- only how the pairs were obtained differs.
    """
    import json
    import os
    import sys

    import numpy as np
    import torch
    sys.path.insert(0, "/root/interp")
    sys.path.insert(0, "/root")
    from field_attribution import (
        affine_r2,
        field_agreements,
        fixed_weight_score,
        logit,
        prob_space_r2,
        richer_field_features,
    )
    from goldenmatch.core.er_matcher.explainer import (
        FIELD_SIGNAL_NAMES,
        PERSON_FIELD_IMPORTANCE,
        PERSON_SIGNAL_IMPORTANCE,
        PERSON_SIGNAL_IMPORTANCE_DENSE,
        field_agreement,
        field_signal_vector,
    )

    def p_match(pairs) -> np.ndarray:
        """Teacher-forced readout: feed the '{"match":' prefix, softmax the
        true/false logits. Verdict-identical to generation, and continuous."""
        prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]
        ps = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            pair = torch.softmax(torch.stack([logits[:, true_id], logits[:, false_id]], 1), 1)
            ps.append(pair[:, 0].float().cpu().numpy())
        return np.concatenate(ps)

    p_tr, p_te = p_match(tr), p_match(te)
    y_te = np.array([t for *_, t in te])
    acc = float(((p_te >= 0.5) == (y_te == 1)).mean())
    print(f"[faith] P(match) test: mean={p_te.mean():.3f} "
          f"frac<0.1={float((p_te < 0.1).mean()):.2f} "
          f"frac>0.9={float((p_te > 0.9).mean()):.2f} acc={acc:.3f}", flush=True)

    # pass the SHIPPED agreement function so `simple` measures the product's
    # actual basis, not a lookalike that could drift from it
    A_tr = field_agreements(rows, tr, fields, agreement=field_agreement)
    A_te = field_agreements(rows, te, fields, agreement=field_agreement)
    X_tr, feat_names = richer_field_features(
        rows, tr, fields, signal_fn=field_signal_vector, signal_names=FIELD_SIGNAL_NAMES
    )
    X_te, _ = richer_field_features(
        rows, te, fields, signal_fn=field_signal_vector, signal_names=FIELD_SIGNAL_NAMES
    )

    results: dict = {}

    # The shipped weight table is person-only; on a product schema there are no
    # learned weights to freeze, so the `fixed` row is skipped rather than faked.
    if dataset == "person":
        fixed = affine_r2(
            fixed_weight_score(A_tr, fields, PERSON_FIELD_IMPORTANCE), p_tr,
            fixed_weight_score(A_te, fields, PERSON_FIELD_IMPORTANCE), p_te,
            link=link,
        )
        results["fixed"] = {**fixed, "n_features": len(fields), "weights_refit": False}
        print(f"[faith] fixed  (shipped weights, frozen) "
              f"R^2_test={fixed['r2_test']:.3f}", flush=True)

        # Same question on the RICHER basis: freeze the direction-derived 36-signal
        # weights and see whether the finer grain survives as OUTPUT faithfulness.
        # Raw dot product, exactly like `fixed`, so the two are comparable.
        fixed_rich = affine_r2(
            fixed_weight_score(X_tr, feat_names, PERSON_SIGNAL_IMPORTANCE_DENSE), p_tr,
            fixed_weight_score(X_te, feat_names, PERSON_SIGNAL_IMPORTANCE_DENSE), p_te,
            link=link,
        )
        results["fixed_richer"] = {
            **fixed_rich, "n_features": len(PERSON_SIGNAL_IMPORTANCE_DENSE), "weights_refit": False,
        }
        print(f"[faith] fixed_richer (36 frozen signal weights) "
              f"R^2_test={fixed_rich['r2_test']:.3f}", flush=True)

        # The readable variant: L1-sparse (14/36) weights, frozen. If this holds
        # most of fixed_richer's gain, the two explainer modes collapse into one.
        fixed_sparse = affine_r2(
            fixed_weight_score(X_tr, feat_names, PERSON_SIGNAL_IMPORTANCE), p_tr,
            fixed_weight_score(X_te, feat_names, PERSON_SIGNAL_IMPORTANCE), p_te,
            link=link,
        )
        results["fixed_sparse"] = {
            **fixed_sparse, "n_features": len(PERSON_SIGNAL_IMPORTANCE),
            "weights_refit": False,
        }
        print(f"[faith] fixed_sparse (14 frozen L1 weights) "
              f"R^2_test={fixed_sparse['r2_test']:.3f}", flush=True)

    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.linear_model import LinearRegression

    # With link="logit" every basis is fit against log-odds and scored back in
    # PROBABILITY space, so the numbers stay comparable to the linear-link table.
    t_tr = logit(p_tr) if link == "logit" else p_tr

    def _fit_r2(model_obj, Xa, Xb, name):
        model_obj.fit(Xa, t_tr)
        if link == "logit":
            r2_te = prob_space_r2(model_obj.predict(Xb), p_te)
            r2_tr = prob_space_r2(model_obj.predict(Xa), p_tr)
        else:
            r2_te, r2_tr = float(model_obj.score(Xb, p_te)), float(model_obj.score(Xa, p_tr))
        print(f"[faith] {name:<7} R^2_test={r2_te:.3f} (train {r2_tr:.3f})", flush=True)
        return {"r2_test": r2_te, "r2_train": r2_tr, "link": link,
                "n_features": Xa.shape[1], "weights_refit": True}

    results["simple"] = _fit_r2(LinearRegression(), A_tr, A_te, "simple")
    results["richer"] = _fit_r2(LinearRegression(), X_tr, X_te, "richer")
    results["gbm"] = _fit_r2(GradientBoostingRegressor(random_state=0), X_tr, X_te, "gbm")

    payload = {
        "target": "model P(match) via teacher-forced true/false logit readout",
        "dataset": dataset, "split": split,
        "negatives": negatives, "link": link,
        "n_train": len(tr), "n_test": len(te), "seed": seed,
        "test_accuracy": acc,
        "fields": fields, "richer_feature_names": feat_names,
        "shipped_weights": ({f: PERSON_FIELD_IMPORTANCE.get(f, 0.0) for f in fields}
                            if dataset == "person" else None),
        "p_match_test": {
            "mean": float(p_te.mean()), "std": float(p_te.std()),
            "frac_below_0.1": float((p_te < 0.1).mean()),
            "frac_above_0.9": float((p_te > 0.9).mean()),
        },
        "results": results,
    }
    os.makedirs("/out/interp", exist_ok=True)
    suffix = "" if link == "linear" else f"_{link}"
    tag = negatives if dataset == "person" else dataset
    out_path = f"/out/interp/faithfulness_{split.replace(chr(47), chr(45))}_{tag}_seed{seed}{suffix}.json"
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] faithfulness -> {out_path}")



# --------------------------------------------------------------------------- #
# stage 8: per-pair CAUSAL attribution -- ablate a field, watch the verdict     #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def causal_attribution(per_class: int = 200, seed: int = 0, negatives: str = "hard",
                       threshold: float = 0.5, dataset: str = "person",
                       max_order: int = 1) -> None:
    """Per-pair CAUSAL field attribution by occlusion -- not another R^2.

    For each pair and each field, blank that field on BOTH records and re-score.
    The prompt renders an absent value as the ``(missing)`` sentinel and the
    system rubric explicitly trains the model to treat a missing field as "ignore,
    do not penalize", so this removes the evidence *in-distribution* rather than
    poking the model off-manifold.

    Two interventions per field:
      - **necessity** (leave-one-out): remove field f, keep the rest. A large
        |delta P(match)| means the model's verdict genuinely leans on f.
      - **sufficiency** (leave-one-in): keep ONLY f, blank the rest. Says whether
        f alone carries the decision.

    The headline is the **flip rate**: the fraction of pairs whose verdict
    actually crosses ``threshold`` when a field is removed. That is a direct
    interventional claim about a real decision ("removing birth_place changes the
    verdict in N% of cases") rather than a variance-explained statistic -- which
    is what an auditor is actually asking for, and it does not inherit the
    faithfulness R^2's weakness on this domain.

    Also reports the Spearman correlation between the causal ranking and the
    shipped ``PERSON_FIELD_IMPORTANCE`` -- an INDEPENDENT check on the explainer's
    weights, since those were derived from the residual-stream geometry and this
    is measured from the model's output behaviour.
    -> interp/causal_attribution_{negatives}_seed{seed}.json
    """
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")
    sys.path.insert(0, "/root")
    from field_attribution import ablation_flip_profile, attribution_summary
    from goldenmatch.core.er_matcher.explainer import PERSON_FIELD_IMPORTANCE

    tok, model = _load_model()
    true_id, false_id = _true_false_ids(tok)
    dev = model.device
    if dataset == "person":
        pairs, rows = _mine(per_class, negatives, seed)
        fields = FIELDS
        regime = f"negatives={negatives}"
    else:
        # messy domain: DeepMatcher's own test split, no negative mining
        _tr, pairs, rows, fields = _load_product_pairs(dataset, limit=per_class * 2)
        regime = "deepmatcher test split"
    print(f"[attr] dataset={dataset} pairs={len(pairs)} fields={len(fields)} "
          f"({regime})", flush=True)

    def p_match(recs: list[tuple[dict, dict]]) -> np.ndarray:
        prompts = [_prompt(tok, a, b) + '{"match":' for a, b in recs]
        ps = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            pair = torch.softmax(torch.stack([logits[:, true_id], logits[:, false_id]], 1), 1)
            ps.append(pair[:, 0].float().cpu().numpy())
        return np.concatenate(ps)

    def blanked(r: dict, drop: set) -> dict:
        """Copy of ``r`` with ``drop`` fields emptied -> renders as ``(missing)``."""
        return {f: ("" if f in drop else r.get(f, "")) for f in fields}

    base = p_match([(rows[a], rows[b]) for a, b, _ in pairs])
    y = np.array([t for *_, t in pairs])
    print(f"[attr] base P(match): mean={base.mean():.3f} "
          f"acc={float(((base >= threshold) == (y == 1)).mean()):.3f}", flush=True)

    nec = np.zeros((len(pairs), len(fields)))
    suf = np.zeros((len(pairs), len(fields)))
    for j, f in enumerate(fields):
        others = set(fields) - {f}
        nec[:, j] = p_match([(blanked(rows[a], {f}), blanked(rows[b], {f})) for a, b, _ in pairs])
        suf[:, j] = p_match(
            [(blanked(rows[a], others), blanked(rows[b], others)) for a, b, _ in pairs]
        )
        print(f"[attr] {f:<14} necessity dP={float((base - nec[:, j]).mean()):+.3f} "
              f"flip={float(((nec[:, j] >= threshold) != (base >= threshold)).mean()):.3f}",
              flush=True)

    # the shipped weight table is person-only; on a product schema there is
    # nothing to correlate against, so skip the comparison rather than fake it
    w = PERSON_FIELD_IMPORTANCE if dataset == "person" else None
    necessity = attribution_summary(base, nec, fields, weights=w, threshold=threshold)
    sufficiency = attribution_summary(base, suf, fields, threshold=threshold)
    print(f"[attr] causal ranking: {necessity['ranking']}", flush=True)
    if "spearman_vs_learned_weights" in necessity:
        print(f"[attr] spearman vs shipped weights = "
              f"{necessity['spearman_vs_learned_weights']:+.3f}", flush=True)
    print(f"[attr] any-field flip rate = {necessity['any_flip_rate']:.3f}", flush=True)

    # MULTI-FIELD ablation: single-field occlusion says "no ONE field decides this"
    # for ~81% of pairs. The sharper question is whether any PAIR or TRIPLE does --
    # i.e. whether the decision is decomposable but not 1-sparse (explain it in
    # pairs) or densely redundant (no small-set attribution exists at all).
    flip_profile = None
    if max_order > 1:
        from itertools import combinations

        combos: list[tuple] = []
        for k in range(1, max_order + 1):
            combos.extend(combinations(fields, k))
        print(f"[attr] multi-field sweep: {len(combos)} combos up to order "
              f"{max_order} x {len(pairs)} pairs", flush=True)
        multi = np.zeros((len(pairs), len(combos)))
        for j, combo in enumerate(combos):
            drop = set(combo)
            multi[:, j] = p_match(
                [(blanked(rows[a], drop), blanked(rows[b], drop)) for a, b, _ in pairs]
            )
            if j % 10 == 0:
                print(f"[attr]   {j + 1}/{len(combos)}", flush=True)
        flip_profile = ablation_flip_profile(
            base, [tuple(c) for c in combos], multi, threshold=threshold
        )
        for k, e in sorted(flip_profile["by_order"].items()):
            print(f"[attr] order {k}: any-flip {e['any_flip_rate']:.3f}  "
                  f"mean {e['mean_flip_rate']:.3f}  max {e['max_flip_rate']:.3f}  "
                  f"best={e['best_combo']}", flush=True)
        print(f"[attr] cumulative flippable by <=k: "
              f"{ {k: round(v, 3) for k, v in flip_profile['cumulative_flippable'].items()} }",
              flush=True)
        print(f"[attr] never flipped at any order: "
              f"{flip_profile['never_flipped_frac']:.3f}", flush=True)

    payload = {
        "method": "occlusion (blank field on both records -> '(missing)' sentinel)",
        "max_order": max_order, "flip_profile": flip_profile,
        "dataset": dataset, "regime": regime,
        "negatives": negatives, "seed": seed, "threshold": threshold,
        "fields": fields,
        "shipped_weights": ({f: PERSON_FIELD_IMPORTANCE.get(f, 0.0) for f in fields}
                            if dataset == "person" else None),
        "base_p_mean": float(base.mean()),
        "base_accuracy": float(((base >= threshold) == (y == 1)).mean()),
        "necessity": necessity, "sufficiency": sufficiency,
        # per-pair rows so a review queue can cite the actual counterfactual
        "per_pair": [
            {"a": int(a), "b": int(b), "label": int(t), "p_base": float(base[i]),
             "p_without": {f: float(nec[i, j]) for j, f in enumerate(fields)}}
            for i, (a, b, t) in enumerate(pairs[:50])
        ],
    }
    os.makedirs("/out/interp", exist_ok=True)
    tag = negatives if dataset == "person" else dataset
    out_path = f"/out/interp/causal_attribution_{tag}_seed{seed}.json"
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] causal attribution -> {out_path}")


# --------------------------------------------------------------------------- #
# stage 9: EXACT direct attribution of the decision to model components         #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def direct_attribution(layer: int = 14, per_class: int = 200, seed: int = 0,
                       negatives: str = "hard") -> None:
    """Decompose each decision into EXACT per-component contributions.

    This is mechanistic rather than correlational, and the difference is the
    point. A transformer's residual stream is a SUM -- embeddings plus every
    attention head's output plus every MLP's output -- and the decision readout
    is a linear projection onto the causally-validated match direction. So each
    component's contribution to that projection is exactly computable, and the
    contributions must sum to the observed projection to floating-point error.

    Nothing here is fitted. There is no R^2, because there is no approximation:
    the reconstruction error is a CORRECTNESS CHECK on the decomposition, not a
    quality score. Every earlier per-field number in this thread estimated what
    the model might be doing; this one states it.

    Decomposes to per-layer MLP and per-ATTENTION-HEAD granularity (splitting
    ``o_proj`` by head, which is exact since it is linear and bias-free), because
    a circuit -- if there is one -- lives at head level.

    Caveat recorded in the artifact: these are DIRECT contributions to the
    readout. Indirect effects (one head changing another's attention pattern)
    are real causal paths that direct attribution assigns to the downstream
    component. Path patching is required for those; a decomposition that is exact
    on direct paths and silent on indirect ones is complete-looking and wrong.
    -> interp/direct_attribution_L{layer}.json
    """
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")
    from field_attribution import contribution_summary, variance_decomposition

    tok, model = _load_model()
    dev = model.device
    pairs, rows = _mine(per_class, negatives, seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    cfg = model.config
    n_heads = cfg.num_attention_heads
    head_dim = getattr(cfg, "head_dim", cfg.hidden_size // n_heads)
    print(f"[direct] layers={cfg.num_hidden_layers} heads={n_heads} "
          f"head_dim={head_dim} readout_layer={layer}", flush=True)

    def resid_at(layer_idx: int) -> np.ndarray:
        out = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                h = model(**enc, output_hidden_states=True).hidden_states[layer_idx]
            out.append(h[:, -1, :].float().cpu().numpy())
        return np.concatenate(out, 0)

    # pass 1: the causally-validated match direction (same recipe as layer2)
    R = resid_at(layer)
    d = R[y == 1].mean(0) - R[y == 0].mean(0)
    d = d / (np.linalg.norm(d) + 1e-9)
    actual = R @ d
    d_t = torch.tensor(d, dtype=torch.float32, device=dev)

    # pass 2: capture every additive term feeding the residual at `layer`
    names: list[str] = ["embed"]
    for L in range(layer):
        names += [f"L{L}.attn.h{h}" for h in range(n_heads)] + [f"L{L}.mlp"]
    contribs = np.zeros((len(prompts), len(names)), dtype=np.float64)

    store: dict[str, torch.Tensor] = {}
    hooks = []

    def mk_mlp_hook(L: int):
        def hook(_m, _i, out):
            store[f"L{L}.mlp"] = (out[0] if isinstance(out, tuple) else out)[:, -1, :]
        return hook

    def mk_oproj_hook(L: int):
        # o_proj INPUT is the concatenated per-head outputs; splitting the weight
        # by head gives each head's exact additive contribution to the residual.
        def hook(mod, inp, _out):
            x = inp[0][:, -1, :]  # (B, n_heads*head_dim)
            W = mod.weight  # (hidden, n_heads*head_dim)
            for h in range(n_heads):
                sl = slice(h * head_dim, (h + 1) * head_dim)
                store[f"L{L}.attn.h{h}"] = x[:, sl] @ W[:, sl].T
        return hook

    for L in range(layer):
        blk = model.model.layers[L]
        hooks.append(blk.mlp.register_forward_hook(mk_mlp_hook(L)))
        hooks.append(blk.self_attn.o_proj.register_forward_hook(mk_oproj_hook(L)))

    col = {n: j for j, n in enumerate(names)}
    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
        store.clear()
        with torch.no_grad():
            h0 = model(**enc, output_hidden_states=True).hidden_states[0][:, -1, :]
        n = h0.shape[0]
        contribs[i : i + n, col["embed"]] = (h0.float() @ d_t).cpu().numpy()
        for name, v in store.items():
            contribs[i : i + n, col[name]] = (v.float() @ d_t).cpu().numpy()
        if (i // 16) % 5 == 0:
            print(f"[direct] {i + n}/{len(prompts)}", flush=True)
    for hk in hooks:
        hk.remove()

    summary = contribution_summary(contribs, names, actual)
    var_dec = variance_decomposition(contribs, names, actual, labels=y)
    print(f"[direct] EXACTNESS: max_abs_err={summary['max_abs_err']:.3e} "
          f"rel={summary['rel_err']:.3e} exact={summary['exact']}", flush=True)
    if not summary["exact"]:
        print("[direct] WARNING: decomposition does NOT reconstruct the projection; "
              "the ranking below is meaningless until this is fixed", flush=True)
    print(f"[direct] concentration: {summary['concentration']}", flush=True)
    print(f"[direct] components for 90% of |contribution|: "
          f"{summary['n_for_90pct']} / {len(names)}", flush=True)
    # VARIANCE ranking -- the correct one. Magnitude ranks constant offsets top;
    # a component that never varies cannot move the decision. The shares are an
    # exact additive split of var(projection) and must sum to 1.
    print(f"[direct] VARIANCE SHARES sum={var_dec['shares_sum']:.6f} "
          f"exact={var_dec['shares_sum_exact']} "
          f"negative={var_dec['n_negative_share']}/{var_dec['n_components']}",
          flush=True)
    print(f"[direct] cumulative var share: {var_dec['cumulative']}", flush=True)
    print(f"[direct] components for 90% of DECISION VARIANCE: "
          f"{var_dec['n_for_90pct_variance']} / {var_dec['n_components']}", flush=True)
    for e in var_dec["ranking"][:20]:
        print(f"[direct]   {e['component']:<16} var_share={e['var_share']:+.4f} "
              f"mean={e['mean']:+.4f} label_r={e.get('label_corr', 0.0):+.3f}",
              flush=True)
    print("[direct] --- most SUPPRESSIVE (negative share) ---", flush=True)
    for e in var_dec["ranking"][-5:]:
        print(f"[direct]   {e['component']:<16} var_share={e['var_share']:+.4f} "
              f"label_r={e.get('label_corr', 0.0):+.3f}", flush=True)

    payload = {
        "method": f"exact direct attribution to the layer-{layer} match direction",
        "caveat": ("DIRECT contributions only -- indirect effects (a head changing "
                   "another head's attention pattern) are attributed to the "
                   "downstream component. Use path patching for those."),
        "layer": layer, "negatives": negatives, "seed": seed,
        "n_heads": n_heads, "head_dim": head_dim,
        "n_pairs": len(pairs), "summary": summary,
        "variance_decomposition": var_dec,
    }
    os.makedirs("/out/interp", exist_ok=True)
    out_path = f"/out/interp/direct_attribution_L{layer}.json"
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] direct attribution -> {out_path}")


# --------------------------------------------------------------------------- #
# stage 10: circuit validation -- faithfulness + completeness by mean-ablation  #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def circuit_validation(layer: int = 14, top_k: int = 21, per_class: int = 200,
                       seed: int = 0, negatives: str = "hard") -> None:
    """Turn the variance-share RANKING into a validated circuit -- or refute it.

    Variance share says a component correlates with the decision along the direct
    path. It does NOT say the model needs it. The standard test is a pair of
    interventions:

      - FAITHFULNESS: ablate the circuit. Behaviour should collapse.
      - COMPLETENESS: ablate everything else. Behaviour should survive.

    Ablation is MEAN, not zero, and that choice is forced by our own data: 98 of
    183 components are near-constant offsets, so zeroing them would destroy the
    model's operating point and confound the result with a scale change. Replacing
    a component's decision-position output with its dataset mean removes exactly
    the VARYING part -- which is what variance share measures -- and leaves the
    constant contribution intact.

    Four arms, because "ablating 21 things breaks the model" proves nothing on its
    own: baseline, circuit, complement, and a RANDOM-k control matched on count.

    Note this is a real intervention, so unlike direct attribution it does capture
    effects downstream of the ablated component. It intervenes only at the decision
    position, so attention from later layers to earlier positions is untouched.
    -> interp/circuit_validation_L{layer}_k{top_k}.json
    """
    import json
    import os
    import random
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")
    from field_attribution import variance_decomposition

    tok, model = _load_model()
    dev = model.device
    true_id, false_id = _true_false_ids(tok)
    pairs, rows = _mine(per_class, negatives, seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    cfg = model.config
    n_heads = cfg.num_attention_heads
    head_dim = getattr(cfg, "head_dim", cfg.hidden_size // n_heads)

    # ---- pass 1: direction, contributions, and per-component MEAN vectors ---- #
    store: dict[str, torch.Tensor] = {}
    hooks = []

    def mk_mlp_cap(L):
        def hook(_m, _i, out):
            store[f"L{L}.mlp"] = (out[0] if isinstance(out, tuple) else out)[:, -1, :]
        return hook

    def mk_oproj_cap(L):
        def hook(_m, inp, _o):
            store[f"L{L}.oin"] = inp[0][:, -1, :]
        return hook

    for L in range(layer):
        blk = model.model.layers[L]
        hooks.append(blk.mlp.register_forward_hook(mk_mlp_cap(L)))
        hooks.append(blk.self_attn.o_proj.register_forward_hook(mk_oproj_cap(L)))

    names = ["embed"]
    for L in range(layer):
        names += [f"L{L}.attn.h{h}" for h in range(n_heads)] + [f"L{L}.mlp"]
    col = {n: j for j, n in enumerate(names)}
    contribs = np.zeros((len(prompts), len(names)))
    resid = np.zeros((len(prompts), cfg.hidden_size))
    sums: dict[str, torch.Tensor] = {}

    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
        store.clear()
        with torch.no_grad():
            hs = model(**enc, output_hidden_states=True).hidden_states
        n = hs[0].shape[0]
        resid[i : i + n] = hs[layer][:, -1, :].float().cpu().numpy()
        store["embed"] = hs[0][:, -1, :]
        for k, v in store.items():
            sums[k] = v.float().sum(0) + (sums[k] if k in sums else 0.0)
    for hk in hooks:
        hk.remove()
    means = {k: (v / len(prompts)) for k, v in sums.items()}

    d = resid[y == 1].mean(0) - resid[y == 0].mean(0)
    d = d / (np.linalg.norm(d) + 1e-9)
    d_t = torch.tensor(d, dtype=torch.float32, device=dev)

    # re-capture to project (cheap second pass keeps the code simple)
    hooks = []
    for L in range(layer):
        blk = model.model.layers[L]
        hooks.append(blk.mlp.register_forward_hook(mk_mlp_cap(L)))
        hooks.append(blk.self_attn.o_proj.register_forward_hook(mk_oproj_cap(L)))
    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
        store.clear()
        with torch.no_grad():
            hs = model(**enc, output_hidden_states=True).hidden_states
        n = hs[0].shape[0]
        contribs[i : i + n, col["embed"]] = (
            hs[0][:, -1, :].float() @ d_t).cpu().numpy()
        for L in range(layer):
            contribs[i : i + n, col[f"L{L}.mlp"]] = (
                store[f"L{L}.mlp"].float() @ d_t).cpu().numpy()
            x = store[f"L{L}.oin"].float()
            W = model.model.layers[L].self_attn.o_proj.weight.detach().float()
            for h in range(n_heads):
                sl = slice(h * head_dim, (h + 1) * head_dim)
                contribs[i : i + n, col[f"L{L}.attn.h{h}"]] = (
                    (x[:, sl] @ W[:, sl].T) @ d_t).cpu().numpy()
    for hk in hooks:
        hk.remove()

    vd = variance_decomposition(contribs, names, resid @ d, labels=y)
    circuit = [e["component"] for e in vd["ranking"][:top_k]]
    others = [n for n in names if n not in set(circuit) and n != "embed"]
    rng = random.Random(seed)
    control = rng.sample(others, min(top_k, len(others)))
    print(f"[circ] circuit({top_k}) = {circuit}", flush=True)
    print(f"[circ] random control  = {control}", flush=True)

    # ---- arms ---- #
    def run(ablate: list[str]) -> np.ndarray:
        abl = set(ablate)
        mlp_L = {int(n.split(".")[0][1:]) for n in abl if n.endswith(".mlp")}
        head_L: dict[int, list[int]] = {}
        for n in abl:
            if ".attn.h" in n:
                L = int(n.split(".")[0][1:])
                head_L.setdefault(L, []).append(int(n.split(".h")[1]))
        hk = []

        def mk_mlp_sub(L):
            def hook(_m, _i, out):
                o = out[0] if isinstance(out, tuple) else out
                o[:, -1, :] = means[f"L{L}.mlp"].to(o.dtype)
                return (o, *out[1:]) if isinstance(out, tuple) else o
            return hook

        def mk_oproj_pre(L, heads):
            def pre(_m, inp):
                x = inp[0].clone()
                m = means[f"L{L}.oin"].to(x.dtype)
                for h in heads:
                    sl = slice(h * head_dim, (h + 1) * head_dim)
                    x[:, -1, sl] = m[sl]
                return (x,)
            return pre

        for L in mlp_L:
            hk.append(model.model.layers[L].mlp.register_forward_hook(mk_mlp_sub(L)))
        for L, hs_ in head_L.items():
            hk.append(model.model.layers[L].self_attn.o_proj
                      .register_forward_pre_hook(mk_oproj_pre(L, hs_)))
        ps, projs = [], []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                o = model(**enc, output_hidden_states=True)
            lg = o.logits[:, -1, :]
            pr = torch.softmax(torch.stack([lg[:, true_id], lg[:, false_id]], 1), 1)
            ps.append(pr[:, 0].float().cpu().numpy())
            projs.append((o.hidden_states[layer][:, -1, :].float() @ d_t).cpu().numpy())
        for h in hk:
            h.remove()
        return np.concatenate(ps), np.concatenate(projs)

    base, base_proj = run([])
    # ablate_all is a SANITY arm: if replacing every component's varying part
    # leaves the layer-`layer` projection unchanged, the hooks are not firing and
    # every other arm is meaningless.
    all_comps = [n for n in names if n != "embed"]
    arms = {
        "baseline": (base, base_proj),
        "ablate_circuit": run(circuit),
        "ablate_complement": run(others),
        "ablate_random_k": run(control),
        "ablate_all_SANITY": run(all_comps),
    }

    results = {}
    for name, (p, pj) in arms.items():
        results[name] = {
            "mean_p": float(p.mean()), "std_p": float(p.std()),
            "accuracy": float(((p >= 0.5) == (y == 1)).mean()),
            "verdict_agreement_with_baseline": float(((p >= 0.5) == (base >= 0.5)).mean()),
            "corr_with_baseline": (
                float(np.corrcoef(p, base)[0, 1]) if p.std() > 1e-9 else 0.0
            ),
            "std_retained": float(p.std() / base.std()) if base.std() > 1e-9 else 0.0,
            "proj_std": float(pj.std()),
            "proj_std_retained": (
                float(pj.std() / base_proj.std()) if base_proj.std() > 1e-9 else 0.0
            ),
            "proj_corr_with_baseline": (
                float(np.corrcoef(pj, base_proj)[0, 1]) if pj.std() > 1e-9 else 0.0
            ),
        }
        e = results[name]
        print(f"[circ] {name:<18} acc={e['accuracy']:.3f} "
              f"p_std={e['std_retained']:.3f}x agree={e['verdict_agreement_with_baseline']:.3f} "
              f"| PROJ std={e['proj_std_retained']:.3f}x "
              f"corr={e['proj_corr_with_baseline']:+.3f}", flush=True)

    payload = {
        "layer": layer, "top_k": top_k, "n_pairs": len(pairs),
        "ablation": "mean (not zero) -- 98/183 components are near-constant offsets",
        "circuit": circuit, "random_control": control,
        "variance_shares": {e["component"]: e["var_share"] for e in vd["ranking"][:top_k]},
        "results": results,
    }
    os.makedirs("/out/interp", exist_ok=True)
    out_path = f"/out/interp/circuit_validation_L{layer}_k{top_k}.json"
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] circuit validation -> {out_path}")


# --------------------------------------------------------------------------- #
# stage 11: WHERE does the decision enter the decision position?                #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def layer_cutoff_sweep(per_class: int = 200, seed: int = 0, negatives: str = "hard",
                       cuts: str = "4,8,14,18,20,22,24,25,26,27,28",
                       single: str = "") -> None:
    """Sweep how much of the decision position can be erased before behaviour dies.

    `circuit_validation` showed that mean-ablating EVERY component at the decision
    position across layers 0-13 leaves accuracy and 99.8% of verdicts untouched --
    the model rebuilds the verdict from the field-token positions. But the logits
    are a function of the final residual AT that position, so ablating all 28
    layers must destroy it. The interesting quantity is where in between.

    For each cut ``k``, mean-ablate every attention head and MLP at the decision
    position for layers ``< k`` and measure behaviour. The curve localizes the
    layer at which the decision becomes committed at that position, which is the
    honest answer to "where is the decision made" -- as opposed to "where is it
    first linearly readable", which is what the probes measured.

    k=28 is the built-in sanity arm: it must drive P(match) to a constant.
    -> interp/layer_cutoff_sweep.json
    """
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")

    tok, model = _load_model()
    dev = model.device
    true_id, false_id = _true_false_ids(tok)
    pairs, rows = _mine(per_class, negatives, seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    cfg = model.config
    n_layers = cfg.num_hidden_layers
    cut_list = sorted({int(c) for c in cuts.split(",") if 0 < int(c) <= n_layers})

    # ---- means of every component's decision-position output, all layers ---- #
    store: dict[str, torch.Tensor] = {}
    hooks = []

    def mk_mlp_cap(L):
        def hook(_m, _i, out):
            store[f"L{L}.mlp"] = (out[0] if isinstance(out, tuple) else out)[:, -1, :]
        return hook

    def mk_oproj_cap(L):
        def hook(_m, inp, _o):
            store[f"L{L}.oin"] = inp[0][:, -1, :]
        return hook

    for L in range(n_layers):
        blk = model.model.layers[L]
        hooks.append(blk.mlp.register_forward_hook(mk_mlp_cap(L)))
        hooks.append(blk.self_attn.o_proj.register_forward_hook(mk_oproj_cap(L)))

    sums: dict[str, torch.Tensor] = {}
    for i in range(0, len(prompts), 16):
        enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
        store.clear()
        with torch.no_grad():
            model(**enc)
        for k, v in store.items():
            sums[k] = v.float().sum(0) + (sums[k] if k in sums else 0.0)
    for hk in hooks:
        hk.remove()
    means = {k: v / len(prompts) for k, v in sums.items()}

    def run(layers) -> np.ndarray:
        """``layers`` is the explicit set of layers to mean-ablate at the
        decision position (a prefix for the cumulative sweep, or a single layer
        for the isolation control -- a prefix sweep alone cannot separate 'this
        layer writes the decision' from 'everything up to here mattered')."""
        hk = []

        def mk_mlp_sub(L):
            def hook(_m, _i, out):
                o = out[0] if isinstance(out, tuple) else out
                o[:, -1, :] = means[f"L{L}.mlp"].to(o.dtype)
                return (o, *out[1:]) if isinstance(out, tuple) else o
            return hook

        def mk_oproj_pre(L):
            def pre(_m, inp):
                x = inp[0].clone()
                x[:, -1, :] = means[f"L{L}.oin"].to(x.dtype)
                return (x,)
            return pre

        for L in layers:
            blk = model.model.layers[L]
            hk.append(blk.mlp.register_forward_hook(mk_mlp_sub(L)))
            hk.append(blk.self_attn.o_proj.register_forward_pre_hook(mk_oproj_pre(L)))
        ps = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(dev)
            with torch.no_grad():
                lg = model(**enc).logits[:, -1, :]
            pr = torch.softmax(torch.stack([lg[:, true_id], lg[:, false_id]], 1), 1)
            ps.append(pr[:, 0].float().cpu().numpy())
        for h in hk:
            h.remove()
        return np.concatenate(ps)

    base = run([])
    print(f"[cut] baseline acc={float(((base >= 0.5) == (y == 1)).mean()):.3f} "
          f"std={base.std():.4f}", flush=True)

    rows_out = []
    jobs = ([("only", int(x)) for x in single.split(",") if x.strip()]
            if single else [("prefix", k) for k in cut_list])
    for kind, k in jobs:
        p = run([k] if kind == "only" else list(range(k)))
        e = {
            "kind": kind, "cut": k,
            "accuracy": float(((p >= 0.5) == (y == 1)).mean()),
            "std_p": float(p.std()),
            "std_retained": float(p.std() / base.std()) if base.std() > 1e-9 else 0.0,
            "verdict_agreement": float(((p >= 0.5) == (base >= 0.5)).mean()),
            "mean_p": float(p.mean()),
        }
        rows_out.append(e)
        label = f"ONLY layer {k:>2}" if kind == "only" else f"layers <{k:>2}"
        print(f"[cut] ablate {label}: acc={e['accuracy']:.3f} "
              f"std={e['std_retained']:.3f}x agree={e['verdict_agreement']:.3f} "
              f"mean_p={e['mean_p']:.3f}", flush=True)

    payload = {
        "note": ("mean-ablation of every head+MLP at the DECISION POSITION for layers "
                 "< cut; k=n_layers is the sanity arm and must give std_retained ~0"),
        "n_layers": n_layers, "n_pairs": len(pairs), "negatives": negatives,
        "seed": seed, "baseline_acc": float(((base >= 0.5) == (y == 1)).mean()),
        "sweep": rows_out,
    }
    os.makedirs("/out/interp", exist_ok=True)
    out_path = ("/out/interp/layer_cutoff_single.json" if single
                else "/out/interp/layer_cutoff_sweep.json")
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] layer cutoff sweep -> {out_path}")


@app.local_entrypoint()
def cutoff(per_class: int = 200, seed: int = 0, negatives: str = "hard",
           cuts: str = "4,8,14,18,20,22,24,25,26,27,28", single: str = "") -> None:
    """Where does the decision actually enter the decision position?"""
    layer_cutoff_sweep.remote(
        per_class=per_class, seed=seed, negatives=negatives, cuts=cuts, single=single
    )


@app.local_entrypoint()
def validate_circuit(layer: int = 14, top_k: int = 21, per_class: int = 200,
                     seed: int = 0, negatives: str = "hard") -> None:
    """Faithfulness + completeness test of the variance-share circuit."""
    circuit_validation.remote(
        layer=layer, top_k=top_k, per_class=per_class, seed=seed, negatives=negatives
    )


@app.local_entrypoint()
def direct(layer: int = 14, per_class: int = 200, seed: int = 0,
           negatives: str = "hard") -> None:
    """Exact per-component decomposition of the decision (mechanistic, not fitted)."""
    direct_attribution.remote(
        layer=layer, per_class=per_class, seed=seed, negatives=negatives
    )


@app.local_entrypoint()
def attribution(per_class: int = 200, seed: int = 0, negatives: str = "hard",
                dataset: str = "person", max_order: int = 1) -> None:
    """Per-pair causal field attribution by occlusion (necessity + sufficiency)."""
    causal_attribution.remote(
        per_class=per_class, seed=seed, negatives=negatives, dataset=dataset,
        max_order=max_order,
    )


@app.local_entrypoint()
def faithfulness(per_class: int = 400, seed: int = 0, negatives: str = "hard",
                 split: str = "cluster", link: str = "linear",
                 dataset: str = "person", corruption_matched: bool = False) -> None:
    """Pin the shipped explainer's faithfulness against the model's real verdict."""
    faithfulness_eval.remote(
        per_class=per_class, seed=seed, negatives=negatives, split=split, link=link,
        dataset=dataset, corruption_matched=corruption_matched,
    )


@app.local_entrypoint()
def sae(layer: int = 14, n_pairs: int = 3000, expansion: int = 16, l1: float = 4e-3,
        steps: int = 4000) -> None:
    train_sae.remote(layer=layer, n_pairs=n_pairs, expansion=expansion, l1=l1, steps=steps)
    print(f"done -> `modal volume get er-matcher-out interp/sae_features_layer{layer}.json`")


@app.local_entrypoint()
def causal(layer: int = 14, lo: int = 8, hi: int = 20, per_class: int = 150,
           n_sae_features: int = 5) -> None:
    causal_validate.remote(layer=layer, lo=lo, hi=hi, per_class=per_class,
                           n_sae_features=n_sae_features)
    print(f"done -> `modal volume get er-matcher-out interp/causal_multilayer_{lo}_{hi}.json`")


@app.local_entrypoint()
def layer2(layer: int = 14, per_class: int = 400, n_sae_features: int = 12) -> None:
    layer2_abstraction.remote(layer=layer, per_class=per_class, n_sae_features=n_sae_features)
    print(f"done -> `modal volume get er-matcher-out interp/layer2_abstraction_L{layer}.json`")


@app.local_entrypoint()
def strip(per_class: int = 400, k_min: int = 6) -> None:
    layer_early_exit.remote(per_class=per_class, k_min=k_min)
    print("done -> `modal volume get er-matcher-out interp/layer_early_exit.json`")


@app.local_entrypoint()
def truncate(per_class: int = 600, ks: str = "8,10,12,14,16,18,21,24,28") -> None:
    truncate_adapt.remote(per_class=per_class, ks=ks)
    print("done -> `modal volume get er-matcher-out interp/truncate_adapt.json`")


@app.local_entrypoint()
def leniency(per_class: int = 500, lo: int = 8, hi: int = 20) -> None:
    leniency_dial.remote(per_class=per_class, lo=lo, hi=hi)
    print("done -> `modal volume get er-matcher-out interp/leniency_dial.json`")
