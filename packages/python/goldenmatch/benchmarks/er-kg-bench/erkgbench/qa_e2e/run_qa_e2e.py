"""CLI: run one engine over one corpus, write results. `--self-test` uses a
built-in mock engine (no LLM) so the harness + writers are CI-validated; the real
goldengraph run is selected with `--engine goldengraph` in the opt-in lane."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import engineered
from .corpora import (
    MUSIQUE_HF_DATASET,
    MUSIQUE_HF_SPLIT,
    MUSIQUE_SUBSET_SEED,
    fetch_musique,
    load_musique,
)
from .harness import AnswerResult, BuildResult, run_engine, write_results


class _MockEngine:
    name = "mock"
    fidelity = "self-test"

    def build_kg(self, corpus) -> BuildResult:
        return BuildResult(handle=None, input_tokens=1, output_tokens=1)

    def answer(self, handle, question: str) -> AnswerResult:
        return AnswerResult(text="?", input_tokens=1, output_tokens=1)


def _load_corpus(
    name: str,
    max_questions: int,
    musique_path: str | None,
    ambiguity: float,
    *,
    musique_dataset: str = MUSIQUE_HF_DATASET,
    musique_split: str = MUSIQUE_HF_SPLIT,
    musique_seed: int = MUSIQUE_SUBSET_SEED,
):
    if name == "engineered":
        return engineered.generate_engineered(
            seed=20260620, n_questions=max_questions, ambiguity=ambiguity
        )
    if name == "musique":
        # MuSiQue has no ambiguity dial (entities are already canonical); the flag is
        # ignored here so a sweep over it just re-runs the same corpus. With an
        # explicit --musique-path, read that JSONL; otherwise fetch a seeded subset
        # from the Hub on demand (the corpus is never committed to the repo).
        if musique_path:
            return load_musique(path=musique_path, max_questions=max_questions)
        return fetch_musique(
            dataset=musique_dataset,
            split=musique_split,
            max_questions=max_questions,
            seed=musique_seed,
        )
    raise SystemExit(f"unknown corpus: {name}")


def _chat_model() -> str:
    """Chat model for the OpenAIClient-based engines. `or` so an empty env reads as unset."""
    return os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"


def _embed_model():
    """Embedding model name; None -> the provider's default (OpenAI text-embedding-3-*)."""
    return os.environ.get("OPENAI_EMBED_MODEL") or None


def _rag_embed_model() -> str:
    """Concrete embedding model for the openai-SDK RAG/baseline engines (they need a name, not None).
    Local lane sets OPENAI_EMBED_MODEL (e.g. nomic-embed-text via Ollama); else the OpenAI default."""
    return os.environ.get("OPENAI_EMBED_MODEL") or "text-embedding-3-large"


def _rag_embed_dim() -> int:
    """Embedding dimension LightRAG needs declared up front. `OPENAI_EMBED_DIM` override, else inferred
    from the model (nomic-embed-text = 768; OpenAI text-embedding-3-* = 3072)."""
    v = os.environ.get("OPENAI_EMBED_DIM")
    if v:
        return int(v)
    return 768 if "nomic" in (os.environ.get("OPENAI_EMBED_MODEL") or "").lower() else 3072


def _lightrag_llm_func():
    """LightRAG llm_model_func bound to the configured chat model + endpoint. Mirrors LightRAG's own
    `gpt_4o_mini_complete` wrapper (absorbs the `keyword_extraction` flag, forwards `hashing_kv` etc.),
    but with our model and OPENAI_BASE_URL so it can target a local Ollama 7B."""
    from lightrag.llm.openai import openai_complete_if_cache

    model = _chat_model()
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    api_key = os.environ.get("OPENAI_API_KEY") or None

    async def _llm(prompt, system_prompt=None, history_messages=None, keyword_extraction=False, **kw):
        return await openai_complete_if_cache(
            model, prompt, system_prompt=system_prompt,
            history_messages=history_messages or [], base_url=base_url, api_key=api_key, **kw,
        )

    return _llm


def _lightrag_embedding_func():
    """LightRAG embedding_func (EmbeddingFunc with declared dim) bound to the configured embed model +
    endpoint -- OpenAI or a local Ollama embedding model."""
    from lightrag.llm.openai import openai_embed
    from lightrag.utils import EmbeddingFunc

    model = _rag_embed_model()
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    api_key = os.environ.get("OPENAI_API_KEY") or None

    async def _embed(texts):
        return await openai_embed(texts, model=model, base_url=base_url, api_key=api_key)

    return EmbeddingFunc(embedding_dim=_rag_embed_dim(), max_token_size=8192, func=_embed)


