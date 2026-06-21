"""CLI: run one engine over one corpus, write results. `--self-test` uses a
built-in mock engine (no LLM) so the harness + writers are CI-validated; the real
goldengraph run is selected with `--engine goldengraph` in the opt-in lane."""
from __future__ import annotations

import argparse
import os

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
            llm=OpenAIClient(model="gpt-4o-mini"), embedder=GoldenmatchEmbedder()
        )
    if name == "lightrag":
        from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed

        from .engines.lightrag import LightRAGQAEngine

        return LightRAGQAEngine(
            llm_model_func=gpt_4o_mini_complete, embedding_func=openai_embed
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

    corpus = _load_corpus(args.corpus, args.max_questions, args.musique_path)
    engine = _MockEngine() if args.self_test else _build_engine(args.engine)
    result = run_engine(engine, corpus, model=args.model, budget_usd=args.budget_usd)
    write_results([result], md_path=args.out_md, json_path=args.out_json)
    print(
        f"wrote {args.out_md} ({result['n_answered']}/{result['n_questions']} answered, "
        f"${result['cost_usd']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
