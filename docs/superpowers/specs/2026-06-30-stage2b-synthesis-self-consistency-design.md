# Stage-2-B: Synthesis Self-Consistency — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-06-30
**Context:** goldengraph real-corpus (stage-2) quality. Follows stage-2-A's verdict
(`docs/superpowers/reports/2026-06-29-stage2a-musique-lever-ranking.md`): on MuSiQue N=50, **SYNTHESIS
is the dominant fixable lever** — 18 questions had the gold answer IN the retrieved ball
(`in_ball=True`) but the 7B wrote the wrong answer. `support_recall` 0.58: the evidence is retrieved;
the model mis-reads it.

## Goal

Reduce the 7B's synthesis reasoning-variance by **sampling the answer N times and majority-voting**,
recovering the answer that is already in the retrieved ball. **Opt-in, measure-first**; default off =
today's single-call behavior, byte-for-byte.

## Why self-consistency

The current `synthesize_local` is already a heavily-engineered SINGLE 7B call (multi-hop decomposition,
direction-awareness, commit-to-single-entity, `Answer:` parsing). The 18 failures are genuine
reasoning/selection errors on a weak model — the right entity is in the ball, the model reasons to the
wrong one. The prompt is already elaborate and format is already fixed (stage-2-A). Self-consistency
(sample + majority vote) is the classic variance-reduction fix for weak-model reasoning, and it fits
the measured situation: the answer is retrievable, so multiple reasoning attempts plus a vote should
surface it.

## Architecture

Three small, independently-testable pieces. All in the goldengraph engine package (a real engine
feature; the bench exercises it via env).

### 1. LLM sampling (`goldengraph/llm.py`)

Add `complete_many(prompt, *, n, temperature) -> list[str]` to `OpenAIClient`: a **loop of N calls** at
the given temperature (NOT the OpenAI `n=` param — Ollama's OpenAI-compatible endpoint does not reliably
support `n`), each token-tracked through the existing budget path. `complete()` stays `temperature=0`,
untouched. **Implementation note for the plan:** `_chat` currently hardcodes `temperature: 0` and takes
no temperature argument; the plan must thread a `temperature` param through `_chat` (default 0, so
`complete()` is unchanged) rather than reuse `complete()` verbatim. The `LLMClient` Protocol gains
`complete_many` as an OPTIONAL capability — mirroring the existing `complete_json` feature-detect
pattern; `synthesize_local` falls back to single-call (via `hasattr`) when a stub lacks it.

### 2. Self-consistency in `synthesize_local` (`goldengraph/synthesize.py`)

Gated by two env vars (parsed defensively, mirroring the repo's `_literals_enabled` house style — a
non-int / `<= 1` / negative `SAMPLES` resolves to single-call, never crashes synthesis):
- `GOLDENGRAPH_SYNTH_SAMPLES` (default `1` = current single call; opt-in).
- `GOLDENGRAPH_SYNTH_TEMPERATURE` (default `0.7`; non-float falls back to the default).

When `samples > 1`: draw N completions via `complete_many`, parse each with the existing
`_extract_answer`, then **vote**:
- Group answers by a small goldengraph-LOCAL normalizer (lowercase + collapse whitespace + strip
  surrounding punctuation). goldengraph cannot import the bench's `metrics._normalize`, so the
  vote-grouping normalizer is local and minimal — its only job is to make `Firefox` / `firefox.` vote
  together.
- **Return the most common RAW form** (not the normalized key), so the answer keeps its real casing.
- **Deterministic tie-break**: highest count, then first-seen order.

The prompt text, `_extract_answer`, and the `samples=1` path are unchanged.

### 3. Validation run

MuSiQue N=50 with self-consistency on (`GOLDENGRAPH_SYNTH_SAMPLES=5`), compared to the stage-2-A
baseline (`answer_match` 0.12, SYNTHESIS bucket 18). The fair metric (now on main) scores it.
**Lever/metric alignment:** the bench runs goldengraph in `mode=auto`, which routes MuSiQue's
natural-language questions to the LOCAL synthesis path (`synthesize_local`) — the exact path this lever
modifies and the one the stage-2-A SYNTHESIS bucket was measured on. (`synthesize_hybrid`/`global` share
the single-call shape but are explicitly OUT of scope here.)

## Data flow

```
ask -> synthesize_local
  -> samples>1 ? complete_many(prompt, n=N, temperature=T)  : complete(prompt)   [default]
  -> _extract_answer on each sample
  -> normalized-group vote -> plurality RAW answer
```

## Error handling

- A failed/empty sample is skipped; if ALL samples fail, fall back to a single `complete`.
- `samples=1` is byte-identical to today: one `complete` call, no temperature change, no vote path.
- A stub LLM without `complete_many` -> single-call fallback (no crash).

## Testing

- **Vote logic** (pure): `["Firefox","firefox.","Chrome"]` -> `Firefox` (normalized grouping collapses
  the first two; raw form returned); tie -> first-seen (`["A","B"]` -> `A`); single element -> itself;
  empty input -> empty.
- **Self-consistency via stub LLM**: a stub `complete_many` returning
  `["Answer: Firefox","Answer: Firefox","Answer: Chrome"]` -> `synthesize_local` returns `Firefox`; a
  stub returning one empty/error entry proves skip-and-continue.
- **Fallback parity**: `samples=1` (default) makes exactly one `complete` call (not `complete_many`); a
  stub lacking `complete_many` still works -> locks the "byte-identical when off" guarantee.
- **`complete_many` shape**: on a fake OpenAI client, asserts N calls at the requested temperature and
  N returned strings (no live network).
- **Integration validation** = the N=50 MuSiQue run.

## Scope / YAGNI

- **No constrained-ball selection** (vote over parsed answers only) — add only if validation shows
  off-ball hallucination dominates.
- **No `n=` API optimization** — robust N-loop; revisit only if cost matters.
- **No change to retrieval / extraction / the prompt text** — purely the sampling + vote wrapper.
- **Default off** — the single-call baseline stays the shipped default; self-consistency is the opt-in
  measured lane.

## Validation gate + honest-null readiness

**Known risk:** if the 7B fails SYSTEMATICALLY (reasons to the *same* wrong answer every time), voting
cannot help — all samples agree on wrong. The design is therefore explicitly falsifiable. The N=50
validation decides:

- **`answer_match` rises meaningfully AND the SYNTHESIS bucket drops** -> success. Keep it default-off
  (the measured opt-in lever), record the before/after in a report. Optionally a small sample-count
  sweep (3 / 5 / 8) if the first N shows life.
- **Flat** -> **honest-null it** (like the argctx and literal-attrs closures): record that 7B synthesis
  errors are systematic, not variance; keep the code behind the default-off flag; the next lever
  becomes constrained-selection or retrieval. **No tuning to force a number.**

## Files

- Modify: `packages/python/goldengraph/goldengraph/llm.py` (`complete_many` on `OpenAIClient`; Protocol
  optional method).
- Modify: `packages/python/goldengraph/goldengraph/synthesize.py` (env-gated self-consistency +
  vote helper in `synthesize_local`).
- Create: `packages/python/goldengraph/tests/test_synthesis_self_consistency.py` (vote + stub-LLM tests).
- Validation: existing `scripts/distill/modal_bench.py --corpus musique` (env opts; no bench code change).
- Report (on success or null): `docs/superpowers/reports/2026-06-30-stage2b-synthesis-self-consistency.md`.
