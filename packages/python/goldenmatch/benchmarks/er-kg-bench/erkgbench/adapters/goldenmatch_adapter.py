"""goldenmatch adapters -- the system under test, run the way the product ships.

This DOGFOODS goldenmatch: the headline rows call zero-config ``dedupe_df(df)``
with no exact/fuzzy kwargs, so goldenmatch's own auto-config controller picks
the strategy -- exactly how a user runs it, and the fair analogue of every
framework running at *its* documented default. (An earlier version hand-set
``fuzzy={name}@0.82``; that is not how the product is used, so it was dropped.)

Three configurations:

* ``goldenmatch(auto)``        -- zero-config on the name string only; the
  apples-to-apples comparison against each framework's name-based default.
* ``goldenmatch(auto+fields)`` -- zero-config on name + type + context; the
  realistic multi-field usage that lets auto-config exploit extra evidence.
* ``goldenmatch(auto+llm)``    -- zero-config + ``llm_scorer=True``; only added
  by the runner when ``OPENAI_API_KEY`` is set. This is the configuration that
  actually attacks the semantic classes (abbreviation / synonym / cross-lingual)
  no string method touches -- goldenmatch's auto-config already *reaches* for the
  LLM by default ("No API key for LLM extraction. Skipping" appears without one).

Cluster members come back in ``__row_id__`` space, which equals input row order,
so they map straight to record indices (the harness resolves the full set,
indices 0..n-1). Auto-config carries mild EM-sample-order non-determinism, so
the runner's determinism check reports the actual observed result rather than an
asserted guarantee.
"""

from __future__ import annotations

import goldenmatch as gm
import numpy as np
import polars as pl

from .base import Record, cluster_by_pairwise

_MODES = {
    "auto": ("goldenmatch(auto)", "zero-config dedupe_df(name) -- auto-config picks the strategy"),
    "auto_fields": (
        "goldenmatch(auto+fields)",
        "zero-config dedupe_df(name+type+context) -- auto-config, multi-field",
    ),
    "auto_llm": (
        "goldenmatch(auto+llm)",
        "zero-config dedupe_df(name+type+context) + llm_scorer -- needs OPENAI_API_KEY",
    ),
}


class GoldenMatchAdapter:
    # Auto-config has mild EM-order non-determinism; the runner's re-run check
    # reports what actually happened rather than trusting this flag.
    deterministic = True

    def __init__(self, mode: str = "auto") -> None:
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.name, self.defaults = _MODES[mode]

    def resolve(self, records: list[Record]) -> list[list[int]]:
        # Resolve the full set in index order so __row_id__ == record index.
        ordered = sorted(records, key=lambda r: r.index)
        data: dict[str, list[str]] = {"name": [r.mention for r in ordered]}
        if self.mode in ("auto_fields", "auto_llm"):
            data["entity_type"] = [r.entity_type for r in ordered]
            data["context"] = [r.context for r in ordered]
        df = pl.DataFrame(data)

        # Zero-config: no exact/fuzzy kwargs -> dedupe_df calls auto_configure_df.
        kwargs = {"llm_scorer": True} if self.mode == "auto_llm" else {}
        result = gm.dedupe_df(df, **kwargs)

        return [
            list(info["members"])
            for info in result.clusters.values()
            if info.get("size", len(info["members"])) > 1
        ]


class GoldenMatchEmbAnnAdapter:
    """Embedding-ANN blocking using goldenmatch's OWN offline embedder.

    The lever the LLM experiment pointed at: generate candidate pairs by
    *semantic-ish similarity* instead of string blocking, then cluster. Uses
    ``goldenmatch.embeddings.inhouse.GoldenEmbedModel`` -- the product's in-house
    char-n-gram + linear-projection embedder: pure numpy, **no key, no torch, no
    cloud**, deterministic (fixed-seed random projection).

    Honest scope: because the inhouse embedder is char-n-gram based, its cosine
    approximates *character* overlap, not world knowledge. So it generates the
    candidates string-blocking misses for typo / org-suffix / cross-lingual
    *transliteration* (shared characters), but NOT for abbreviation or
    synonym/brand (IBM<->International Business Machines ~0.05; Coumadin<->warfarin
    ~0.02). Those need a semantic embedding model (sentence-transformers / cloud),
    which costs a key or torch -- the benchmark says so plainly rather than
    pretending the offline path closes that gap.

    At benchmark scale this computes exact cosine; the ANN index is the scale-out
    form of the same candidate-generation step. Name only, to isolate what the
    embedding itself bridges (apples-to-apples with the frameworks' name dedup).

    Swapping the embedder is the lever the LLM experiment pointed at to crack
    the two classes the offline path leaves open. ``provider`` selects it:
    ``None`` keeps the offline char-ngram model (the committed ``emb-ann`` row);
    a name like ``"openai"`` routes through ``goldenmatch.embeddings.providers``
    to a *semantic* embedder with world knowledge (``IBM`` <-> its expansion,
    ``Coumadin`` <-> ``warfarin``). Semantic providers cost a key or torch, so
    the runner gates them on availability and keeps them out of the committed,
    reproducible-by-anyone table -- recorded as prose, like the LLM experiment.
    """

    name = "goldenmatch(emb-ann)"
    deterministic = True

    def __init__(
        self,
        threshold: float = 0.5,
        *,
        provider: str | None = None,
        name: str | None = None,
        defaults: str | None = None,
    ) -> None:
        self.threshold = threshold
        self.provider = provider
        if name is not None:
            self.name = name
        elif provider is not None:
            self.name = f"goldenmatch(emb-{provider})"
        # else: keep the class-level "goldenmatch(emb-ann)" default.
        if defaults is not None:
            self.defaults = defaults
        elif provider is None:
            self.defaults = (
                f"inhouse char-ngram embedding (no key/torch) -> cosine>={threshold} "
                "candidate pairs (ANN at scale) -> union-find; name only"
            )
        else:
            self.defaults = (
                f"{provider} semantic embedding -> cosine>={threshold} candidate "
                "pairs (ANN at scale) -> union-find; name only"
            )

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self.provider is None:
            # fixed-seed random projection -> deterministic; no key, no torch.
            from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel

            return np.asarray(GoldenEmbedModel().embed(texts), dtype=np.float32)
        from goldenmatch.embeddings.providers import resolve_provider

        return np.asarray(resolve_provider(self.provider).embed(texts), dtype=np.float32)

    def resolve(self, records: list[Record]) -> list[list[int]]:
        ordered = sorted(records, key=lambda r: r.index)
        vecs = self._embed([r.mention for r in ordered])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.where(norms == 0.0, 1.0, norms)
        sim = vecs @ vecs.T  # cosine; index i == record i (ordered 0..n-1)

        thr = self.threshold

        def pred(a: Record, b: Record) -> bool:
            return bool(sim[a.index, b.index] >= thr)

        return cluster_by_pairwise(ordered, pred)
