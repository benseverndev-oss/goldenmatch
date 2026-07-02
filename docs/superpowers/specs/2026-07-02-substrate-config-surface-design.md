# Substrate Config Surface — Design (SP-B1)

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Sub-project:** SP-B1 of the substrate-builder config-surface program (SP-A metric split shipped #1371; SP-B2 staged ejection harness + SP-C MCP/LLM loop follow this).

## Problem

~30 scattered `GOLDENGRAPH_*` env vars gate the substrate levers (verified in SP-A). There is no structured config object and no sane-default picker: a human or agent flips raw env strings with no validation and no guidance on which *combination* is good. SP-A gave the metric split + `LEVER_AXIS_MAP` (which lever moves which axis). SP-B1 gives the **config object those levers live in** + a **deterministic rule-table** that picks a sane-default config from cheap corpus signals — the goldenmatch `AutoConfigController` v3 rule-table analog, ported to the substrate builder. This is the pure, box-safe deterministic foundation the SP-B2 staged harness and the SP-C LLM loop sit on.

## Non-goals

- No staged build harness / ejection gates (SP-B2).
- No MCP tool / LLM (SP-C).
- No refactor of the ~30 call-site env reads — `apply()` materializes a config to env, working WITH the existing reads (YAGNI; a full threaded-config refactor is deferred).
- No new lever behavior — SP-B1 only *structures* existing levers.

## Deliverables

New module `packages/python/goldengraph/goldengraph/config.py` (pure, box-safe — no LLM, no build, no native). Becomes the lever registry SP-A's `KNOWN_LEVERS` test deferred to.

### 1. `SubstrateConfig` (frozen dataclass)

One typed field per lever + sub-params. Defaults = the engine's current defaults (a default-constructed `SubstrateConfig` materializes to a no-op env, so `apply()`ing it reproduces today's behavior):

```
xdoc_key: str = ""                       # "" | "name" | "name_ci" | "name_ci_type"  (""=(name,typ))
chunk_extract: bool = False
chunk_sentences: int = 6
chunk_overlap: int = 2
entity_type_canon: bool = False
entity_type_vocab: tuple[str, ...] = ()  # () = engine default 4-type (person/organization/concept/other)
schema_canon: bool = False
relation_vocab: tuple[str, ...] = ()     # () = unset
extractor: str = "api"                   # "api" | "rebel" | "gliner"
relation_reprompt: bool = False          # REFUTED (seed-determinism #1360); kept ONLY for SP-C measurement-gating
rebel_fuse: bool = False                 # REFUTED (breaks precision #1357)
extract_recall: bool = False             # REFUTED (trades edges for entity noise #1348)
```

`__post_init__` validates: `xdoc_key in {"", "name", "name_ci", "name_ci_type"}`; `extractor in {"api", "rebel", "gliner"}`; `chunk_sentences >= 1`; `0 <= chunk_overlap < chunk_sentences`. Invalid → `ValueError`. (Frozen + validated → a config is a safe, immutable value object to pass around and log.)

### 2. `MANAGED_ENV_VARS` + `to_env()`

- `MANAGED_ENV_VARS: tuple[str, ...]` — the exact set of `GOLDENGRAPH_*` names this config owns (one per field, + `GOLDENGRAPH_CHUNK_SENTENCES`/`_CHUNK_OVERLAP`/`_ENTITY_TYPE_VOCAB`/`_RELATION_VOCAB`). The single source of truth for what `apply()` snapshots/restores.
- `to_env() -> dict[str, str]` — materialize the config to a **total** env map (every managed key present, so applying a config fully determines the env — no ambient `GOLDENGRAPH_*` leaks through). Bool → `"1"`/`"0"`; `xdoc_key`/vocabs empty → `""`; tuple → comma-joined. Deterministic.

### 3. `apply()` — context manager

```
with config.apply():
    ingest_corpus(...)   # every GOLDENGRAPH_* read inside sees this config
```

Snapshot the current values of every `MANAGED_ENV_VARS` key (recording *absent* vs *present-with-value*), set them from `to_env()`, `yield`, then restore exactly (delete keys that were absent before; restore prior values). **Thread note (documented in the docstring):** env is process-global; `ingest_corpus` sets the config once and then fans per-doc extraction out to threads that *inherit* the process env, so this is safe for the intended one-config-per-build use. It is NOT safe for two different configs building concurrently in one process — out of scope.

### 4. `CorpusProfile` + `profile_corpus()`

```
@dataclass(frozen=True)
class CorpusProfile:
    n_docs: int
    mean_sentences_per_doc: float
    mean_chars_per_doc: float
```

