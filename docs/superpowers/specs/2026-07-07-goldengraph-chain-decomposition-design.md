# GoldenGraph chain decomposition — design

**Status:** design draft 2026-07-07. Awaiting approval → plan.
**Owner:** ER platform.
**Related:** `2026-07-07-goldengraph-path-aware-retrieval-design.md` (+ its plan +
`results/RESULTS_PATH_AWARE_RETRIEVAL.md` — Levers A & C refuted), `route.py`
(`resolve_profile`/`plan_query`/`LLMQueryClassifier`/`_extract_chain_slots`), `trace_chain` +
`_rel_match` in `answer.py`, `erkgbench/qa_e2e/{corpora.py,router_paraphrases.py,router_eval.py}`.

## 1. The evidence that motivates this

The path-aware investigation refuted BOTH cheap path-selection levers for ~$0 (recall guard,
`RESULTS_PATH_AWARE_RETRIEVAL.md`):

- **Lever A** (anchor-to-anchor topology prune): recall-safe ⟺ ~no pruning (strands 33–46% of
  chains at any pruning level).
- **Lever C** (query-name-embedding candidate prune): pinned to the same recall floor — the
  multi-hop answer isn't named in the question, so its name embeds no closer than the distractors.

Both fail for the same structural reason: **the full ball already contains the answer chain
(bridge-recall 1.0) yet the end-to-end answer-match is only ~0.275** (oracle dial, amb 0.5,
`RESULTS_ER_ANSWER_ABLATION.md` "Follow-up diagnosis"; goldengraph ~0.372) **, and no topology- or
embedding-scored PRUNE of that ball localizes the answer.** The isolated lever is not pruning — it is *knowing the relation
sequence to walk*. That is exactly what `trace_chain` does (a relation-guided, LLM-free walk that
returns the answer node directly), and it answers ~1.0 given the chain. The gap: `trace_chain`
only fires when a `chain` plan carries an ordered `relation_chain`, which today ONLY the
engineered-template regex (`_CHAIN_RE`) produces. For a natural-language question there is no
decomposition into an ordered relation chain, so `trace_chain` never runs and the query falls to
ball+synthesis — the ~0.275 regime.

**This spec builds the missing piece: a natural-language question → ordered, KG-vocabulary
`relation_chain` decomposer**, so the EXISTING chain-plan → `trace_chain` → fallback machinery
runs on real questions.

## 2. Prior art in this repo (read before building)

1. **`trace_chain` (`answer.py`)** — the relation-guided walk. Seeds the anchor by name, follows
   each named relation hop-by-hop via **lenient `_rel_match`** (normalize + substring-either-way),
   is **direction-tolerant** (accepts a reversed edge when forward yields nothing), and **bridges
   the store's under-merge** (`_bridge_surfaces`). Returns the final node's canonical name or
   `None`. Already handles the ~0.85-accurate extracted predicates.
2. **The recall-safe fallback is ALREADY built, but with a clamp bug** (`ask(mode="auto")`,
   answer.py:383–391): a `chain` plan calls `trace_chain`; if it returns `None` the code falls
   through. **BUG (reviewer finding 1):** the clamp at answer.py:391 is
   `mode = plan.mode if plan.mode in ("local","hybrid","global") else "local"` — `"chain"` is not
   in that set, so a failed chain drops to **`local`**, whereas the pre-decomposer route for an NL
   multi-hop question is the MULTI_HOP else-branch `RetrievalPlan(mode="hybrid")` (route.py:166).
   So without a fix, introducing the decomposer converts a walk-fail from `hybrid` to `local`
   synthesis — a real behavior change, NOT a no-op. **This spec fixes the clamp so a failed `chain`
   falls back to `hybrid`** (the exact route the same profile takes today without a chain). WITH
   that fix, chain decomposition is a genuine **strict precision-add**: walk-succeeds adds the
   answer, walk-fails reproduces today's `auto` route. This is the property the prunes lacked (they
   could strand). The fix is a one-line clamp change, test-covered in the goldengraph suite.
