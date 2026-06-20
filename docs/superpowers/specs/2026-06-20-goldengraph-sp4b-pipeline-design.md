# goldengraph SP4b — extraction → resolve → store (Python pipeline) — design

**Status:** Design draft 2026-06-20. SP4b slice of SP4 (`2026-06-20-goldengraph-sp4-host-pipeline-design.md`). Awaiting approval → plan. **Depends on SP4a** (PyStore + communities in `goldengraph-native`, PR #1136, in queue).

**Surface:** a new standalone Python package `packages/python/goldengraph/`. **The "build a KG from text" path** — the first half of the standalone milestone.

---

## Motivation

SP4a gave Python a durable, queryable, bi-temporal store. SP4b feeds it: **text → LLM-extracted entities + relationships → zero-config resolution → durable store**. This is what makes goldengraph a standalone own-your-KG tool rather than a Rust engine. Entity resolution — the moat — runs via goldenmatch's existing zero-config controller, so duplicate surface forms across documents collapse into one durable entity (the thing the popular frameworks do badly).

## Package shape

`packages/python/goldengraph/` (PyPI `goldengraph`), mirroring the `goldenmatch-kg` precedent:
- **Excluded from the uv workspace** (`[tool.uv.workspace].exclude` in root `pyproject.toml`) — its LLM/native extras are heavy and shouldn't enter the main `uv.lock` (the `goldenmatch[native]` footgun). Its own CI lane; not in the `ci.yml` matrix; not (yet) a published-suite roster package.
- **Deps:** `goldenmatch` (zero-config `dedupe_df`, `record_fingerprint`, `BudgetTracker`) + `goldengraph-native` (the engine: `PyStore`, `build_graph`). LLM access via a minimal in-package protocol (below), real providers behind an optional extra.

## Modules

### `llm.py` — provider-agnostic LLM boundary
```
class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...
```
- Tests inject a deterministic stub (à la goldenmatch-kg's conftest). A thin OpenAI adapter ships behind a `[openai]` extra (uses the key from the env; reuses goldenmatch's `BudgetTracker` to cap spend). **Decision:** goldengraph owns this minimal protocol rather than coupling to goldenmatch's internal LLM client — it keeps extraction testable + provider-swappable. (Accuracy of real extraction is an SP6 concern, not an SP4b unit test.)

### `extract.py` — text → triples (NEW logic)
```
@dataclass
class Mention: name: str; typ: str
@dataclass
class Relationship: subj: int; predicate: str; obj: int   # subj/obj index into mentions
@dataclass
class Extraction: mentions: list[Mention]; relationships: list[Relationship]

def extract(text: str, llm: LLMClient) -> Extraction
```
- A prompt template instructs the LLM to return strict JSON (`{"entities":[{"name","type"}], "relationships":[{"subj","predicate","obj"}]}` with subj/obj as entity indices). `extract` parses + validates that JSON into the typed result; malformed/out-of-range indices are dropped (defensive — LLMs drift). goldenmatch has no text→triples extractor (`llm_extract_features` is ER *feature* extraction), so this is genuinely new; only the LLM transport + budget are reused.

### `resolve.py` — mentions → entities + record keys (REUSE the controller)
```
@dataclass
class ResolvedEntity: local_id: int; canonical_name: str; typ: str
                      surface_names: list[str]; record_keys: list[str]; member_idx: list[int]
def resolve(mentions: list[Mention]) -> list[ResolvedEntity]
```
- Builds a Polars frame of mention `(name, typ)` and calls **`goldenmatch.dedupe_df`** (the zero-config auto-config controller — the moat), exactly as goldenmatch-kg's `resolve_entities` does (mirror its ~40-line group-extraction, including singletons; do NOT depend on the goldenmatch-kg package — depend on `goldenmatch` directly). Each group → a `ResolvedEntity`: `canonical_name` = longest member name; `surface_names` = distinct member names; `record_keys` = `goldenmatch.record_fingerprint` (the `:h1:` key) per member mention. `member_idx` maps back to extraction indices (for edge remap).

### `ingest.py` — the end-to-end path
```
def ingest(text: str, store: PyStore, *, at: int, llm: LLMClient,
           valid_from: int | None = None) -> None
```
- `extract` → `resolve` → build a `StoreBatch` dict → `json.dumps` → `store.append(json)`. Entities map 1:1 to `ResolvedEntity` (`local_id`); build a `mention_idx → local_id` lookup from `ResolvedEntity.member_idx`, then remap each extracted `Relationship`'s `subj`/`obj` into a `BatchEdge` (`valid_from = valid_from or at`, `valid_to = None`, `source_refs = []` or a caller-supplied doc id; `ingested_at = at`). **Edge cases (specify in the plan):** a relationship whose endpoints resolve to the SAME entity (self-loop after dedup) → drop it (SP1/SP2 retrieval treats self-loops as noise); a relationship index with no owning entity → can't happen (singletons are included in `resolve`), but guard defensively. Reuses SP4a's JSON `append` boundary.

## Determinism + testing

- **Mocked-LLM tests (deterministic):** a stub `LLMClient` returns fixed JSON for a fixed input → `ingest` into a `PyStore` → `store.as_of(...).query(...)` asserts the expected entities/edges. To assert "duplicates collapse" WITHOUT a flaky fuzzy merge, use **exact-duplicate mentions** (identical `name`+`typ`, which dedup version-stably) OR inject a deterministic `resolve` stub — NOT a fuzzy toy-merge (see the caveat below). This verifies the **binding/marshaling + the resolve→store wiring**, not LLM or fuzzy-resolution accuracy.
- **Resolution caveat (carried from goldenmatch-kg):** zero-config `dedupe_df` on a tiny toy frame commits a degenerate config whose fuzzy merges vary by goldenmatch version/process — so assert on the *plumbing* (a hand-curated mention set with an unambiguous duplicate, or inject resolution), NOT on a flaky toy-merge. The moat's accuracy is covered by SP6's eval + goldenmatch's own parity suite.
- **Real-LLM lane:** an opt-in test (skipped without `OPENAI_API_KEY`) runs the OpenAI adapter end-to-end on a short passage. Never required.

## CI

A new informational lane `goldengraph-pipeline.yml` (mirrors `goldenmatch-kg.yml`): install `goldenmatch` + build/install `goldengraph-native` (maturin) + install `goldengraph`, run pytest with the mocked LLM. Triggers on `packages/python/goldengraph/**` + the native crate. Informational (not `ci-required`) — confirm green before arming auto-merge (the only real-execution signal for the pipeline).

## Non-goals (SP4b)

Retrieval / synthesis / NL query / text-to-Cypher (**SP4c**). Embedding (SP4c). Community summaries (SP4c — uses SP4a `communities()`). WASM/C (SP5). The eval (SP6). Real-LLM accuracy guarantees (SP6). Publishing `goldengraph` to PyPI / MCP roster (a later rollout once the pipeline is real).

## Risks / open questions (resolve in the plan)

- **`record_key` granularity + the limit of store-side reconciliation (honest):** key on `record_fingerprint({"name": ..., "typ": ...})` — `record_fingerprint` takes a **dict**, and the dict keys must be constructed identically on every call or the fingerprint drifts and cross-document reconciliation silently breaks (pin the exact dict keys in the plan + assert in a test). Identical surface forms across documents share a key → reconcile to the same entity (desired). BUT a distinct-but-resolved pair only stays linked **while each batch's resolution keeps emitting both keys on one entity**: SP4a's store treats a batch's `record_keys` as AUTHORITATIVE (it does not union prior survivor keys, and emits a Split when a stored entity's keys land across batch entities). So **the store does NOT persist a fuzzy link across batches** — reconciliation is per-batch-resolution-driven. If document N+1 mentions only one surface form, the store sees only that key; it won't back-stop resolution drift. This is acceptable for SP4b (it matches the "each batch is a full resolution pass" contract), but the implementer must not assume the store remembers fuzzy links the resolver didn't re-emit.
- **`dedupe_df` output shape:** `resolve_entities` reads `result.clusters[*].{members,size}`; mirror exactly (members are row indices). Pin in the plan against the installed goldenmatch.
- **Package not in uv workspace** → its own lockfile/lane; the local dev loop needs `goldenmatch` + the `goldengraph-native` wheel installed (maturin) in a dedicated venv (the SP4a exFAT `CARGO_TARGET_DIR=C:` + explicit `VIRTUAL_ENV` gotchas apply).
