"""Stage 5 eval -- IMPLEMENTED in the bench (this is a pointer).

Extraction-F1 in isolation, scored against the INDEPENDENT planted gold (not teacher labels -- the
spec-review circularity fix), now lives in the er-kg-bench package because it reuses the engineered
corpus + the scorecard's `extraction_counts`:

  - module:   erkgbench.qa_e2e.extraction_eval (evaluate_extractor / render_md)
  - CLI:      python -m erkgbench.qa_e2e.run_extraction_eval --configs api_json,api_nojson,rebel \
                  --n-questions 40 --out-md EXTRACTION_F1.md
  - lane:     bench-graphrag-qa.yml  (dispatch with mode=extraction_f1, local_llm=<model>, small
              max_questions) -> EXTRACTION_F1.md artifact

That measures {api_json, api_nojson, rebel} extraction-F1 vs planted gold -- the low-noise signal for
whether a purpose-built extractor (and thus distillation) is worth it. A trained student plugs in via
`GOLDENGRAPH_EXTRACTOR=rebel` + (future) `GG_REBEL_MODEL=<checkpoint>` and is scored the same way.

This shim exists only so the scaffold is self-describing; run the bench CLI/lane above.
"""

if __name__ == "__main__":
    raise SystemExit(
        "extraction-F1 eval lives in the bench: "
        "python -m erkgbench.qa_e2e.run_extraction_eval (or the bench-graphrag-qa mode=extraction_f1 lane)"
    )
