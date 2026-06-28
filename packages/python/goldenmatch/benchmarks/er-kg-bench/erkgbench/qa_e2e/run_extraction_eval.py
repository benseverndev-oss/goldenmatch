"""CLI: extraction-F1 for several extractor configs -> EXTRACTION_F1.md.

Each config sets GOLDENGRAPH_EXTRACTOR + GOLDENGRAPH_EXTRACT_JSON_MODE, then scores extraction-F1 vs
planted gold. `api*` configs need the chat LLM (OPENAI_* env -> OpenAIClient; local Ollama or OpenAI);
`rebel`/`gliner` need transformers + torch (network-free, no LLM).

Run (in a job with Ollama + torch, small N for local CPU):
    python -m erkgbench.qa_e2e.run_extraction_eval --configs api_json,api_nojson,rebel \
        --n-questions 40 --out-md EXTRACTION_F1.md
"""
from __future__ import annotations

import argparse
import os
import sys

from .extraction_eval import evaluate_extractor, render_md

#: label -> the env that selects the extractor + json mode for that arm.
_CONFIGS = {
    "api_json": {"GOLDENGRAPH_EXTRACTOR": "api", "GOLDENGRAPH_EXTRACT_JSON_MODE": "1"},
    "api_nojson": {"GOLDENGRAPH_EXTRACTOR": "api", "GOLDENGRAPH_EXTRACT_JSON_MODE": "0"},
    "rebel": {"GOLDENGRAPH_EXTRACTOR": "rebel", "GOLDENGRAPH_EXTRACT_JSON_MODE": "1"},
    "gliner": {"GOLDENGRAPH_EXTRACTOR": "gliner", "GOLDENGRAPH_EXTRACT_JSON_MODE": "1"},
}


def _llm():
    from goldengraph.llm import OpenAIClient

    return OpenAIClient(model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="extraction-F1 vs planted gold, per extractor config")
    ap.add_argument("--configs", default="api_json,api_nojson,rebel",
                    help="comma list of: " + " ".join(_CONFIGS))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-questions", type=int, default=40)
    ap.add_argument("--ambiguity", type=float, default=0.6)
    ap.add_argument("--out-md", default="EXTRACTION_F1.md")
    args = ap.parse_args(argv)

    llm = _llm()
    results = []
    for name in (c.strip() for c in args.configs.split(",") if c.strip()):
        if name not in _CONFIGS:
            raise SystemExit(f"unknown config {name!r} (choose from {', '.join(_CONFIGS)})")
        for k, v in _CONFIGS[name].items():
            os.environ[k] = v
        results.append(
            evaluate_extractor(name, llm=llm, seed=args.seed,
                               n_questions=args.n_questions, ambiguity=args.ambiguity)
        )
    md = render_md(results, model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
