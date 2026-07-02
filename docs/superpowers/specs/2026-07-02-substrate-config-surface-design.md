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

**Note:** the config's `extractor` set is stricter than the engine — the engine also accepts `""`/`"llm"` as aliases for `api` (ingest.py:643), but the config canonicalizes to `"api"` and rejects the aliases with `ValueError`. Callers pass the canonical `"api"`; documented so a caller passing `"llm"` isn't surprised.

### 2. `MANAGED_ENV_VARS` + `to_env()`

- `MANAGED_ENV_VARS: tuple[str, ...]` — the exact `GOLDENGRAPH_*` names this config owns. That is **one var per field (12 total)** — `GOLDENGRAPH_XDOC_KEY`, `_CHUNK_EXTRACT`, `_CHUNK_SENTENCES`, `_CHUNK_OVERLAP`, `_ENTITY_TYPE_CANON`, `_ENTITY_TYPE_VOCAB`, `_SCHEMA_CANON`, `_RELATION_VOCAB`, `_EXTRACTOR`, `_RELATION_REPROMPT`, `_REBEL_FUSE`, `_EXTRACT_RECALL` — **plus one leak-guard var, `GOLDENGRAPH_SCHEMA_DISCOVER`** (13 total). `SCHEMA_DISCOVER` is not a config field but MUST be managed: if it is ambient `=1`, `ingest_corpus` runs schema *discovery* and ignores `GOLDENGRAPH_RELATION_VOCAB` (ingest.py:631-635, 885), silently defeating the `has_known_schema` rule. `to_env()` always emits `GOLDENGRAPH_SCHEMA_DISCOVER="0"`. This is the single source of truth for what `apply()` snapshots/restores.
- `to_env() -> dict[str, str]` — materialize the config to a **total** env map over `MANAGED_ENV_VARS` (every managed key present, so applying a config fully determines those keys — no ambient value of a *managed* var leaks through). Bool → `"1"`/`"0"`; `xdoc_key`/vocabs empty → `""`; tuple → comma-joined. Deterministic.
- **Residual (documented, not fixed in SP-B1):** ~18 other substrate `GOLDENGRAPH_*` vars (e.g. `LITERAL_ATTRS`, `EXTRACT_JSON_MODE` [default-on], `CROSS_DOC_LINK`, `PROFILE_LINK`, link/merge thresholds) are NOT managed and still read from ambient env. None of them *defeats a `for_profile` rule* (only `SCHEMA_DISCOVER` did, hence its inclusion), so they are left unmanaged for now; SP-B2 can widen the managed set if a gate proves to interact. The config is leak-proof over its **managed** keys, not over all `GOLDENGRAPH_*`.

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

`profile_corpus(docs: Sequence[str]) -> CorpusProfile` — cheap signals from RAW text only (no LLM, no build). Sentence count via a **local** `(?<=[.!?])\s+` split helper defined in `config.py`. (Reusing `chunk_extract`'s splitter is rejected: importing `goldengraph.chunk_extract` runs `from .llm import LLMClient` at module top — it drags in the LLM path, which `config.py` must stay free of.) Empty corpus → zeros.

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

**Rule precedence (explicit):** the base rule always sets `xdoc_key="name_ci"`; the `expect_homographs` rule **overrides** it to `"name_ci_type"` and is the only rule that sets `entity_type_canon=True`. The rows are not independent — homograph wins over base. `chunk_extract` and `schema_canon` are orthogonal (compose freely). Implement as: start from base, then apply homograph override, then the chunk and schema rules.

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
- **`to_env()` is total** (emits every managed key including `"0"`/`""`) so `apply()` is leak-proof over its **managed** keys (the 12 field vars + `SCHEMA_DISCOVER` guard). Alternative (emit only non-defaults) was rejected: it would let a stale *managed* env var bleed into a build. It does NOT manage the ~18 other substrate vars (documented in §2); only `SCHEMA_DISCOVER` is pulled in because it alone can defeat a `for_profile` rule.

## Branch / dependency note

SP-B1's runtime code (`config.py`) does NOT depend on SP-A's code — it lives in the `goldengraph` package and only touches `GOLDENGRAPH_*` env semantics. The ONLY cross-dependency is the optional consistency test `config_fields_cover_known_levers`, which imports `erkgbench.substrate_eval.KNOWN_LEVERS` (added in SP-A #1371). SP-A merged to `main` but this branch (`feat/substrate-config`) was cut before that merge landed, so `KNOWN_LEVERS`/`substrate_scorecard` are not yet present here. Implementation handling:
- The consistency test **skips** if `KNOWN_LEVERS` is unimportable (so SP-B1 is testable standalone).
- Before the final PR, **rebase `feat/substrate-config` onto `origin/main`** (after #1371 has merged) so the consistency test actually runs and the SP-A↔SP-B contract is enforced. If #1371 has not merged by then, note it in the PR and land the skip.

## Follow-ons

- **SP-B2:** staged build harness (profile → sample-extract → slice-build → full-build) with ejection gates emitting the SP-A `substrate_scorecard`; consumes `SubstrateConfig` + `for_profile` as the initial pick and routes ejections via `LEVER_AXIS_MAP[failing_axis]`.
- **SP-C:** `suggest_substrate_config` MCP tool + bounded LLM tweak loop; final config must beat the `for_profile` baseline on the scorecard (the `review_config` self-verify).
