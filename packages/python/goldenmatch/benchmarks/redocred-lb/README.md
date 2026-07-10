# Re-DocRED leaderboard harness (ATLOP)

A from-scratch, modern-transformers reimplementation of **ATLOP** (Zhou et al., *Document-Level
Relation Extraction with Adaptive Thresholding and Localized Context Pooling*, AAAI 2021),
trained on **Re-DocRED** (Tan et al., 2022) and scored with the **official DocRED scorer**
(F1 + Ign F1) — the metric the [Re-DocRED leaderboard](https://paperswithcode.com/sota/relation-extraction-on-re-docred)
ranks on.

This is the deliberate "commodity axis" companion to the CLEAR-KG benchmark next door: CLEAR-KG
argues extraction is fine-tuning-bound and measures the zero-shot LLM floor (~0.15–0.28 F1); this
harness closes the loop by actually *doing* the fine-tuning, on the real standard benchmark, to
show where the ceiling is.

## Architecture (`model.py` / `losses.py` / `long_input.py`)

- **Encoder** (RoBERTa-large by default; DeBERTa-v3-large for the SOTA push) with length-invariant
  encoding — `process_long_input` splits >512-token docs into two overlapping windows and averages.
- **Entity embedding** = log-sum-exp pool over the `*` markers inserted before each mention.
- **Localized context pooling** — per entity-pair context vector from the product of the head/tail
  attention distributions of the last layer.
- **Grouped bilinear** classifier + **adaptive-thresholding loss** (a learned per-pair threshold
  class): a relation is predicted iff its logit beats the TH logit.

## Data / scoring (`prepro.py` / `scoring.py`)

- `prepro.read_docred` is tokenizer-injected so the marker insertion, entity-position mapping, and
  pair/label matrix are unit-testable offline (`tests/test_pipeline_offline.py`, no torch/network).
- `scoring.official_evaluate` is a faithful stdlib port of the reference DocRED `evaluation.py`:
  micro F1 plus **Ign F1** (facts whose entity-name pair appears in train are removed from precision).
- Re-DocRED splits are fetched on demand from `tonytan48/Re-DocRED` into the gitignored `data/`.

## Run (Modal GPU)

```bash
pip install modal aiohttp-socks python-socks       # client + proxy support
modal token set --token-id <id> --token-secret <secret>
# fetch data/ (train/dev/test_revised.json) from tonytan48/Re-DocRED first
modal run modal_app.py --smoke-only                # image + GPU + prepro + 1 forward
modal run --detach modal_app.py --spawn            # full 30-epoch train, survives CLI kill
modal volume get redocred-lb /out ./_out           # pull manifest.json (dev+test metrics)
```

Per-epoch dev/test F1 + Ign F1 are logged and the best-dev-Ign checkpoint's metrics are written to
`/out/manifest.json` on the `redocred-lb` Volume.

## Results (see `RESULTS.md` for the full table + honest caveats)

| milestone | test F1 | test Ign F1 |
|---|--:|--:|
| ATLOP RoBERTa-large / DeBERTa-v3-large single (reproduction) | 0.776 / 0.780 | 0.770 / 0.773 |
| KnowRA (IJCAI 2025, current published single-model SOTA) | ~0.804 | — |
| **4-checkpoint ensemble + dev-tuned threshold** | **0.820** | **0.810** |

Honest read: as a **single model** we're at ATLOP level (~0.78, *below* KnowRA's ~0.804); the 0.820 is a **tuned ensemble** beating the published number on the measured metric, not a better single-model method. See `RESULTS.md`.

The ensemble/threshold search runs offline (no GPU) from the dumped logits:
`modal volume get redocred-lb /logits ./logits && python ensemble_sweep.py --logits ./logits`.

Offline tests: `python -m pytest tests/ -q` (9 tests: scorer, prepro, evidence, vectorised kernels).
