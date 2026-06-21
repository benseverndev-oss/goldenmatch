"""MS-GraphRAG QA engine adapter -- the program's LONG POLE. Microsoft GraphRAG
indexes via a multi-workflow pipeline (config + file IO -> parquet artifacts) and
queries via `api.local_search` over the loaded artifacts.

Honesty notes:
- The `settings.yaml` schema + the `build_index`/`local_search` API are
  version-sensitive and could not be validated locally (graphrag is not installable
  on the dev box). The DB-free CI smoke validates protocol conformance + that
  graphrag imports; the REAL `bench-graphrag-qa` lane is the execution validator and
  may need an iteration to nail the version-specific config. CONFIRM items are
  flagged inline.
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

# Best-effort minimal settings.yaml (graphrag ~2.x). CONFIRM the schema against the
# pinned graphrag version; the real lane is the validator. ${GRAPHRAG_API_KEY} is
# graphrag's default key env (set = the OpenAI key in the lane).
_SETTINGS_YAML = """\
models:
  default_chat_model:
    type: openai_chat
    model: {model}
    api_key: ${{GRAPHRAG_API_KEY}}
  default_embedding_model:
    type: openai_embedding
    model: text-embedding-3-small
    api_key: ${{GRAPHRAG_API_KEY}}
input:
  type: file
  file_type: text
  base_dir: input
chunks:
  size: 600
  overlap: 100
output:
  type: file
  base_dir: output
cache:
  type: file
  base_dir: cache
reporting:
  type: file
  base_dir: logs
extract_graph:
  model_id: default_chat_model
embed_text:
  model_id: default_embedding_model
community_reports:
  model_id: default_chat_model
"""

# Output parquet tables local_search needs (CONFIRM filenames vs the pinned version).
_ARTIFACTS = ("entities", "communities", "community_reports", "relationships", "text_units")


class MSGraphRAGQAEngine:
    name = "ms_graphrag"
    fidelity = "real-e2e"

    def __init__(self, *, model: str = "gpt-4o-mini"):
        self._model = model
        self._counter = {"in": 0, "out": 0}

    def _build_config(self, workdir: str):
        # CONFIRM: load_config import path + that this settings.yaml validates.
        from graphrag.config.load_config import load_config

        root = Path(workdir)
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "settings.yaml").write_text(
            _SETTINGS_YAML.format(model=self._model), encoding="utf-8"
        )
        return load_config(root)

    def build_kg(self, corpus) -> BuildResult:
        import graphrag.api as api

        t0 = time.perf_counter()
        workdir = tempfile.mkdtemp(prefix="msgraphrag_")
        cfg = self._build_config(workdir)
        in_dir = Path(workdir) / "input"
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
        # CONFIRM local_search's exact param list against the pinned version.
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