def _build_engine(name: str):
    if name == "goldengraph":
        from goldengraph.embed import GoldenmatchEmbedder
        from goldengraph.llm import OpenAIClient

        from .engines.goldengraph import GoldenGraphQAEngine

        return GoldenGraphQAEngine(
            # OPENAI_MODEL / OPENAI_EMBED_MODEL (project-defined; empty == unset) let the local
            # OSS-LLM lane point goldengraph at a self-hosted Ollama model. Default unchanged.
            llm=OpenAIClient(model=_chat_model()),
            # provider="openai" uses goldenmatch's stdlib-only OpenAI embedding
            # provider (urllib + OPENAI_API_KEY) -- no torch/sentence-transformers
            # install, and it matches the OpenAI embeddings the other engines use, so
            # the head-to-head compares KG construction, not embedding backends. The
            # default "local" provider needs goldenmatch[embeddings] (not installed in
            # this lane) and raised ImportError at query time.
            embedder=GoldenmatchEmbedder(provider="openai", model=_embed_model()),
        )
    if name == "lightrag":
        from .engines.lightrag import LightRAGQAEngine

        # Local-aware: openai_complete_if_cache / openai_embed honor OPENAI_BASE_URL, so the SAME funcs
        # serve OpenAI (model=gpt-4o-mini, base_url unset) or a local Ollama 7B (model=qwen, base_url
        # set). LightRAG needs the embedding DIM up front -> _rag_embed_dim() (nomic=768, OpenAI=3072).
        return LightRAGQAEngine(
            llm_model_func=_lightrag_llm_func(), embedding_func=_lightrag_embedding_func()
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
    if name == "text_rag":
        # The no-KG control: naive paragraph-retrieval RAG on the SAME models the KG
        # engines use, so the gap is exactly what the graph buys (or costs).
        from .engines.text_rag import TextRAGQAEngine

        return TextRAGQAEngine(model=_chat_model(), embedding_model=_rag_embed_model())
    if name == "goldenmatch_rag":
        # goldenmatch's OWN retrieval surface (retrieve_similar_records) with the SAME
        # OpenAI embedder text_rag uses -- isolates our retrieval mechanics vs naive
        # cosine, embedder held constant.
        from .engines.goldenmatch_rag import GoldenmatchRAGQAEngine

        return GoldenmatchRAGQAEngine(model=_chat_model(), embedding_model=_rag_embed_model())
    if name == "goldenmatch_entity_rag":
        # goldenmatch's entity-aware RAG (retrieve -> dedupe -> canonicalize) -- the
        # product's differentiated claim, measured for the first time.
        from .engines.goldenmatch_rag import GoldenmatchEntityRAGQAEngine

        return GoldenmatchEntityRAGQAEngine(model=_chat_model(), embedding_model=_rag_embed_model())
    raise SystemExit(f"unknown engine: {name}")


def _make_judge(model: str):
    """A judge callable(prompt)->str via OpenAI, or None when no key/SDK. Used for
    the format-fair LLM-judge metric; a FIXED model across engines keeps the
    comparison honest. Judge failures return '' (scored NO) so they never crash a
    run. `openai` is installed in every engine lane."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    client = OpenAI()

    def _judge(prompt: str) -> str:
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=4,
            )
            return r.choices[0].message.content or ""
        except Exception:
            return ""

    return _judge


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", default=None)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--corpus", choices=("engineered", "musique"), required=True)
    p.add_argument("--max-questions", type=int, default=300)
    p.add_argument(
        "--ambiguity",
        type=float,
        default=0.5,
        help="engineered-corpus variant-mention fraction (0.0-1.0); the decay-curve "
        "sweep dial. Ignored for musique.",
    )
    p.add_argument(
        "--musique-path",
        default=None,
        help="JSONL file of MuSiQue-Ans rows. If omitted, --corpus musique fetches a "
        "seeded subset from the HuggingFace Hub on demand.",
    )
    p.add_argument("--musique-dataset", default=MUSIQUE_HF_DATASET)
    p.add_argument("--musique-split", default=MUSIQUE_HF_SPLIT)
    p.add_argument("--musique-seed", type=int, default=MUSIQUE_SUBSET_SEED)
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--budget-usd", type=float, default=25.0)
    p.add_argument(
        "--judge",
        action="store_true",
        help="score a format-fair LLM-judge answer-equivalence metric alongside "
        "answer_match (one fixed-model call per question; eval overhead, not charged "
        "to the engine budget). Needs OPENAI_API_KEY.",
    )
    p.add_argument(
        "--judge-model",
        default="gpt-4o-mini",
        help="fixed judge model -- the SAME across engines so the comparison is fair.",
    )
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

    corpus = _load_corpus(
        args.corpus,
        args.max_questions,
        args.musique_path,
        args.ambiguity,
        musique_dataset=args.musique_dataset,
        musique_split=args.musique_split,
        musique_seed=args.musique_seed,
    )
    engine = _MockEngine() if args.self_test else _build_engine(args.engine)
    judge = _make_judge(args.judge_model) if (args.judge and not args.self_test) else None
    # Label the run with the model the engine ACTUALLY used (_chat_model() honors OPENAI_MODEL), not
    # the CLI default -- otherwise a local_llm run's artifact mislabels itself as gpt-4o-mini. Unknown
    # local models fall back to the default cost rate in BudgetTracker (notional; local runs are free).
    result = run_engine(
        engine, corpus, model=_chat_model(), budget_usd=args.budget_usd, judge=judge
    )
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
        f"llm_judge={result.get('answer_judge')} "
        f"token_f1={result['token_f1']} exact_match={result['exact_match']} "
        f"support_recall={result['support_recall']}"
    )
    # Entity-answerable subset: an entity-graph can only emit a node, so non-entity
    # golds (dates/amounts/phrases) are unanswerable-by-construction -- this is the
    # honest denominator.
    print(
        f"  scores[{result['engine']}] entity-subset: "
        f"answer_match={result.get('answer_match_entity', 0.0)} "
        f"llm_judge={result.get('answer_judge_entity')} "
        f"(n={result.get('n_entity_answerable', 0)}/{result['n_answered']}); "
        f"answer_type_mix={result.get('answer_type_counts', {})}"
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
