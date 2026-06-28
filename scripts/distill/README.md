# goldengraph distilled extractor -- pipeline scaffold

Purpose-built small KG extractor distilled from gpt-4o-mini, to lift the local OSS-LLM lane's
extraction quality while staying CPU-inferable + key-free at run time.

Design: `docs/superpowers/specs/2026-06-28-goldengraph-distilled-extractor-design.md`.

**Status: HARNESS SCAFFOLD.** The pipeline plumbing is here; the trainer bodies + the per-extractor
eval loop are stubbed (TODOs) until the cheap-win A/B (JSON-mode / REBEL / hybrid) confirms extraction
is the bottleneck and picks the student. Nothing here trains yet -- it's the rails.

## Stages

| stage | script | runs where | status |
|-------|--------|-----------|--------|
| 1 capture teacher labels | `capture_pairs.py` | local/CI (reads a DISTILL_LOG) | concrete |
| 2 build dataset (disjoint split) | `build_dataset.py` | local/CI | concrete |
| 3 train student (GPU) | `modal_train.py` | **Modal** | harness + stubbed trainer |
| 5 eval extraction-F1 vs planted gold | `eval_extractor.py` | local/CI (bench) | skeleton |

(Stage 4 "serve" = publish the trained artifact as a GitHub Release / HF repo, then the bench-graphrag-qa
lane pulls it -- `local_llm=<ollama model>` or `GOLDENGRAPH_EXTRACTOR=rebel` + `GG_REBEL_MODEL=<path>`.)

## Capture -> dataset (A/B-independent, do anytime)

```bash
# 1. capture: run a gpt-4o-mini teacher pass with GOLDENGRAPH_DISTILL_LOG set (e.g. via the
#    bench-graphrag-qa goldengraph job's distill knob, or any ingest), producing distill.jsonl.
python scripts/distill/capture_pairs.py --in distill.jsonl --out scripts/distill/data/pairs.jsonl
# 2. split document-disjoint + schema report:
python scripts/distill/build_dataset.py --in scripts/distill/data/pairs.jsonl \
    --out-dir scripts/distill/data/dataset
```

## Train on Modal (creds in Infisical)

Modal creds: project `a99885f0-c5af-4ae1-9dc8-255cc60aa129`, env `dev` -- `MODAL_TOKEN_ID` +
`MODAL_TOKEN_SECRET`.

```powershell
$P = "a99885f0-c5af-4ae1-9dc8-255cc60aa129"
$env:MODAL_TOKEN_ID     = (infisical.cmd secrets get MODAL_TOKEN_ID     --projectId $P --env dev --plain)
$env:MODAL_TOKEN_SECRET = (infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId $P --env dev --plain)
pip install modal
modal run scripts/distill/modal_train.py::smoke                 # validate auth + GPU image FIRST
modal run scripts/distill/modal_train.py --student rebel --data scripts/distill/data/dataset
```

`scripts/distill/data/` is gitignored (datasets + artifacts are not committed).

## Honest constraints (from the design)

- Teacher CAPS the student (gpt-4o-mini extraction is the ceiling, not ground truth).
- Eval gold is INDEPENDENT (engineered planted triples), NOT teacher labels -- else circular.
- Overfit guard: train on diverse text, eval extraction-F1 on held-out engineered + answer-match on
  held-out MuSiQue. A win only on the 45-entity engineered corpus is NOT a win.
- Default student = seq2seq-REBEL (cheapest, seam exists) unless the A/B says otherwise.
