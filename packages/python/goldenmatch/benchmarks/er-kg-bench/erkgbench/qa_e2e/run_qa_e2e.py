"""CLI: run one engine over one corpus, write results. `--self-test` uses a
built-in mock engine (no LLM) so the harness + writers are CI-validated; the real
goldengraph run is selected with `--engine goldengraph` in the opt-in lane."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import engineered
from .corpora import load_musique
from .harness import AnswerResult, BuildResult, run_engine, write_results


class _MockEngine:
    name = "mock"
    fidelity = "self-test"

    def build_kg(self, corpus) -> BuildResult:
        return BuildResult(handle=None, input_tokens=1, output_tokens=1)

    def answer(self, handle, question: str) -> AnswerResult:
        return AnswerResult(text="?", input_tokens=1, output_tokens=1)


def _load_corpus(name: str, max_questions: int, musique_path: str | None):
    if name == "engineered":
        return engineered.generate_engineered(
            seed=20260620, n_questions=max_questions, ambiguity=0.5
        )
    if name == "musique":
        return load_musique(path=musique_path, max_questions=max_questions)
    raise SystemExit(f"unknown corpus: {name}")


def _build_engine(name: str):
    if name == "goldengraph":
        from goldengraph.embed import GoldenmatchEmbedder
        from goldengraph.llm import OpenAIClient

        from .engines.goldengraph import GoldenGraphQAEngine

        return GoldenGraphQAEngine(
            llm=OpenAIClient(model="gpt-4o-mini"),
            # provider="openai" uses goldenmatch's stdlib-only OpenAI embedding
            # provider (urllib + OPENAI_API_KEY) -- no torch/sentence-transformers
            # install, and it matches the OpenAI embeddings the other engines use, so
            # the head-to-head compares KG construction, not embedding backends. The
            # default "local" provider needs goldenmatch[embeddings] (not installed in
            # this lane) and raised ImportError at query time.
            embedder=GoldenmatchEmbedder(provider="openai"),
        )
    if name == "lightrag":
        from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed

        from .engines.lightrag import LightRAGQAEngine

        return LightRAGQAEngine(
            llm_model_func=gpt_4o_mini_complete, embedding_func=openai_embed
        )
    if name == "ms_graphrag":
        from .engines.ms_graphrag import MSGraphRAGQAEngine

        return MSGraphRAGQAEngine(
            model="gpt-4o-mini", embedding_model="text-embedding-3-large"
        )
    if name == "graphiti":
        from .engines.graphiti import GraphitiQAEngine

        return GraphitiQAEngine(
            falkordb_host=os.environ.get("FALKORDB_HOST", "localhost"),
            falkordb_port=int(os.environ.get("FALKORDB_PORT", "6379")),
        )
    raise SystemExit(f"unknown engine: {name}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", default=None)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--corpus", choices=("engineered", "musique"), required=True)
    p.add_argument("--max-questions", type=int, default=300)
    p.add_argument("--musique-path", default=None)
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--budget-usd", type=float, default=25.0)
    p.add_argument("--out-md", required=True)
    p.add_argument("--out-json", required=True)
    args = p.parse_args(argv)

    # Resolve output paths to absolute up front: some engines (graphrag's load_config)
    # os.chdir() during the run, so a relative results/ path would write to the wrong
    # dir. Anchoring before the run -- and creating the parent -- makes the write
    # CWD-independent.
    out_md = Path(args.out_md).resolve()
    out_json = Path(args.out_json).resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)

    corpus = _load_corpus(args.corpus, args.max_questions, args.musique_path)
    engine = _MockEngine() if args.self_test else _build_engine(args.engine)
    result = run_engine(engine, corpus, model=args.model, budget_usd=args.budget_usd)
    write_results([result], md_path=out_md, json_path=out_json)
    print(
        f"wrote {out_md} ({result['n_answered']}/{result['n_questions']} answered, "
        f"${result['cost_usd']})"
    )
    # Echo the head-to-head scores so the CI job log carries them without an
    # artifact download (answer_match is the correctness signal; EM reads ~0 on
    # free-text generative answers).
    print(
        f"  scores[{result['engine']}]: answer_match={result['answer_match']} "
        f"token_f1={result['token_f1']} exact_match={result['exact_match']} "
        f"support_recall={result['support_recall']}"
    )
    # A few example Q->A records so the log shows WHAT the engine answered (wrong
    # vs. async-corrupted vs. phrasing) -- the full set rides in the JSON artifact.
    for rec in result.get("per_question", [])[:3]:
        pred = " ".join(rec["prediction"].split())
        if len(pred) > 160:
            pred = pred[:160] + "..."
        print(
            f"    {rec['id']} hop{rec['hop_count']} am={rec['answer_match']} "
            f"gold={rec['gold_answer']!r} pred={pred!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
