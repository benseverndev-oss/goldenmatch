"""SP-C substrate config suggester.

An LLM reads a sample of the corpus and proposes the corpus-characteristic inputs `for_profile` cannot
self-derive (homographs / known schema / vocabs). `suggest_substrate_config` then measurement-verifies
the resulting config against the flags-off baseline and accepts only a net improvement -- so the LLM can
never do worse than the deterministic baseline. `chat` and `build_and_score` are injected (box-testable
with fakes). See docs/superpowers/specs/2026-07-02-substrate-suggest-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from goldengraph.config import SubstrateConfig, for_profile, profile_corpus

from erkgbench.substrate_tuner import (  # noqa: F401 (real adapter for the runner)
    _score,
    build_and_score_real,
)


@dataclass(frozen=True)
class CorpusFlags:
    """The corpus-characteristic inputs `for_profile` needs but can't self-derive. All-off default =
    the deterministic baseline (a bad/empty LLM read degrades to it)."""
    expect_homographs: bool = False
    has_known_schema: bool = False
    relation_vocab: tuple[str, ...] = ()
    entity_type_vocab: tuple[str, ...] = ()


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _vocab(v) -> tuple[str, ...]:
    """A list of non-empty strings -> lowercased tuple; anything else -> ()."""
    if not isinstance(v, list):
        return ()
    return tuple(s.strip().lower() for s in v if isinstance(s, str) and s.strip())


def _parse_flags(raw: str) -> CorpusFlags:
    """Salvage the LLM's JSON into CorpusFlags. Fence-tolerant; drops unknown keys; coerces types;
    returns the all-off default on ANYTHING unparseable (never raises) so a bad read == baseline."""
    try:
        data = json.loads(_strip_fence(raw))
    except Exception:  # noqa: BLE001 -- any parse failure degrades to the deterministic baseline
        return CorpusFlags()
    if not isinstance(data, dict):
        return CorpusFlags()
    return CorpusFlags(
        expect_homographs=bool(data.get("expect_homographs", False)),
        has_known_schema=bool(data.get("has_known_schema", False)),
        relation_vocab=_vocab(data.get("relation_vocab")),
        entity_type_vocab=_vocab(data.get("entity_type_vocab")),
    )


_PROMPT = """You are analyzing a document corpus to configure an entity-resolution pipeline.
Read the sample documents and answer STRICTLY as a JSON object with these keys:
- "expect_homographs": true if the corpus contains DISTINCT entities that share the SAME name
  (e.g. Apple the company vs Apple the fruit), else false.
- "has_known_schema": true if the relationships form a small, closed set worth constraining, else false.
- "relation_vocab": a list of the canonical relation names if has_known_schema, else [].
- "entity_type_vocab": a list of coarse entity types (e.g. ["person","organization","concept"]) if
  expect_homographs, else [].
Output ONLY the JSON object, no prose.

SAMPLE DOCUMENTS:
{sample}
"""


def propose_corpus_flags(sample_docs, *, chat) -> CorpusFlags:
    """One schema-constrained LLM call (`chat(prompt) -> str`) over the sample -> CorpusFlags. Any
    unparseable output degrades to the all-off default (see _parse_flags)."""
    sample = "\n\n".join(f"[doc {i}] {t}" for i, t in enumerate(sample_docs))
    return _parse_flags(chat(_PROMPT.format(sample=sample)))


@dataclass(frozen=True)
class SuggestResult:
    config: SubstrateConfig
    flags: CorpusFlags
    accepted: bool
    baseline_scorecard: dict
    proposed_scorecard: dict


def suggest_substrate_config(docs, *, gold, qid_aliases, build_and_score, chat,
                             profile=None, sample_docs=6) -> SuggestResult:
    """LLM proposes for_profile flags from a corpus sample; accept the proposed config ONLY if it beats
    the flags-off baseline on `_score` (else fall back to baseline). `docs` must be Document objects
    (build_and_score needs .text/.id). `build_and_score(config, (docs, gold, qid_aliases))` injected."""
    profile = profile or profile_corpus([d.text for d in docs])
    sample = [d.text for d in docs[:sample_docs]]
    flags = propose_corpus_flags(sample, chat=chat)

    baseline = for_profile(profile)
    proposed = for_profile(profile, expect_homographs=flags.expect_homographs,
                           has_known_schema=flags.has_known_schema, relation_vocab=flags.relation_vocab)
    dataset = (docs, gold, qid_aliases)
    base_sc = build_and_score(baseline, dataset)
    prop_sc = build_and_score(proposed, dataset)
    accepted = _score(prop_sc) > _score(base_sc)
    winner = proposed if accepted else baseline
    # Stamp entity_type_vocab ONLY on an accepted homograph winner (canon is on only via
    # expect_homographs; `accepted` alone does NOT imply it -- a schema-only proposal can be accepted
    # with canon off). Both terms required -> never dirties a canon-off config.
    if accepted and flags.expect_homographs and flags.entity_type_vocab:
        winner = replace(winner, entity_type_vocab=flags.entity_type_vocab)
    return SuggestResult(winner, flags, accepted, base_sc, prop_sc)


def suggest_substrate_config_unverified(sample_texts, *, chat, sample_docs=6) -> dict:
    """No-gold MCP surface: return the LLM's PERCEIVED config from a corpus sample, labeled UNVERIFIED
    (an MCP caller has no gold to self-verify). `sample_texts` = list of raw doc strings."""
    sample = list(sample_texts[:sample_docs])
    flags = propose_corpus_flags(sample, chat=chat)
    cfg = for_profile(profile_corpus(sample), expect_homographs=flags.expect_homographs,
                     has_known_schema=flags.has_known_schema, relation_vocab=flags.relation_vocab)
    if flags.expect_homographs and flags.entity_type_vocab:
        cfg = replace(cfg, entity_type_vocab=flags.entity_type_vocab)
    return {"config": cfg, "flags": flags, "verified": False,
            "note": "LLM perception only; measurement-verify with gold on the bench"}
