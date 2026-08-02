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

Usage:
  modal run scripts/er_matcher/interp/modal_interp.py::probe_layers
  modal run scripts/er_matcher/interp/modal_interp.py::sae --layer 14
  modal run scripts/er_matcher/interp/modal_interp.py::causal --layer 14

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
    print(f"[sae] train activations={A.shape} decision-token={D.shape}")

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
        recon = F.mse_loss(x_hat, x)
        sparsity = z.abs().mean()
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
                "layer": layer, "d": d, "m": m},
               f"/out/interp/sae_layer{layer}.pt")
    with open(f"/out/interp/sae_features_layer{layer}.json", "w") as fh:
        json.dump({"layer": layer, "d": d, "m": m, "l1": l1, "steps": steps,
                   "n_train_acts": int(A.shape[0]), "top_features": top}, fh, indent=2)
    _out_vol.commit()
    print(f"[done] SAE -> /out/interp/sae_layer{layer}.pt + sae_features_layer{layer}.json")


# --------------------------------------------------------------------------- #
# stage 3: causal validation -- steering / ablation (THE LOCK)                #
# --------------------------------------------------------------------------- #
@app.function(image=_image, gpu=GPU, timeout=60 * 60, volumes={"/out": _out_vol})
def causal_validate(layer: int = 14, per_class: int = 150, seed: int = 0,
                    coeffs: str = "-8,-4,-2,0,2,4,8", n_sae_features: int = 5) -> None:
    """Add c*direction to the residual stream at ``layer`` during the forward pass
    and measure the mean P(match) shift. A direction is CAUSAL iff P(match) moves
    monotonically with c. Tests the diff-of-means axis + the top-correlated SAE
    feature decoder directions. Also ablation (project the direction out)."""
    import json
    import os
    import sys

    import numpy as np
    import torch

    sys.path.insert(0, "/root/interp")

    tok, model = _load_model()
    true_id, false_id = _true_false_ids(tok)
    cvals = [float(c) for c in coeffs.split(",")]

    pairs, rows = _mine(per_class, "hard", seed)
    y = np.array([t for *_, t in pairs])
    prompts = [_prompt(tok, rows[a], rows[b]) + '{"match":' for a, b, _ in pairs]

    # decision-token residual at `layer` -> diff-of-means direction
    def dec_residual() -> np.ndarray:
        reps = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(model.device)
            with torch.no_grad():
                out = model(**enc, output_hidden_states=True)
            reps.append(out.hidden_states[layer][:, -1, :].float().cpu().numpy())
        return np.concatenate(reps, axis=0)

    R = dec_residual()
    mu1, mu0 = R[y == 1].mean(0), R[y == 0].mean(0)
    diff_dir = mu1 - mu0
    diff_dir = diff_dir / (np.linalg.norm(diff_dir) + 1e-9)

    directions = {"diff_of_means": diff_dir}
    sae_path = f"/out/interp/sae_layer{layer}.pt"
    if os.path.exists(sae_path) and n_sae_features > 0:
        sae = torch.load(sae_path, map_location="cpu")
        feat_path = f"/out/interp/sae_features_layer{layer}.json"
        top = json.load(open(feat_path))["top_features"][:n_sae_features]
        for f in top:
            j = f["feature"]
            wj = sae["W_dec"][j].numpy()
            directions[f"sae_feat_{j}"] = wj / (np.linalg.norm(wj) + 1e-9)
        print(f"[causal] loaded {len(top)} SAE feature directions from {sae_path}")

    # residual-stream hook at layer L (Qwen2 decoder block output). Two modes:
    #   add:    hidden[-1] += vec               (steering)
    #   ablate: hidden[-1] -= (hidden[-1].unit_dir)*unit_dir   (project the dir out)
    ctrl: dict = {"add": None, "ablate": None}

    def hook(_mod, _inp, output):
        hidden = output[0] if isinstance(output, tuple) else output
        if ctrl["ablate"] is not None:
            u = ctrl["ablate"]
            coeff = (hidden[:, -1, :] * u).sum(-1, keepdim=True)
            hidden[:, -1, :] = hidden[:, -1, :] - coeff * u
        if ctrl["add"] is not None:
            hidden[:, -1, :] = hidden[:, -1, :] + ctrl["add"]
        return (hidden, *output[1:]) if isinstance(output, tuple) else hidden

    h = model.model.layers[layer].register_forward_hook(hook)

    def mean_p_match(add=None, ablate=None) -> float:
        ctrl["add"], ctrl["ablate"] = add, ablate
        ps = []
        for i in range(0, len(prompts), 16):
            enc = tok(prompts[i : i + 16], return_tensors="pt", padding=True).to(model.device)
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            pair = torch.softmax(torch.stack([logits[:, true_id], logits[:, false_id]], 1), dim=1)
            ps.append(pair[:, 0].float().cpu().numpy())
        ctrl["add"], ctrl["ablate"] = None, None
        return float(np.concatenate(ps).mean())

    base = mean_p_match()
    print(f"[causal] baseline mean P(match) = {base:.4f}")

    out: dict = {"layer": layer, "baseline_p_match": base, "coeffs": cvals, "directions": {}}
    for name, dvec in directions.items():
        dt = torch.tensor(dvec, dtype=torch.float16, device=model.device)
        sweep = {c: mean_p_match(add=dt * c) for c in cvals}
        ablated = mean_p_match(ablate=dt)
        mono = _monotonic([sweep[c] for c in sorted(cvals)])
        out["directions"][name] = {
            "sweep": {str(c): sweep[c] for c in cvals},
            "monotonic": mono,
            "delta_full_swing": sweep[max(cvals)] - sweep[min(cvals)],
            "ablated_p_match": ablated,
            "ablation_delta": ablated - base,
        }
        print(f"[causal] {name}: swing {sweep[min(cvals)]:.3f}->{sweep[max(cvals)]:.3f} "
              f"monotonic={mono}  ablated={ablated:.3f} (base {base:.3f})", flush=True)

    h.remove()
    os.makedirs("/out/interp", exist_ok=True)
    with open(f"/out/interp/causal_layer{layer}.json", "w") as fh:
        json.dump(out, fh, indent=2)
    _out_vol.commit()
    print(f"[done] causal -> /out/interp/causal_layer{layer}.json")


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


@app.local_entrypoint()
def sae(layer: int = 14, n_pairs: int = 3000, expansion: int = 16, l1: float = 4e-3,
        steps: int = 4000) -> None:
    train_sae.remote(layer=layer, n_pairs=n_pairs, expansion=expansion, l1=l1, steps=steps)
    print(f"done -> `modal volume get er-matcher-out interp/sae_features_layer{layer}.json`")


@app.local_entrypoint()
def causal(layer: int = 14, per_class: int = 150, n_sae_features: int = 5) -> None:
    causal_validate.remote(layer=layer, per_class=per_class, n_sae_features=n_sae_features)
    print(f"done -> `modal volume get er-matcher-out interp/causal_layer{layer}.json`")
