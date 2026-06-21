"""MS-GraphRAG QA engine adapter -- the program's LONG POLE. Microsoft GraphRAG
indexes via a multi-workflow pipeline (config + file IO -> parquet artifacts) and
queries via `api.local_search` over the loaded artifacts.

Version note (validated against graphrag 3.1.0):
- graphrag's `settings.yaml` schema moves fast across releases (2.x used
  `models:`/`output:`/`chunks:`; 3.x renamed these to
  `completion_models:`/`embedding_models:`/`output_storage:`/`chunking:` and
  silently ignores the old keys -- so a hand-written 2.x YAML loads but configures
  nothing, leaving the models unset). To stay correct across versions we DON'T hand
  -write the config: we call graphrag's own project scaffolder
  (`initialize_project_at`, the function behind `graphrag init`) to emit the
  canonical `settings.yaml` + prompt files for the INSTALLED version, substituting
  only the shared chat + embedding model. The `build_index`/`local_search` API
  signatures and the output parquet table names were confirmed against 3.1.0.
- The API key resolves from `$GRAPHRAG_API_KEY` (graphrag's default key env, set to
  the OpenAI key in the lane); python-dotenv does not override an env var that is
  already set, so the scaffolded `.env` placeholder is inert.
- GraphRAG's LLM is config-driven (no inject-a-counting-func hook), so token usage
  isn't surfaced to the harness -> cost is reported approximate (0). The results doc
  marks MS-GraphRAG's cost approximate.

graphrag is imported lazily so importing this module for the registry is cheap."""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from ..harness import AnswerResult, BuildResult

# Output parquet tables `local_search` consumes (confirmed against graphrag 3.1.0:
# the workflows write `<name>.parquet` into output_storage, default base_dir "output").
_ARTIFACTS = ("entities", "communities", "community_reports", "relationships", "text_units")


class MSGraphRAGQAEngine:
    name = "ms_graphrag"
    fidelity = "real-e2e"

    def __init__(
        self, *, model: str = "gpt-4o-mini", embedding_model: str = "text-embedding-3-large"
    ):
        # text-embedding-3-large (3072-dim) matches graphrag's default
        # vector_store.vector_size (3072). graphrag decouples the embedding model from
        # the LanceDB column dimension, so a 1536-dim model (3-small) provisions a
        # 3072-dim store at index time and then fails local_search at query time with
        # "query dim(1536) doesn't match the column vector dim(3072)". Aligning the
        # model to the default vector_size keeps index + query consistent with zero
        # nested config overrides.
        self._model = model
        self._embedding_model = embedding_model

    def _build_config(self, workdir: str):
        """Scaffold graphrag's own canonical config for the installed version, then
        load it. Self-heals across graphrag's fast-moving settings schema."""
        from graphrag.cli.initialize import initialize_project_at
        from graphrag.config.load_config import load_config

        root = Path(workdir)
        # force=True overwrites settings.yaml/prompts; it does not touch input/output.
        initialize_project_at(
            root, force=True, model=self._model, embedding_model=self._embedding_model
        )
        return load_config(root)

    def build_kg(self, corpus) -> BuildResult:
        import graphrag.api as api

        t0 = time.perf_counter()
        workdir = tempfile.mkdtemp(prefix="msgraphrag_")
        cfg = self._build_config(workdir)
        # Default input config is text files under input_storage base_dir "input".
        in_dir = Path(workdir) / "input"
        in_dir.mkdir(parents=True, exist_ok=True)
        for doc in corpus.documents:
            (in_dir / f"{doc.id}.txt").write_text(doc.text, encoding="utf-8")
        asyncio.run(api.build_index(config=cfg))  # reads input/, writes output/*.parquet
        handle = {"config": cfg, "output_dir": Path(workdir) / "output"}
        # config-driven LLM -> usage not surfaced; build cost approximate (0).
        return BuildResult(
            handle=handle, input_tokens=0, output_tokens=0, latency_s=time.perf_counter() - t0
        )

    def answer(self, handle, question: str) -> AnswerResult:
        import graphrag.api as api
        import pandas as pd

        t0 = time.perf_counter()
        out = handle["output_dir"]
        arts = {name: pd.read_parquet(out / f"{name}.parquet") for name in _ARTIFACTS}
        resp, _ctx = asyncio.run(
            api.local_search(
                config=handle["config"],
                entities=arts["entities"],
                communities=arts["communities"],
                community_reports=arts["community_reports"],
                text_units=arts["text_units"],
                relationships=arts["relationships"],
                covariates=None,
                community_level=2,
                response_type="single sentence",
                query=question,
            )
        )
        return AnswerResult(
            text=str(resp or ""),
            retrieved_fact_ids=(),
            input_tokens=0,  # config-driven LLM; cost approximate
            output_tokens=0,
            latency_s=time.perf_counter() - t0,
        )
