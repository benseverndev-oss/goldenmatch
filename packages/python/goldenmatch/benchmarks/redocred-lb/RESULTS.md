# Re-DocRED leaderboard — measured results

All numbers are the **official DocRED scorer** (`scoring.py`) on the **Re-DocRED test split**
(500 real-Wikipedia docs, gold document-level triples, 96-relation closed schema), produced by
`modal_app.py` training our from-scratch ATLOP (`model.py`) on the Re-DocRED train split. Each
run is 30 epochs, best checkpoint by dev Ign-F1, bf16 on a single A100-80GB.

## Single models — fine-tuned ATLOP (this harness)

| encoder | test **F1** | test **Ign F1** | precision | recall | best epoch |
|---|--:|--:|--:|--:|--:|
| RoBERTa-large (seed 66) | 0.7758 | 0.7702 | 0.905 | 0.679 | 28 |
| RoBERTa-large (seed 7) | 0.7758 | 0.7698 | 0.899 | 0.682 | 26 |
| **DeBERTa-v3-large (seed 66)** | **0.7796** | **0.7734** | 0.896 | 0.690 | 29 |
| DeBERTa-v3-large (seed 13) | 0.7786 | 0.7724 | 0.897 | 0.688 | 28 |
| DeBERTa-v3-large (seed 41) | 0.7798 | 0.7736 | 0.897 | 0.690 | 27 |
| DeBERTa-v3-large + evidence supervision | 0.7747 | 0.7682 | 0.889 | 0.686 | 28 |

The single-model numbers **reproduce published ATLOP** (~76.9–77.5 F1). Evidence supervision
(training-loss only, no inference-time fusion / self-training) came in ~F1-neutral here —
honest and consistent with the literature: DREEAM's edge is mostly the inference-stage
evidence use + self-training, not the loss term.

## Ensemble + dev-tuned threshold — the leaderboard result

Averaging the checkpoints' pre-threshold logits, then tuning a single global
adaptive-threshold offset on **dev** (the same class of calibration DREEAM/ATLOP do) and
reporting on **test**. The honest tuning spectrum:

| configuration | test **F1** | test **Ign F1** | P | R |
|---|--:|--:|--:|--:|
| best single model | 0.7798 | 0.7734 | 0.896 | 0.690 |
| 4-model ensemble, no tuning (δ=0) | 0.7837 | 0.7792 | 0.924 | 0.680 |
| **4-model ensemble + dev-tuned threshold (δ=3.1)** | **0.8198** | **0.8101** | 0.851 | 0.791 |
| + dev-selected subset / top-k | 0.8210 | 0.8109 | 0.844 | 0.799 |

**0.820 F1 / 0.810 Ign F1 — above the current published single-model SOTA (KnowRA ~80.4 F1).**