3. **`resolve_profile` two-tier planner (`route.py`)** — heuristic `classify_query` first;
   escalates to an injected `LLMQueryClassifier` ONLY when the heuristic is below `MIN_CONF=0.8`,
   and the tier-2 result wins only if strictly more confident. `LLMQueryClassifier` is
   budget-capped (`max_calls`), fail-open (any error/bad-JSON/**out-of-vocab relation** →
   abstain `confidence=0.0`), and passed the slice's predicate vocabulary (`_slice_predicates`).
   **It emits a single `relation`, never an ordered `relation_chain`** (verified: its `_PROMPT`
   asks only `{intent, anchor, relation, as_of}`) — so NL multi-hop routes to `hybrid`, and
   `plan_query`'s `chain` branch (needs `relation_chain`) never fires. THIS is the one gap.
4. **`router_paraphrases.py`** — hand-authored NL questions the heuristic regex misses, each with
   gold slots; the established offline (stub-LLM) fixture for "heuristic-miss → LLM recovers".
   Today single-relation only.
5. **MuSiQue (`corpora.py`)** — `load_musique`/`fetch_musique`: real multi-hop NL questions
   (`"Who founded the company that makes Widgets?"`) with `gold_answer`, `gold_supporting_fact_ids`,
   `hop_count`, and (in the raw rows) `question_decomposition` (ordered NL sub-questions).
   **`QAItem.relation_chain` is EMPTY for MuSiQue** — MuSiQue has no KG-relation gold chain, so its
   grading signal is `gold_answer` (+ supporting-fact recall), NOT a bridge-recall over a gold
   relation_chain (contrast the engineered corpus).
6. **The 2026-06-22 negative result** (in the `_retrieve_local` docstring): a verbatim
   predicate-word PRUNE was measured worse. Note this decomposer feeds a WALK, not a prune, and its
   relations are **vocabulary-constrained** (the LLM must choose real predicate ids, out-of-vocab →
   abstain) + matched **leniently** — it is not the 2026-06-22 verbatim-prune trap, but the same
   alignment risk (decomposed words vs extracted predicates) is the crux this spec measures.

## 3. Design — decisions

### D1. Decomposer = extend `LLMQueryClassifier` to emit an ordered `relation_chain`
Add a `relation_chain` field to the tier-2 classifier's prompt + defensive parse: for a multi_hop
intent, ask for an ORDERED list of relations plus the anchor, and **pin the emitted intent to
`multi_hop`** so `plan_query` reaches the `chain` branch (a `relation_chain` on any other intent is
ignored → `local`; reviewer finding 7). The prompt SHOWS the slice's predicate vocabulary to bias
the model toward real relations, but — unlike the single-`relation` path — the chain relations are
**NOT strict-vocab-gated** (see D2). Reuse the rest of the seam wholesale: budget cap, fail-open
abstain, two-tier escalation. `resolve_profile`/`plan_query` are unchanged: a populated
`relation_chain` at `confidence≥MIN_CONF` routes to `mode="chain"` → `trace_chain`. The
single-`relation` path KEEPS its existing strict out-of-vocab abstain (don't change shipped
behavior). Rejected: a separate `LLMChainDecomposer` class — it would duplicate the
budget/abstain/escalation seam for one extra output field.

### D2. Relation alignment = measure-first; the WALK's `_rel_match` is the filter, not a strict gate
Reviewer findings 4+5 corrected the original "constrain + lenient stack" framing: the existing
single-relation guard is **strict equality** (`relation not in predicates → abstain`,
route.py:229–230), which would force the model to reproduce one of possibly hundreds of noisy
extracted predicate ids **verbatim** and would make `_rel_match`'s lenience moot (a verbatim id
trivially equality-matches, and a near-miss like `founded_by` vs vocab `founded_by_person` would be
abstained even though the lenient walk accepts it). So for the CHAIN we do NOT strict-gate: the
decomposed relations are prompt-guided but pass through as-authored, and **`trace_chain`'s lenient
`_rel_match` (normalize + substring-either-way) is the actual filter at walk time.** A relation that
matches no edge just makes the walk return `None` → fall back to `hybrid` (D-safety) — the
None→fallback IS the safety net that lets us drop the strict gate for chains without risking a
doomed route. **Measure how often a prompt-guided chain actually walks to the answer before building
anything heavier.** Rejected for now: per-hop embedding relation-matching — more robust to vocab
gaps but a new drift surface + build cost; defer until the measurement shows the prompt-guided +
`_rel_match` path falls short. (The A/C measure-first discipline that saved $ twice.) Open: cap or
neighborhood-scope the vocab list shown in the prompt if a real MuSiQue slice has too many
predicates to inline (§6).

### D3. Measurement gate = MuSiQue end-to-end, recall-guard-first
- **Offline mechanism ($0, no network) — proves ROUTING PLUMBING, not real-LLM quality.** Extend
  `router_paraphrases.py` (`Paraphrase` gains a `relation_chain`) with multi-hop NL paraphrases; a
  stub classifier returns the gold chain; assert `resolve_profile` → `plan_query` yields
  `mode="chain"` with the expected chain, and the heuristic alone does NOT. **This is an ORACLE stub
  (gold-slot lookup), so it proves only that `resolve_profile→plan_query→mode="chain"` wires up — it
  does NOT prove a real LLM decomposes correctly** (that is MuSiQue's job). Reviewer finding 6: this
  also requires extending `StubClassifier`, `_profile_matches`, and `stub_escalation_accuracy`
  (currently `aggregate`/`as_of` only) with a `chain` case in `router_eval.py`.
- **Recall guard on the real corpus (surface-INDEPENDENT, cheaper than answer-match).** On a MuSiQue
  subset, build the KG (extract→resolve→store), decompose (LLM), run `trace_chain` with
  `refs_out=`. **Primary signal = supporting-fact recall: did the walk's traversed-edge provenance
  (`refs_out`) intersect the question's `gold_supporting_fact_ids`?** (reviewer finding 2 — this
  does NOT depend on the walk's canonical surface matching MuSiQue's free-text gold, so it cleanly
  measures "did the walk go through the right evidence"). Secondary (surface-DEPENDENT, reported but
  not gated) = `metrics.answer_match(walk_result, gold_answer)`; a gap between the two localizes ER
  canonical-surface-vs-free-text mismatch. Sub-diagnostics: anchor-seed hit-rate (§6.3 — a walk
  can't start if the anchor doesn't `seeds_by_name`-match) and chain-decode rate. Spends ONE
  decompose LLM call/question and NO synthesis. **Run the SAME guard on the engineered corpus as a
  control** (gold KG, node names == gold by construction → isolates decoder+walk from pipeline noise
  AND from the surface confound).
- **Paid answer-match (the gate).** Full `ask(mode="auto", query_classifier=LLMQueryClassifier(llm))`
  with the decomposer (walk-then-`hybrid`-fallback) vs an `ask(mode="local")` baseline, answer-match
  on the same MuSiQue subset, hard `--max-cost-usd`. Win: auto+decomposer answer-match > local
  baseline. Cite the baseline explicitly from `RESULTS_ER_ANSWER_ABLATION.md` (oracle answer-match
  ~0.275 / goldengraph ~0.372 at amb 0.5), NOT the bruise-recall cells.
- MuSiQue is the honest real-NL gate (the engineered template is already handled by the regex, so it
  can't measure an NL decomposer). Engineered stays a byte-identical-when-off regression anchor.

### D4. Gating + safety
- The decomposer is active only when the bench constructs an `LLMQueryClassifier(llm)` and passes it
  as `ask(mode="auto", query_classifier=…)` — `ask` never builds one itself (reviewer finding 3), so
  the harness wiring (construct + thread) is IN SCOPE, not just the `route.py` prompt/parse. The env
  gate `GOLDENGRAPH_CHAIN_DECOMPOSE` (default off, mirroring sibling gates) is read **inside the
  classifier** to toggle chain emission: off → today's single-`relation` behavior byte-identical;
  on → also emit `relation_chain`. Raise the classifier's `max_calls` to ≥ the MuSiQue subset size
  (default 5 would silently fail-open-abstain past 5 questions — reviewer finding 8).
- **No regression by construction (WITH the finding-1 clamp fix):** a failed/absent chain routes to
  exactly the mode the same profile takes today (`hybrid` for multi_hop). The decomposer only ever
  ADDS a `relation_chain`; the None→`hybrid` fallback reproduces today's `auto` route.

## 4. Validation

- **Ceiling:** `trace_chain` given a correct chain answers ~1.0 (the engineered result); the
  decomposer's job is to produce correct chains for NL questions.
- **Metrics:** (a) offline: `mode="chain"` recovery rate on the multi-hop paraphrases (stub LLM);
  (b) MuSiQue recall guard: walk-result == `gold_answer` rate (LLM-decompose, no synthesis);
  (c) MuSiQue answer-match: auto+decomposer vs local, Δ ≥ 0 required, Δ > 0 the win.
- **Recall guard precedes paid answer-match** (D3) — a decomposer that rarely walks to the answer is
  refuted at decompose-only cost, before synthesis spend.
- **A/B discipline:** env-gated, default OFF, byte-identical off; measured delta on the same
  seed/subset before any default flip.

## 5. Scope + caveats
- **Product-code change:** (a) `route.py` — `LLMQueryClassifier` prompt/parse emits an ordered,
  intent-pinned `relation_chain`, gated by `GOLDENGRAPH_CHAIN_DECOMPOSE`; (b) `answer.py` — the
  finding-1 clamp fix (failed `chain` → `hybrid`, not `local`), a one-line change + goldengraph test.
  Both have test suites; new behavior is gated + parity-tested.
- **Bench-code change (in scope, reviewer finding 3):** the MuSiQue harness constructs
  `LLMQueryClassifier(llm, max_calls≥N)` and threads it as `ask(query_classifier=…)`; adds the
  supporting-fact-recall guard (`refs_out ∩ gold_supporting_fact_ids`); extends the offline router
  harness (`Paraphrase.relation_chain`, `StubClassifier`, `_profile_matches`,
  `stub_escalation_accuracy` `chain` case).
- **Real confounds:** MuSiQue end-to-end runs the full extract→resolve→store→answer pipeline, so a
  low walk rate could be extraction/resolution quality, not the decomposer. The recall guard on the
  ENGINEERED corpus (where the KG is gold) isolates the decomposer from pipeline noise — run it as a
  control alongside MuSiQue.
- **Out of scope:** per-hop embedding relation-matching (D2 defers it); synthesizer prompt work
  (proven not the bottleneck); the prunes (refuted); building an NL chain decomposer WITHOUT the LLM
  (the heuristic regex is exactly what's insufficient).
- **Cost:** decompose is one LLM call/question (budget-capped in the classifier); recall guard adds
  no synthesis; answer-match is the only synthesis spend, gated behind a passing recall guard and a
  hard `--max-cost-usd`.

## 6. Open questions (resolve before/at plan)
1. Chain length cap in the decomposer prompt (MuSiQue is 2–4 hop)? A cap bounds a runaway
   hallucinated chain; propose a small cap (e.g. 4) with abstain past it.
2. Does the MuSiQue KG build need the retrieval-bridge (`GOLDENGRAPH_RETRIEVAL_BRIDGE`) on for the
   walk to cross under-merged siblings, and should the guard run with it on (it's the realistic
   `ask` config)?
3. Anchor resolution on MuSiQue: the decomposer's anchor surface must `seeds_by_name`-match a real
   entity; measure anchor-seed hit-rate as a sub-diagnostic (a walk can't start without it).
4. Vocabulary in the prompt (reviewer findings 5/7): a real MuSiQue slice may have hundreds of
   noisy extracted predicates. Inline all (current single-relation behavior), cap to top-N by
   frequency, or scope to the anchor's neighborhood predicates? And note the heuristic pre-emption
   path — a MuSiQue question tripping a high-conf aggregate/temporal lead-in regex (`conf=0.9`)
   never escalates to the decomposer (route.py:180); measure how often that fires on real NL.
