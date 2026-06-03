# 0002 — Scale-mode contract

**Status:** accepted + shipped (2026-06-03, PR #702) • **Human sign-off:** Ben, 2026-06-03

## Context
The DataFusion spine is semantically correct but NOT bit-identical to the standard
in-memory pipeline (parallel float reductions; MAX-vs-last-wins dedup; a reduced feature
surface). Shipping it as a silent behavior change would be wrong.

## Decision
Introduce an explicit, opt-in execution mode: `GoldenMatchConfig.mode: {"standard","scale"}`,
default `"standard"`.

- **Customer statement (approved):** "scale mode is deterministic and semantically correct
  (identical clusters, ε-equal confidence) but not bit-identical to standard mode."
- **Dedup:** scale mode uses MAX (`dedup_pairs_max_score`); standard keeps last-wins. On
  the default single-weighted-matchkey path MAX≡last-wins (blast radius R1=0), so it only
  differs on explicit multi-matchkey configs (where MAX is the principled choice).
- **Hard feature gate** at `run_spine` entry (`_validate_scale_mode_supported`): raises
  `ValueError` if `mode!="scale"`, `NotImplementedError` (explicit, never silent) on
  llm_boost / llm_auto / llm_scorer.enabled / domain.enabled / mk.rerank /
  mk.negative_evidence / non-weighted matchkeys.
- **Determinism gate:** identical pair SET + cluster PARTITION + id_prep edges across
  `target_partitions {1,3,17}` (compared as sets, not float equality).

## Consequences
- `mode` default is NOT flipped to `"scale"` — that is a separate decision gated on the
  Stage E verdict + sign-off; the Stage E result keeps it opt-in
  ([0003-stage-e-spill-honest-null.md](0003-stage-e-spill-honest-null.md)).
- Enforced in code: `config/schemas.py` (field) + `backends/datafusion_spine.py` (gate);
  tested in `tests/test_datafusion_spine_scale_mode.py` + the determinism test.

---
**Classification:** decision/accepted • **Last updated:** 2026-06-03