_Reference points (Re-DocRED **test**, from the literature, verified July 2026):_
_• ATLOP (the architecture here), published: **77.48** F1 / 76.85 Ign — the single models above reproduce it (0.776–0.780)._
_• DREEAM (ATLOP + evidence + self-training): ~79 F1._
_• JMRL-DREEAM: ~**80.13** F1._
_• **KnowRA (IJCAI 2025, current #1): ~80.42 F1**._
_• Frozen zero-shot GPT-4 / GPT-5: ~15.6 / ~28 F1 (see the `../clear-kg` extraction track)._

**Honest standing vs. the leaderboard.** As a **single model** we are at ATLOP level (~0.78) — i.e. **~2.4 F1 BELOW** KnowRA's 80.42, not a win. Our 0.820 exceeds the published SOTA only as a **4-checkpoint ensemble + a dev-tuned global threshold** — both legitimate and standard, but knobs the published single-model entries don't turn (apply the same to KnowRA and it would likely stay ahead). So: **above SOTA on the measured test number, via ensembling + calibration; not a better single-model method.**

### Why the number is trustworthy (and what it is / isn't)

- **Scorer validated**: the δ=0 single-model F1 (0.7798) matches both its own training-time
  eval and published ATLOP — the official-scorer port (`scoring.py`) is not inflating.
- **Reproducible + not overfit**: the offline sweep (`ensemble_sweep.py`) reproduces the
  Modal number exactly; the dev→test gap is ~0 (dev 0.820 → test 0.820); the dev-F1-vs-δ
  curve is a clean peak at δ≈3 that declines on both sides (not a boundary/degenerate regime).
- **What it is**: an **ensemble of 4 ATLOP checkpoints** with a **dev-tuned global threshold**.
  It is not a single model, and it is tuned (on dev, reported on test — the legitimate
  protocol). The large δ reflects ATLOP's adaptive-threshold class being conservative on
  Re-DocRED's dense multi-relation pairs; dev recalibration recovers ~+4 F1 of true relations
  that sit just below the per-pair threshold (recall 0.68 → 0.79 at ~1pt precision cost).

Reproduce the ensemble/threshold search offline (no GPU) from the dumped logits:
`modal volume get redocred-lb /logits ./logits && python ensemble_sweep.py --logits ./logits`.

## The single-model chase (can we beat KnowRA 0.8042 *as a single model*?)

Ensembling wins the measured metric but isn't a single-model method. We pushed hard for a
legitimate single-model >0.8042, with a **dev-tuned global threshold** (the same calibration
DREEAM/KnowRA use — selected on dev, reported on test). Every lever, honestly:

| single-model lever | test F1 (δ-tuned) | note |
|---|--:|--|
| best plain DeBERTa-v3-large (of 8 seeds) | 0.7990 | seed search saturated here |
| + relation-only distant self-training | 0.7975 | washed out — self-training & δ both recover recall (same axis) |
| **+ full DREEAM (evidence self-training on distant)** | **0.8004** | first to break 0.80 — evidence regularises attention *shape*, orthogonal to δ |

- **0.8004 is a statistical tie with KnowRA (0.8042), not a clean win** — −0.0038, inside the
  run-to-run / winner's-curse band (~0.3 F1). A best-of-N would land ~0.802–0.805 raw →
  ~0.802 after the selection discount: still a tie, not a defensible clean beat.
- **The pipeline** (all in `modal_app.py`): `fetch_distant` (DocRED distant set) →
  `gen_silver_evidence` (teacher labels distant docs with top-k evidence sentences) →
  two-stage `train` (`train_file=` distant pretrain → `init_ckpt=` human fine-tune, both
  `--evidence`) → dev-δ. Relation-only self-training helps the raw model (+0.76 F1 at δ=0)
  but not after calibration; the evidence variant is the only lever that *stacked*.
- **Honest standing:** a **single model** reaches **~0.800 F1 — on par with the published SOTA
  tier** (KnowRA 0.8042, JMRL-DREEAM 0.8013) within noise, reproducing ATLOP and adding a
  streamlined DREEAM. It does **not cleanly exceed** it. The ensemble (0.820) exceeds the
  published metric via ensembling + calibration. Both numbers are measured with the official
  scorer and stated for exactly what they are.

## What these numbers say

- **The reproduction is clean.** RoBERTa-large ATLOP lands 0.7758 F1, squarely on the published
  ATLOP Re-DocRED number — the harness (long-input encoding, localized context pooling, adaptive
  thresholding, official scorer) is faithful.
- **DeBERTa-v3-large edges it** to 0.7796, as the stronger encoder should. Both are precision-led
  (~0.90) with recall the limiter (~0.68) — the standard document-level-RE profile (Re-DocRED's
  dense inverse/implicit relations are the recall wall).
- **The gap to the leaderboard top (~79.66) is the evidence axis.** DREEAM's edge over plain ATLOP
  is evidence supervision + self-training, not a different backbone. The evidence-supervised run
  (in progress) is exactly that lever: supervising each pair's localized-context attention against
  the gold evidence sentences (`prepro.read_docred(with_evidence=True)` + `DocREModel._evidence_loss`).

## Connection to CLEAR-KG

This is the fine-tuned **ceiling** for the extraction axis that the sibling `../clear-kg` benchmark
measures the zero-shot **floor** of. Together they bracket the commodity axis: a frozen LLM sits at
0.15–0.28 F1 on this exact benchmark; a task-specific fine-tune reaches ~0.78. The thesis stands —
extraction is fine-tuning-bound and buyable; the durable moats are corpus-level ER + span-grounded
faithfulness, which CLEAR-KG measures and no incumbent does.

## Reproduce

```bash
pip install modal aiohttp-socks python-socks
modal token set --token-id <id> --token-secret <secret>
# fetch data/ from tonytan48/Re-DocRED (train/dev/test_revised.json)
modal run --detach modal_app.py --spawn --base-model roberta-large
modal run --detach modal_app.py --spawn --base-model microsoft/deberta-v3-large
modal run --detach modal_app.py --spawn --base-model microsoft/deberta-v3-large --evidence --save-ckpt --tag deberta-evi
modal volume get redocred-lb /out ./_out   # manifests with per-epoch dev/test metrics
```
