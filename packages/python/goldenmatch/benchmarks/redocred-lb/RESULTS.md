# Re-DocRED leaderboard — measured results

All numbers are the **official DocRED scorer** (`scoring.py`) on the **Re-DocRED test split**
(500 real-Wikipedia docs, gold document-level triples, 96-relation closed schema), produced by
`modal_app.py` training our from-scratch ATLOP (`model.py`) on the Re-DocRED train split. Each
run is 30 epochs, best checkpoint by dev Ign-F1, bf16 on a single A100-80GB.

## Fine-tuned ATLOP (this harness)

| encoder | test **F1** | test **Ign F1** | precision | recall | best epoch |
|---|--:|--:|--:|--:|--:|
| RoBERTa-large | 0.7758 | 0.7702 | 0.905 | 0.679 | 28 |
| **DeBERTa-v3-large** | **0.7796** | **0.7734** | 0.896 | 0.690 | 29 |
| DeBERTa-v3-large + evidence supervision (DREEAM) | _running_ | _running_ | | | |

_Reference points (Re-DocRED test, from the literature):_
_• ATLOP (the architecture here), published: ~76.9–77.5 F1 — our RoBERTa 0.7758 / DeBERTa 0.7796 **reproduce it**._
_• DREEAM (ATLOP + evidence supervision + self-training): ~**79.66** F1 — the single-model leaderboard peak._
_• Frozen zero-shot GPT-4 / GPT-5: ~15.6 / ~28 F1 (see the `../clear-kg` extraction track)._

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
