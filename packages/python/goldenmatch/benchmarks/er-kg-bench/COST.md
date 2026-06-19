# ER-KG-Bench cost axis — LLM spend to resolve entities

The board scores accuracy (P/R/F1) and wall-clock (`ms`). This doc adds the axis a
knowledge-graph builder actually pays for: **LLM calls to resolve entities.** A KG is
only as cheap as its entity-resolution layer, and the frameworks here meter that layer
per record. goldenmatch resolves deterministically (fuzzy + auto-config) or with an
offline embedder — **0 LLM calls, $0** — and still tops every framework default on
this corpus. The point of this page is to put accuracy and per-entity cost in one
table so "ER → cost-effective KG" is a measured side-by-side, not a claim.

## Headline

| approach | ER mechanism | LLM calls / record | $ / 1000 entities | deterministic | F1 |
|---|---|---|---|---|---|
| goldenmatch `auto` | fuzzy + auto-config | **0** | **$0.00** | yes | 0.520 |
| goldenmatch `auto+fields` | fuzzy + auto-config, multi-field | **0** | **$0.00** | yes | **0.602** |
| goldenmatch `emb-ann` | offline char-ngram ANN candidate gen | **0** | **$0.00** | yes | 0.440 |
| goldenmatch `auto+llm` *(optional)* | + LLM on borderline pairs only | keyed lane → `results/` | keyed lane → `results/` | no | keyed lane |
| goldenmatch `emb-openai` *(optional)* | semantic ANN candidate gen | keyed lane → `results/` | keyed lane → `results/` | yes\* | keyed lane |
| mem0 — MD5 floor | exact content hash | **0** | **$0.00** | yes | 0.066 |
| mem0 — LLM add/merge | per-`add()` LLM fact extraction + merge | **~2** *(probe-measured)* | ~$0.33 *(derived)* | no | **0.048** |
| Graphiti — MinHash/Jaccard floor | deterministic | **0** | **$0.00** | yes | 0.093 |
| Graphiti — full (LLM fallback) | + LLM per unresolved node | ~1 *(characterized)* | ~$0.17 *(derived)* | no | — |

\* `emb-openai` clustering is deterministic *given* the embeddings, but the embeddings
themselves are a paid API call — the row counts that call.

**goldenmatch's deterministic ER beats every framework default here at 0 LLM calls /
$0.** The frameworks' LLM resolution layers cost real money and, where measured
(mem0), buy *worse* accuracy: mem0's LLM add/merge layer (0.048) scores **below its own
free MD5 floor** (0.066) while spending ~2 calls per record. For KG construction that
means deterministic + embedding ER gives equal-or-better entities at a fraction of the
per-entity LLM cost — you do not have to meter an LLM to get the better graph.

## How the cost numbers are produced

Two sources, both labeled in the table so nothing reads as a black box.

**Measured (goldenmatch rows).** goldenmatch's `BudgetTracker` computes
`total_cost_usd` at published **gpt-4o-mini** rates — `$0.00015 / 1K` input,
`$0.0006 / 1K` output (`goldenmatch/core/llm_budget.py`). The `auto+llm` adapter runs
with a budget-bearing config, and `DedupeResult.llm_cost` now surfaces
`{llm_calls, llm_tokens, llm_usd}` straight from that tracker. So the goldenmatch LLM
row's `$ / 1000 entities = total_cost_usd / records × 1000` is a **real measured
number**, filled from the keyed CI lane (it can't run on the committed offline board —
no key). The deterministic rows are `$0` by construction (no LLM call is made).

**Derived (framework rows).** mem0 and Graphiti expose no cost tracker, so their `$` is
derived from their call count at the **same gpt-4o-mini rate**, with one stated token
assumption: a deliberately modest **650 tokens/call** (~500 input + ~150 output) for a
short entity-merge prompt → **~$0.000165 / call**. Then
`$ / 1000 entities = calls/record × 1000 × $0.000165`.
- **mem0 — LLM add/merge:** ~2 calls/record (probe-measured, `adapters/FIDELITY.md`
  Phase 3) → ~**$0.33 / 1000**.
- **Graphiti — full:** ~1 call per node the deterministic floor leaves unresolved;
  this corpus is recall-bound so the floor merges few and nearly every node escalates
  → ~1 call/record → ~**$0.17 / 1000** (characterized from architecture, not run).

Using one rate everywhere keeps the column internally consistent; the assumption is
printed here so the derived figures are honest, not asserted.

## Why the LLM layers don't pay for themselves here

The corpus is dominated by **recall-bound** classes (abbreviation, synonym/brand,
cross-lingual). Three independent measurements (`adapters/FIDELITY.md` Phases 2-3,
`RECALL-LEVER.md`) show those classes fail at **candidate generation**, not at scoring:
the pairs are never proposed, so a per-pair LLM judge or a precision-side cosine term
has nothing to act on. mem0 goes further into the red because it resolves **memories,
not entities** — its extraction front-end drops bare mentions ("IBM") and files each
surface-form variant as a separate dated memory, so the LLM layer merges zero variants
*and* loses some exact-duplicate recall the MD5 floor keeps. The lever that actually
moves these classes is a better **candidate generator** (semantic embedding ANN), and
the free multilingual model does it for $0 — see `RECALL-LEVER.md`.

## Reproduce

The committed board is all-deterministic / $0 (`RESULTS.md` now carries an `LLM?`
yes/no column so "every reproducible row here is free" is visible at a glance). The
goldenmatch LLM cells above are filled from the keyed lane (`OPENAI_API_KEY` present);
`results/results.json` carries the per-row `cost: {llm_calls, llm_tokens, llm_usd}`.
mem0/Graphiti `$` are derived (no live full-pipeline run) per the assumption above.

## Out of scope

- No live mem0/Graphiti full-LLM pipeline run — the probe + architecture
  characterization supply the contrast (see `adapters/FIDELITY.md`).
- No per-token cost on deterministic rows ($0 by construction).
- One stated cheap-model price; no multi-model price sweep.