`profile_corpus(docs: Sequence[str]) -> CorpusProfile` — cheap signals from RAW text only (no LLM, no build). Sentence count via a simple `[.!?]`-boundary split (reuse `chunk_extract`'s splitter if cheaply importable without side effects, else a local 3-line helper — decided at implementation, whichever avoids importing the LLM path). Empty corpus → zeros.

### 5. `for_profile()` — the deterministic rule table

```
def for_profile(profile: CorpusProfile, *, has_known_schema: bool = False,
                expect_homographs: bool = False, relation_vocab: tuple[str, ...] = ()) -> SubstrateConfig
```

Encodes the arc's MEASURED findings as deterministic rules (each cites its report in a comment):

| condition | sets | evidence |
|---|---|---|
| base (always) | `xdoc_key="name_ci"` | near-universal relational win L0/L1/L2 (#1331/#1340/#1341) |
| `expect_homographs` | `xdoc_key="name_ci_type"`, `entity_type_canon=True` | homograph-safe, ~0.06 recall cost, 4-type vocab (#1335/#1336) |
| `mean_sentences_per_doc >= CHUNK_MIN_SENTENCES` (default 8) | `chunk_extract=True` (6,2) | chunking win on dense multi-sentence docs; no-op + 4-10x cost on short docs (#1350) |
| `has_known_schema` | `schema_canon=True`, `relation_vocab=<given>` | closed-vocab predicate win (SCHEMA_CANON arc) |
| — | refuted levers stay `False` | reprompt/rebel/extract_recall all refuted |

`CHUNK_MIN_SENTENCES` is a module constant, env-overridable (`GOLDENGRAPH_AUTOCFG_CHUNK_MIN_SENTENCES`).

### 6. Tests (`packages/python/goldengraph/tests/test_config.py`, pure box-safe)

- `config_validation` — bad `xdoc_key`/`extractor` raise; `chunk_overlap >= chunk_sentences` raises; valid config constructs.
- `default_config_is_noop_env` — `SubstrateConfig().to_env()` sets `xdoc_key=""`, all bools `"0"` (reproduces engine default).
- `to_env_maps_types` — bool→1/0, tuple→csv, name_ci_type present.
- `apply_sets_and_restores` — inside `apply()` the env reflects `to_env()`; after, a key that was ABSENT before is deleted, a key that had a PRIOR value is restored to it.
- `profile_corpus_signals` — hand docs → correct n_docs / mean_sentences / mean_chars; empty → zeros.
- `for_profile_short_docs_no_chunking` — low mean_sentences → `chunk_extract False`, `xdoc_key "name_ci"`.
- `for_profile_dense_docs_enables_chunking` — high mean_sentences → `chunk_extract True`, (6,2).
- `for_profile_homographs` — `expect_homographs=True` → `xdoc_key "name_ci_type"`, `entity_type_canon True`.
- `for_profile_known_schema` — `has_known_schema=True, relation_vocab=(...)` → `schema_canon True` + vocab set.
- `for_profile_never_selects_refuted` — no rule sets reprompt/rebel/extract_recall.
- `config_fields_cover_known_levers` (skip-if-erkgbench-unimportable) — every `KNOWN_LEVERS` key is a `SubstrateConfig` field, keeping the SP-A contract and SP-B object in sync.

## Design choices flagged for review

- **Rule-table thresholds are hypotheses.** `CHUNK_MIN_SENTENCES=8` comes from the wiki finding (leads ~20 sentences, chunking won at (6,2)); it is a documented, env-overridable default. SP-B2 (which can actually *score* a config) is where these get measured/tuned — SP-B1 just ships defensible starting rules.
- **`apply()` mutates process-global env.** Acceptable for the single-build use; explicitly flagged not-thread-safe for concurrent different configs.
- **Refuted levers are IN the config** (so SP-C can measurement-gate a re-test) but default `False` and are never selected by `for_profile`.
- **`to_env()` is total** (emits every managed key including `"0"`/`""`) so `apply()` is leak-proof against ambient `GOLDENGRAPH_*`. Alternative (emit only non-defaults) was rejected: it would let a stale env var bleed into a build.

## Follow-ons

- **SP-B2:** staged build harness (profile → sample-extract → slice-build → full-build) with ejection gates emitting the SP-A `substrate_scorecard`; consumes `SubstrateConfig` + `for_profile` as the initial pick and routes ejections via `LEVER_AXIS_MAP[failing_axis]`.
- **SP-C:** `suggest_substrate_config` MCP tool + bounded LLM tweak loop; final config must beat the `for_profile` baseline on the scorecard (the `review_config` self-verify).
