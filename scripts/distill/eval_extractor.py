"""Stage 5: extraction-F1 in isolation, scored against INDEPENDENT planted gold (not teacher labels).

CRITICAL (spec-review fix): the headline metric is entity/relation-F1 vs the bench's PLANTED gold
triples -- the engineered corpus emits `src::rel::dst` document ids and
`erkgbench.qa_e2e.scorecard_llm.extraction_counts(gold_src, gold_dst, extraction)` scores against those.
Scoring against the teacher's own labels would be circular (teacher ~ 1.0 by construction). We compare
{base-OSS, student, teacher} all against the SAME planted gold; teacher-AGREEMENT (vs teacher labels) is
a separate distillation-fidelity check, never the headline.

This is the harness skeleton -- the per-extractor run loop is wired to the bench but left as a TODO
until a trained student artifact exists (it needs the bench corpus + the extractor instances).

Usage (once a student is trained):
    PYTHONPATH=<bench>;<goldengraph> python scripts/distill/eval_extractor.py \
        --extractor rebel --model <downloaded student checkpoint>
"""
from __future__ import annotations

import argparse
import sys


def evaluate(extractor: str, *, model: str | None, n_docs: int) -> dict:
    """Run `extractor` over the engineered corpus docs and score entity/relation-F1 vs planted gold.

    extractor: 'api' (the OSS/teacher LLM via OPENAI_*), 'rebel', or 'gliner'.
    Returns {"entity_f1":..., "relation_f1":..., "n_docs":...}.
    """
    # The independent-gold scorer already exists -- import it from the bench.
    from erkgbench.qa_e2e.scorecard_llm import extraction_counts, f1_from_counts  # noqa: F401

    # TODO(impl, post-train): build the engineered corpus (generate_engineered), select the extractor
    # (goldengraph.ingest._resolve_extractor honors GOLDENGRAPH_EXTRACTOR=<extractor>; for the trained
    # student pass `model` through GG_REBEL_MODEL / the Ollama model), run extraction per doc, accumulate
    # extraction_counts(gold_src, gold_dst, extraction) across docs, then f1_from_counts. Mirror how
    # scorecard_llm aggregates entity/relation TP/FP/FN.
    raise NotImplementedError(
        "per-extractor eval loop -- wire to generate_engineered + _resolve_extractor once a student "
        "artifact exists; the independent-gold scorer (extraction_counts) is already imported above"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="extraction-F1 vs planted gold (base/student/teacher)")
    ap.add_argument("--extractor", default="api", choices=["api", "rebel", "gliner"])
    ap.add_argument("--model", default=None, help="trained student checkpoint / Ollama model")
    ap.add_argument("--n-docs", type=int, default=80)
    args = ap.parse_args(argv)
    res = evaluate(args.extractor, model=args.model, n_docs=args.n_docs)
    sys.stdout.write(
        f"{args.extractor}: entity-F1 {res['entity_f1']:.3f} relation-F1 {res['relation_f1']:.3f} "
        f"({res['n_docs']} docs)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
