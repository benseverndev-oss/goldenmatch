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
    tp = sum(1 for p, t in zip(pred, y) if p and t)
    fp = sum(1 for p, t in zip(pred, y) if p and not t)
    fn = sum(1 for p, t in zip(pred, y) if not p and t)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return (2 * prec * rec / (prec + rec)) if prec + rec else 0.0


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
    from field_attribution import attribute_direction, field_agreements, label_sae_features

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
    field_feats = field_agreements(rows, pairs, FIELDS)
    decomp = attribute_direction(projections, field_feats, FIELDS)
    print(f"[layer2] direction R^2 by field signals = {decomp['r2']:.3f}")
    for e in decomp["ranking"]:
        print(f"[layer2]   {e['field']:<14} coef={e['coef']:+.3f}", flush=True)

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
        "sae_feature_labels": sae_labels,
    }
    os.makedirs("/out/interp", exist_ok=True)
    with open(f"/out/interp/layer2_abstraction_L{layer}.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    _out_vol.commit()
    print(f"[done] layer2 -> /out/interp/layer2_abstraction_L{layer}.json")


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
