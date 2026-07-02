# Substrate Config Suggester — Design (SP-C)

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Sub-project:** SP-C of the substrate-builder config-surface program (SP-A metric split #1371 · SP-B1 config #1373 · SP-B2 tuner #1375 · reset fix #1380 armed).

## Premise (reframed by the SP-B2 smoke)

The deterministic `for_profile` + `escalate` (SP-B1/B2) already reach the right levers via the ladder — an LLM that just proposes the *next config* would mostly duplicate it. But `for_profile` **cannot self-derive** whether a corpus *has* homographs or a *known schema* — the caller must set `expect_homographs` / `has_known_schema` / `relation_vocab` / `entity_type_vocab`. SP-C's LLM does the one thing deterministic can't: **read the corpus prose to perceive its structure** and propose those inputs. Measurement then verifies the resulting config beats the flags-off baseline before accepting — so the LLM can never make things worse than deterministic.

## Non-goals

- No escalation-replacement (the smoke showed it mostly matches deterministic).
- No production/no-gold gate (unsupervised-proxy verification is a documented follow-on; SP-C's verify is gold-based, on the bench).
- No new levers — SP-C only *supplies inputs* to `for_profile`.
- No change to `build_and_score_real`, `substrate_scorecard`, `for_profile`.

## Architecture (pure core + injected LLM/build, mirroring SP-B2)

New module `erkgbench/substrate_suggest.py`:

### 1. `CorpusFlags` (frozen dataclass) + `propose_corpus_flags(sample_docs, *, chat) -> CorpusFlags`
```
expect_homographs: bool = False
has_known_schema: bool = False
relation_vocab: tuple[str, ...] = ()
entity_type_vocab: tuple[str, ...] = ()
```
One **schema-constrained** LLM call over a bounded sample of doc texts (default first `sample_docs` ≤ N chars each). `chat(prompt) -> str` is the injected seam; the prompt asks the model to report, as JSON, whether the corpus contains same-name-different-entity pairs (homographs), and — if the relation set looks small/closed — a candidate `relation_vocab` + coarse `entity_type_vocab`. **Parsing + validation is the box-tested surface**: `_parse_flags(raw_json) -> CorpusFlags` tolerates fenced/messy JSON (reuse the extractor's JSON-salvage pattern), drops unknown keys, coerces types, and returns the all-False/empty default on unparseable output (never raises → a bad LLM read degrades to the deterministic baseline).

### 2. `suggest_substrate_config(docs, gold, qid_aliases, *, build_and_score, chat, profile=None, sample_docs=6) -> SuggestResult`
The **review_config self-verify** (the config-suggestion-kernel pattern):
```
profile   = profile or profile_corpus([d.text for d in docs])
baseline  = for_profile(profile)                         # flags OFF
flags     = propose_corpus_flags(sample(docs), chat)
proposed  = for_profile(profile, expect_homographs=flags.expect_homographs,
                         has_known_schema=flags.has_known_schema,
                         relation_vocab=flags.relation_vocab)   # entity_type_vocab via env (below)
base_sc   = build_and_score(baseline,  (docs, gold, qid_aliases))
prop_sc   = build_and_score(proposed,  (docs, gold, qid_aliases))
accepted  = _score(prop_sc) > _score(base_sc)            # SP-B2 _score; proposed > baseline, else fall back
winner    = proposed if accepted else baseline
# Stamp entity_type_vocab ONLY on an accepted proposed config that ALSO turned canon on (via
# expect_homographs) -- else the vocab is a no-op AND we'd dirty a canon-off winner. `accepted` does
# NOT imply expect_homographs (a has_known_schema-only proposal can be accepted with canon off), so
# BOTH terms are required (matches the MCP gate in section 3):
if accepted and flags.expect_homographs and flags.entity_type_vocab:
    winner = dataclasses.replace(winner, entity_type_vocab=flags.entity_type_vocab)
return SuggestResult(config=winner, flags=flags, accepted=accepted,
                     baseline_scorecard=base_sc, proposed_scorecard=prop_sc)
```
- `_score` is imported from `substrate_tuner` (relational.f1 + presence.coverage, or relational.f1 when presence None — the homograph engineered corpus has presence None, so the win shows as relational F1, which rises because `name_ci_type` recovers the precision `name_ci` loses to over-merged homographs).
- **Reproducibility:** `build_and_score` is `build_and_score_real`, which resets the LLM between builds (#1380) so the baseline-vs-proposed comparison is trustworthy (the reason #1 came first). If a build isn't reproducible, the ±noise could flip `accepted` on a marginal delta.
- **`entity_type_vocab` gating (review fix):** `for_profile` doesn't take `entity_type_vocab` (it's env-driven, `GOLDENGRAPH_ENTITY_TYPE_VOCAB`, and — verified — only bites when `entity_type_canon` is on). It is stamped via `dataclasses.replace` only when **both** `accepted` **and** `flags.expect_homographs` — because `entity_type_canon` turns on *only* via `expect_homographs` in `for_profile`, and `accepted` alone does NOT imply `expect_homographs` (a `has_known_schema`-only proposal can be accepted with canon off; §1 explicitly couples proposed `entity_type_vocab` to the *schema* signal, so vocab-without-homographs is the expected shape). Requiring both terms means we never stamp vocab onto a canon-off config. On the `accepted=False` fallback the winner is exactly `for_profile(profile)` (baseline), untouched — preserving the clean "fallback == deterministic baseline" guarantee. (SubstrateConfig HAS the `entity_type_vocab` field; `for_profile` just doesn't set it.)

### 3. Thin MCP tool `suggest_substrate_config` (no-gold surface)
Wraps **`propose_corpus_flags` only** → returns the LLM's suggested `SubstrateConfig` **labeled UNVERIFIED**. An MCP caller has a corpus but no gold, so the tool cannot run the self-verify; it returns the perception + a note to measurement-verify with gold on the bench. Build the config the SAME way as §2 (NOT `**flags` — `CorpusFlags` is a frozen dataclass whose `entity_type_vocab` field `for_profile` doesn't accept, so a splat would `TypeError`):
```
flags = propose_corpus_flags(sample, chat)
cfg = for_profile(profile_corpus(sample_texts),
                  expect_homographs=flags.expect_homographs,
                  has_known_schema=flags.has_known_schema, relation_vocab=flags.relation_vocab)
if flags.expect_homographs and flags.entity_type_vocab:
    cfg = dataclasses.replace(cfg, entity_type_vocab=flags.entity_type_vocab)
return {"config": cfg, "flags": flags, "verified": False,
        "note": "LLM perception only; measurement-verify with gold on the bench"}
```
(The gold-verified self-verify is the bench harness that PROVES the proposer works; the MCP tool is the production perception surface.) Lives alongside goldenmatch's `suggest_config` MCP registration.

### 4. Runner `run_substrate_suggest.py` + `modal_bench.py` `suggest` mode
Loads the **homograph engineered corpus** (`GOLDENGRAPH_BENCH_HOMOGRAPH=k` via `generate_engineered` + `emit_gold_mentions`, `qid_aliases=None`), runs `suggest_substrate_config`, prints baseline-vs-proposed scorecards + the flags + `accepted`, writes a report. The Modal smoke.
- **CRITICAL (silent-failure guard):** pass `corpus.documents` (the `Document` objects with `.text`/`.id`) as `docs` to both `suggest_substrate_config` and the sample — NOT `[d.text for d in ...]`. `build_and_score_real` only preserves the real `src::rel::dst` doc-ids when `docs[0]` has a `.text` attribute; raw strings get re-wrapped with synthetic `d{i}` ids, and the engineered gold oracle then matches nothing (both arms score ~0, silently). The proposer's *sample text* is `[d.text for d in docs[:sample_docs]]` (strings, for the prompt), but the build `docs` stay Documents.

## Testing (TDD, box-safe with FAKE `chat` + FAKE `build_and_score`)

`erkgbench/tests/test_substrate_suggest.py`, all pure:
- `parse_flags_clean_json` / `parse_flags_fenced` / `parse_flags_garbage_defaults` — `_parse_flags` salvages JSON, drops unknown keys, and returns the empty default on unparseable input (never raises).
- `propose_flags_calls_chat_once` — fake `chat` returns a scripted JSON; `propose_corpus_flags` returns the parsed `CorpusFlags`.
- `suggest_accepts_when_proposed_beats_baseline` — fake `build_and_score` returns a HIGHER `_score` for the `name_ci_type` config than for `name_ci` (key the fake on `config.xdoc_key`) → `accepted=True`, `config.xdoc_key=="name_ci_type"`.
- `suggest_falls_back_when_proposed_worse` — fake returns a LOWER `_score` for the proposed config → `accepted=False`, `config==for_profile(profile)` exactly (baseline, NO entity_type_vocab stamped — the LLM can't make it worse).
- `suggest_homograph_flags_apply_type_vocab` — fake scores proposed higher (so `accepted=True`); flags `expect_homographs=True` + `entity_type_vocab=(...)` → winning config has `xdoc_key=="name_ci_type"` AND `entity_type_canon is True` AND `entity_type_vocab` set.
- `suggest_vocab_without_homograph_not_stamped` — flags `expect_homographs=False, has_known_schema=True, entity_type_vocab=(...)`, fake scores proposed higher (`accepted=True` via schema_canon) → winner has `entity_type_canon is False` AND `entity_type_vocab == ()` (vocab NOT stamped on the canon-off config — the §2 gate requires `expect_homographs` too).
- `suggest_bad_llm_read_is_safe` — fake `chat` returns garbage → flags default → proposed==baseline → `accepted=False`, no crash.
- `mcp_suggest_returns_unverified_config` — the MCP wrapper returns a config + `verified=False` note without touching gold/build.

## Design choices flagged for review

- **Python-API-first; MCP tool is a thin no-gold wrapper.** The gold-verified self-verify is inherently a bench operation (needs gold + a real build); the MCP tool exposes only the LLM perception (unverified). This is the honest split, not a scope cut.
- **Accept metric = SP-B2 `_score`.** Consistent with the tuner. On the homograph corpus (presence None) that's relational F1; `name_ci_type`'s precision recovery raises it when homographs are prevalent. If the corpus has NO homographs, `name_ci_type` costs recall → lower F1 → `accepted=False` → baseline kept (correct).
- **The proposer LLM call is seeded** (`GOLDENGRAPH_LLM_SEED`, via the same `llm._chat`) so the perception is reproducible.
- **Smoke corpus = homograph engineered** (`HOMOGRAPH=k`), per Ben's steer. The measurable success: the LLM detects the injected homographs → `expect_homographs` → `name_ci_type`, and the self-verify accepts it (beats `name_ci` baseline on relational F1).

## Dependency / branch note

Needs `SubstrateConfig`/`for_profile` (SP-B1), `_score`/`build_and_score_real` (SP-B2 #1375, on main), and the **reset in `build_and_score_real` (#1380, armed not merged)** for a trustworthy verify. This branch (`feat/substrate-suggest`) is off main before #1380 merged. **Rebase onto `origin/main` after #1380 merges** before the smoke (else the verify rides on the noisy pre-reset build). The pure box tests don't need it (fake `build_and_score`).

## Follow-ons

- **No-gold production verify:** validate an unsupervised proxy (fragmentation/precision-proxy) against the gold `_score` on the bench, then let the MCP tool self-verify without gold.
- **Vocab proposal quality:** the arc's SCHEMA_CANON win used a curated `relation_vocab`; whether an LLM-proposed vocab matches it is a measurable follow-on (the homograph flag is the first, cheapest win).
