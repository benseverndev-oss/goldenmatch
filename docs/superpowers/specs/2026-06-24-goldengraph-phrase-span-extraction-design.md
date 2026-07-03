# goldengraph — phrase-span extraction lever (scoping)

**Status:** Part 1 SHIPPED (#1260); **Part 2 DEPRIORITIZED** — see the pivot below
**Date:** 2026-06-24
**Author:** measure-driven loop (follows the literal-attribute lever #1236/#1253)
**Parent:** `2026-06-22-goldengraph-qa-e2e-first-headline-handoff.md`

## 0. UPDATE 2026-06-24 — PIVOT: extraction is not the bottleneck, retrieval is

Part 1 (broadened literal typing → `ordinal|range|region|event`) shipped (#1260)
and was measured. The result **redirects this whole lever** and is worth pinning
at the top so no one builds Part 2 on the old premise.

**Part 1 works at the extraction layer.** A `distill_log=true` N=50 run (with the
distill logger fixed to actually record attributes, #1264) shows the extractor
emits the new types across 1000 docs: `date` 1198, `text` 744, `quantity` 722,
**`ordinal` 170, `range` 76, `region` 19, `event` 17** (88% of docs carry ≥1
attribute). And the *specific* phrase-gold values land as typed leaf nodes — the
smoking gun:
- `Piedmont -[average temperature range]-> "upper 40s-lower 50s °F" (range)` — the
  **exact** gold, captured. Yet the QA answered `"−15 °C"` (wrong).
- `Mississippi River -[rises in]-> "northern Minnesota" (text)` — captured. QA said
  "Gulf of Mexico".
- `Mega Drive -[is built on]-> "16-bit architecture" (text)` — captured. QA said "60%".

**So the binding constraint is RETRIEVAL/SYNTHESIS, not extraction.** The value
leaf exists 1-hop off its subject, but the multi-hop chain to the subject never
reaches it, or synthesis fails to select the predicate-matched attribute among the
subject's several. The earlier 2010-election case (gold extracted as an `event`
*entity*, still retrieval-missed) points the same way.

**Consequences:**
1. Part 1 succeeded at its layer and stays (it's the right representation).
2. **Part 2 (descriptive-span extraction machinery) is DEPRIORITIZED** — the
   values are already in the graph; more extraction won't move the bench. Building
   it would have solved the wrong problem.
3. The next lever is **retrieval/synthesis-side**: pull a reached entity's
   value-leaves into the answer ball, and have synthesis match the question's
   predicate to the attribute label. Pin the exact failure mode with a
   `GOLDENGRAPH_QA_TRACE=1` run on the phrase golds (EXTRACTION /
   RETRIEVAL-BROKEN-CHAIN / RETRIEVAL-BUDGET / SYNTHESIS) before designing it.

The original scoping below is retained for the record; sections 4-part-2 and 6
onward are superseded by this pivot.

---

## 1. Why — the measured loss bucket

The N=50 MuSiQue head-to-head (runs 28103014864 = `literal_attrs=true`,
28103022892 = control, both on `main` @ 95d29636, same fixed gpt-4o-mini judge)
gave a clean per-answer-type breakdown:

| answer_type | n | judge (literal) | judge (control) |
|-------------|---|-----------------|-----------------|
| date        | 4 | 0.250           | 0.000           |
| number      | 7 | 0.286           | 0.286           |
| **phrase**  | **9** | **0.000**   | **0.000**       |
| entity      | 30| 0.333           | 0.367           |

The literal lever did exactly its job (date 0→0.25) but netted to a 1-for-1 swap
(+1 date, −1 entity from graph bloat) — overall judge flat at 0.26. The next
untapped frontier is the **`phrase` bucket: 9/50 = 18% of golds, scoring 0.0
across BOTH runs** — neither the entity path nor the literal path can represent
these answers.

This is the largest single addressable bucket left. Even half-capturing it is
+0.08–0.09 judge — bigger than the entire date win — *if* it can be done without
re-triggering the entity regression that made literals a wash.

## 2. What a "phrase" gold actually is (the 9, verbatim)

The bucket is **not** homogeneous. Three sub-shapes:

**(A) True free-text descriptive spans — the predicate-object answer** (the hard core):
- "built on 16-bit architectures and offered improved graphics and sound"
  (Q: *Genesis's advantages over the Master System?*) → pred grabbed "1989"
- "rises in northern Minnesota and meanders slowly southwards"
  (Q: *direction of flow of the body of water…?*) → pred "south"
- "the novel of the same name by Robert Ludlum"
  (Q: *what was the story based on?*) → pred "Jason Bourne"

**(B) Ordinals / ranks** (cheap; a literal-typing extension):
- "third-largest" → pred "1st"
- "551-600" (QS ranking band) → pred empty

**(C) Qualified / range / region / event values** (literal-typing extension):
- "northeastern Oklahoma" (region = entity + directional qualifier) → pred empty
- "upper 40s–lower 50s °F" (qualified temperature range) → pred a coordinate
- "two" (a bare count, arguably mis-bucketed as phrase) → pred "40%"
- "the 2010 election" (event reference) → pred empty

The prediction column is the tell: 3/9 answered **empty** (no node to surface),
6/9 grabbed the **closest wrong literal/entity** in the ball. Both failure modes
mean *the correct answer span was never a retrievable node* — a representation
gap, not a retrieval-budget or synthesis-walk gap.

## 3. Root cause — phrase answers are blocked at BOTH ends

Grounded in current `main` source:

**Extraction won't emit spans.** `extract.py`:
- `relationships[].obj` is a 0-based index into `entities` — by construction an
  edge object must be a named entity, so a descriptive span can never be a
  relationship object (`extract.py:36`, `parse_extraction` drops non-int
  endpoints `:108`).
- The literal `attributes` channel (`_PROMPT_LITERALS`, `extract.py:32-46`) is
  framed exclusively for "dates, quantities, money amounts, measurements" with
  date/number examples, and the type enum is `date|quantity|text` (`:39`,
  `_ATTR_TYPES :90`). The model will not reliably route a multi-clause
  descriptive answer ("built on 16-bit architectures…") into that channel, and
  there is no signal telling it to.

**Synthesis forbids span answers.** `synthesize.py`:
- `_ANSWER_LITERAL` (`:81-89`) — even the literals-on prompt — says the answer
  "MAY be a literal VALUE leaf (a **date, quantity, or amount**…) when the
  question asks 'when', 'how much', or 'how many'" and explicitly: *"do NOT
  answer with a free-form description."* So even if a span node existed, the
  synthesis is instructed to avoid it and emit an exact entity/literal name.

The node *mechanism* already exists: `build_batch` materializes ANY attribute as
a `literal:<kind>` leaf, including `type:"text"` (`ingest.py:98-135`), and the
seed-fix (#1253) already keeps `literal:*` nodes out of query-seeding while
leaving them BFS-reachable. The gap is upstream (extraction) and downstream
(synthesis), not in storage/retrieval.

## 4. Design — two coordinated parts, bloat-aware

The literal lever's lesson is the binding constraint: **materializing more value
nodes bloats the graph and cost the entity bucket one question** (4800→6615
nodes in the earlier run). Descriptive spans are longer and more numerous than
dates, so naive "materialize every object span" would bloat far worse. The
design is therefore *selective + retrieval-isolated + synthesis-gated*.

### Part 1 — broaden literal typing (cheap, low-risk; sub-shapes B & C)
Extend the `attributes` taxonomy from `date|quantity|text` to add
`ordinal|range|region|event` (names TBD), with prompt examples drawn from the B/C
golds ("third-largest", "551-600", "upper 40s–lower 50s °F", "northeastern
Oklahoma", "the 2010 election"). These are still short, typed leaf values — same
node mechanism, same isolation, marginal bloat. This is a direct extension of
#1236 and should land first as the low-risk half.

### Part 2 — descriptive-span answer nodes (the hard core; sub-shape A)
A new extraction channel for the **salient descriptive object of an attribute-like
relation** — `entity -[predicate]-> <span>` where the span is free text. Crucial
guards so it doesn't repeat the bloat regression:
- **Selective, not exhaustive.** Only spans the extractor marks as *answer-bearing*
  (a salient property/description), capped per document (e.g. ≤2). NOT every
  modifier or clause. The prompt must steer hard here or bloat dominates.
- **Retrieval-isolated.** Materialize as a distinct typ (e.g. `span:descriptor`)
  that inherits the #1253 seed-exclusion AND is excluded from the entity-BFS node
  budget accounting — reachable only as a 1-hop leaf of its subject entity, never
  competing with entities for the answer-ball budget. This is the specific
  mechanism that should prevent the entity-bucket regression.
- **Synthesis-gated.** A new answer clause (gated on a `GOLDENGRAPH_SPAN_ATTRS`
  flag, mirroring `GOLDENGRAPH_LITERAL_ATTRS`) that PERMITS a descriptive-span
  answer when the question is non-factoid ("what were the advantages", "what is
  the direction of flow", "what was it based on"). Must NOT perturb the
  entity-only or literal-only prompts (byte-identical when the flag is off), per
  the #1236 discipline.

## 5. Hard success criterion

Not "phrase judge goes up" in isolation — **phrase judge up WITHOUT entity judge
down.** The literal lever passed the first and failed the second (net wash). The
retrieval-isolation guard (Part 2) is the design's answer to that; the
measurement must verify it held.

## 6. Phased plan

1. **Part 1 (literal-typing broaden)** — extend enum + prompt + `_ATTR_TYPES`;
   regression tests; ship behind the existing `GOLDENGRAPH_LITERAL_ATTRS`
   (or a new sibling flag). Low risk, lands first.
2. **Part 2a (extraction + node)** — span channel in `_PROMPT_SPANS`, parse,
   `build_batch` materialization as `span:*` with isolation; offline tests.
3. **Part 2b (retrieval isolation + synthesis)** — seed/budget exclusion for
   `span:*`, `_ANSWER_SPAN` clause; offline tests.
4. **Measure** — one traced N=50 MuSiQue run, flag on vs the control already
   captured (28103022892), under the LLM-judge metric. Read the per-type table:
   phrase delta AND entity delta. Sweep `node_budget` if the isolation didn't
   fully protect entities.

## 7. Risks / open questions

- **Extractor obedience.** Will gpt-4o-mini reliably emit a clean span for "the
  advantages" rather than a date/entity? The empty/wrong-literal predictions
  suggest the answer text IS in the source; the question is routing it to the
  span channel. May need few-shot examples or a 2-pass extract. **Measure before
  expanding.**
- **Judge fairness on spans.** The LLM-judge is the right scorer here (containment
  would over-credit verbose spans), but spans are exactly where judge calibration
  matters most — spot-check the judge verdicts on the 3 type-A golds.
- **Bloat vs the entity bucket** is the central risk; the isolation guard is a
  hypothesis to be measured, not a proven fix.
- **Sub-shape C overlap.** "two" is bucketed phrase but is really a count; some C
  items may already be reachable via Part 1 alone — measure Part 1 standalone
  before committing to Part 2's complexity.

## 8. Recommendation

Land **Part 1 first** (cheap literal-typing broaden, low bloat risk) and measure
it standalone. It may capture sub-shapes B and C (5 of the 9) on its own. Only
commit to Part 2's descriptive-span machinery — the bloat-risky half — if Part 1
leaves type-A spans (3 of 9) as the residual loss AND the entity bucket survived.
This sequences the cheap, safe win ahead of the expensive, risky one, and keeps
each step independently measurable on the bench.
