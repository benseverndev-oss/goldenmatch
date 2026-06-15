#!/usr/bin/env python3
"""Algorithm Forge — survey, structure, invent, and sanity-check string-similarity algorithms.

A closed-loop research harness for entity resolution's oldest workhorse: the
string-similarity / distance function. It drives Claude through four stages and
loops until a budget or a target verdict is reached.

    1. SURVEY      Enumerate the lineage of similarity algorithms (Hamming,
                   Levenshtein, Damerau, Needleman-Wunsch, Smith-Waterman, Jaro,
                   Jaro-Winkler, Soundex, Metaphone, NYSIIS, Jaccard, Dice,
                   q-gram, TF-IDF/cosine, Monge-Elkan, ...), structured per
                   algorithm. Sourced from model knowledge, anchored to a curated
                   seed list for factual accuracy (no network dependency).
    2. STRUCTURIZE Build a taxonomy (the axes algorithms vary on) and extract
                   explicit GAPS / logical branch-off points.
    3. PROPOSE     Invent a new candidate algorithm that targets one gap, with
                   mechanism + pseudocode. Each loop it sees prior proposals and
                   their verdicts, so it learns from rejections.
    4. VALIDATE    A FRESH, adversarial context (it never sees the proposer's
                   reasoning) returns failed | maybe | yes, each with a reason.

The loop repeats until the USD budget is exhausted, the iteration cap is hit, or
the target number of acceptable verdicts is reached.

Usage
-----
    export ANTHROPIC_API_KEY=sk-ant-...
    python algorithm_forge.py --budget-usd 5 --max-iterations 6 \
        --target-verdict yes --target-count 1 --out forge_run

    # Smoke-test the whole pipeline + report with no API calls / no key:
    python algorithm_forge.py --mock --max-iterations 3 --out /tmp/forge_demo

Outputs `<out>.json` (full structured run log) and `<out>.md` (readable report).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import textwrap
import time
from typing import Any, Optional

MODEL = "claude-opus-4-8"

# Opus 4.8 list pricing, USD per 1M tokens. Cache writes ~1.25x input, reads ~0.1x.
PRICING = {
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

# Curated anchor list. We don't trust the model to remember every date; we hand it
# a spine of known algorithms so the SURVEY stage is grounded rather than invented.
SEED_ALGORITHMS = [
    "Hamming distance (1950)",
    "Levenshtein / edit distance (1965)",
    "Damerau-Levenshtein (1964, transpositions)",
    "Needleman-Wunsch global alignment (1970)",
    "Smith-Waterman local alignment (1981)",
    "Jaro distance (1989)",
    "Jaro-Winkler (1990, prefix-weighted)",
    "Soundex (Russell & Odell, patented 1918/1922)",
    "Metaphone (Philips, 1990) and Double Metaphone (2000)",
    "NYSIIS (1970)",
    "Jaccard index (1901) over token / q-gram sets",
    "Sorensen-Dice coefficient (1948)",
    "q-gram / n-gram overlap and the q-gram distance (Ukkonen, 1992)",
    "Longest common subsequence (LCS) similarity",
    "TF-IDF cosine similarity (Salton, 1970s)",
    "Monge-Elkan hybrid token similarity (1996)",
    "Smith-Waterman-Gotoh affine gaps (1982)",
    "Tversky index (1977, asymmetric set similarity)",
    "Ratcliff/Obershelp gestalt pattern matching (1988)",
    "Levenshtein automata (Schulz & Mihov, 2002)",
    "Cosine over character/word embeddings (2010s, neural)",
]

# --------------------------------------------------------------------------- #
# JSON schemas for structured outputs (output_config.format). Kept within the
# supported subset: object/array/string/integer/number/boolean, enum, required,
# additionalProperties:false. No numeric/length constraints, no recursion.
# --------------------------------------------------------------------------- #


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


_STR = {"type": "string"}
_STRS = _arr(_STR)

ALGORITHM_SCHEMA = _obj(
    {
        "name": _STR,
        "year": {"type": "integer", "description": "Year introduced; 0 if unknown."},
        "inventor": _STR,
        "family": {
            "type": "string",
            "enum": [
                "edit-distance",
                "alignment",
                "phonetic",
                "token-set",
                "q-gram",
                "vector-space",
                "hybrid",
                "embedding",
                "probabilistic",
                "other",
            ],
        },
        "core_idea": _STR,
        "strengths": _STRS,
        "weaknesses": _STRS,
        "complexity": _STR,
        "metric_properties": _STRS,
    },
    ["name", "year", "inventor", "family", "core_idea", "strengths", "weaknesses", "complexity", "metric_properties"],
)

SURVEY_SCHEMA = _obj(
    {"algorithms": _arr(ALGORITHM_SCHEMA), "notes": _STR},
    ["algorithms", "notes"],
)

GAP_SCHEMA = _obj(
    {
        "id": _STR,
        "title": _STR,
        "description": _STR,
        "branch_from": _STRS,
        "why_unaddressed": _STR,
        "opportunity": _STR,
    },
    ["id", "title", "description", "branch_from", "why_unaddressed", "opportunity"],
)

TAXONOMY_SCHEMA = _obj(
    {
        "dimensions": _STRS,
        "clusters": _arr(_obj({"name": _STR, "members": _STRS, "shared_trait": _STR}, ["name", "members", "shared_trait"])),
        "gaps": _arr(GAP_SCHEMA),
        "summary": _STR,
    },
    ["dimensions", "clusters", "gaps", "summary"],
)

PROPOSAL_SCHEMA = _obj(
    {
        "name": _STR,
        "targets_gap": _STR,
        "one_liner": _STR,
        "mechanism": _STR,
        "builds_on": _STRS,
        "pseudocode": _STR,
        "complexity": _STR,
        "metric_properties": _STRS,
        "expected_advantages": _STRS,
        "known_risks": _STRS,
        "novelty_argument": _STR,
    },
    ["name", "targets_gap", "one_liner", "mechanism", "builds_on", "pseudocode",
     "complexity", "metric_properties", "expected_advantages", "known_risks", "novelty_argument"],
)

VERDICT_SCHEMA = _obj(
    {
        "verdict": {"type": "string", "enum": ["failed", "maybe", "yes"]},
        "reason": _STR,
        "fatal_flaws": _STRS,
        "fixable_concerns": _STRS,
        "novelty_assessment": _STR,
        "suggested_improvements": _STRS,
    },
    ["verdict", "reason", "fatal_flaws", "fixable_concerns", "novelty_assessment", "suggested_improvements"],
)

VERDICT_RANK = {"failed": 0, "maybe": 1, "yes": 2}


# --------------------------------------------------------------------------- #
# Cost-tracking client wrapper
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class Cost:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    def add(self, usage: Any, model: str) -> None:
        p = PRICING.get(model, PRICING[MODEL])
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_write_tokens += cw
        self.cache_read_tokens += cr
        self.calls += 1
        self.usd += (
            inp * p["input"]
            + out * p["output"]
            + cw * p["input"] * 1.25
            + cr * p["input"] * 0.10
        ) / 1_000_000


class ForgeClient:
    """Thin wrapper over the Anthropic SDK that does structured calls + cost accounting.

    In --mock mode no SDK or key is required; deterministic stand-in objects are
    returned so the pipeline and report generation are fully exercisable offline.
    """

    def __init__(self, model: str, mock: bool = False, verbose: bool = True):
        self.model = model
        self.mock = mock
        self.verbose = verbose
        self.cost = Cost()
        self._client = None
        if not mock:
            try:
                import anthropic  # noqa: F401
            except ImportError:
                sys.exit(
                    "anthropic SDK not installed. `pip install -U anthropic`, or run with --mock."
                )
            import anthropic

            self._client = anthropic.Anthropic()

    def structured(
        self,
        *,
        system: list[dict] | str,
        user: str,
        schema: dict,
        think: bool = True,
        max_tokens: int = 16000,
        label: str = "",
    ) -> dict:
        """One structured-output call. Returns the parsed JSON object as a dict."""
        if self.mock:
            return _mock_response(schema, label)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if think:
            # Adaptive thinking is the recommended setting on Opus 4.8; it decides
            # depth per call and interleaves reasoning. Structured outputs are
            # compatible with extended thinking.
            kwargs["thinking"] = {"type": "adaptive"}

        resp = self._client.messages.create(**kwargs)
        self.cost.add(resp.usage, self.model)

        if resp.stop_reason == "refusal":
            raise RuntimeError(f"[{label}] request refused: {getattr(resp, 'stop_details', None)}")

        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError(f"[{label}] no text block in response (stop_reason={resp.stop_reason})")
        return json.loads(text)


# --------------------------------------------------------------------------- #
# System prompts
# --------------------------------------------------------------------------- #

SURVEY_SYSTEM = textwrap.dedent(
    """\
    You are a historian of string-similarity and string-distance algorithms, with a
    bias toward the ones that matter for entity resolution / record linkage (the task
    of deciding whether two messy strings refer to the same real-world entity).

    Survey the field across its full history. For each algorithm be precise and
    honest: real inventor, approximate year, the actual mechanism, where it shines,
    and where it breaks. Cover edit-distance, sequence-alignment, phonetic,
    token/set-based, q-gram, vector-space, hybrid, and embedding families. Do not
    invent algorithms or dates; if unsure of a year, use 0. Prefer breadth of
    genuinely distinct ideas over near-duplicates.
    """
)

TAXONOMY_SYSTEM = textwrap.dedent(
    """\
    You are a research strategist mapping the design space of string-similarity
    algorithms so a new, more effective one can be invented for entity resolution.

    From the survey, do two things. First, name the orthogonal DIMENSIONS along which
    these algorithms genuinely differ (e.g. unit of comparison, treatment of
    position/order, symmetry, normalization, learnability, error model, cost). Cluster
    the algorithms along them. Second — and most important — identify GAPS and logical
    branch-off points: combinations of dimensions that no existing algorithm occupies,
    failure modes everyone shares, or a good idea from one family never ported to
    another. Be concrete and adversarial about WHY each gap is still open. These gaps
    are the launch pads for invention, so make them sharp and non-obvious.
    """
)

PROPOSE_SYSTEM = textwrap.dedent(
    """\
    You are an inventor of string-similarity algorithms for entity resolution. You are
    given a survey of prior art, a taxonomy of the design space with identified gaps,
    and — on later rounds — your own earlier proposals with a critic's verdicts.

    Propose ONE new algorithm that targets a specific identified gap and is genuinely
    more effective than prior art on some real, stated axis. It must be a concrete,
    implementable mechanism — not a vague ensemble or "use a transformer." Give a
    precise step-by-step mechanism and runnable pseudocode. State complexity, metric
    properties, where you expect it to win, and the risks you already see.

    If earlier proposals were rejected, do NOT repeat them — diagnose why they failed
    from the verdict and either fix the flaw or attack a different gap. Favor ideas
    that are testable and falsifiable over grand but unimplementable ones.
    """
)

VALIDATE_SYSTEM = textwrap.dedent(
    """\
    You are a ruthless but fair reviewer doing a sanity check on a PROPOSED new
    string-similarity algorithm for entity resolution. You have the prior-art survey
    and the proposal. You do NOT have, and do not trust, the inventor's own hype.

    Judge it on: (1) is the mechanism actually well-defined and implementable as
    described; (2) is it genuinely novel versus the survey, or a renamed existing
    method; (3) is the claimed advantage plausible and testable, or hand-waving; (4)
    are there fatal flaws (ill-defined, degenerate output, intractable, no advantage).

    Return exactly one verdict:
      - "failed": a fatal flaw, not novel, or no credible advantage. Give the reason.
      - "maybe":  promising but with real concerns that must be resolved first. Give
                  the reason and what would have to be true.
      - "yes":    well-defined, novel enough, with a plausible and testable advantage.
                  Give the reason. Reserve this for proposals you'd actually prototype.
    Be specific. A one-line "looks good" is a failure of your job.
    """
)


def _cached_system(text: str) -> list[dict]:
    """System prompt as a single cache-controlled block (reused across the loop)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# --------------------------------------------------------------------------- #
# Pipeline stages
# --------------------------------------------------------------------------- #


def run_survey(client: ForgeClient) -> dict:
    user = (
        "Survey the major string-similarity and string-distance algorithms. Anchor your "
        "survey to (and expand beyond) these known algorithms:\n- "
        + "\n- ".join(SEED_ALGORITHMS)
        + "\n\nReturn 18-30 genuinely distinct algorithms."
    )
    return client.structured(
        system=SURVEY_SYSTEM, user=user, schema=SURVEY_SCHEMA,
        think=False, label="survey",
    )


def run_taxonomy(client: ForgeClient, survey: dict) -> dict:
    user = (
        "Here is the survey of prior art as JSON:\n\n"
        + json.dumps(survey, indent=2)
        + "\n\nProduce the taxonomy: dimensions, clusters, and 6-10 sharp GAPS / "
        "branch-off points that a new algorithm could exploit."
    )
    return client.structured(
        system=TAXONOMY_SYSTEM, user=user, schema=TAXONOMY_SCHEMA,
        think=True, label="taxonomy",
    )


def run_proposal(client: ForgeClient, survey: dict, taxonomy: dict, history: list[dict]) -> dict:
    # Stable, cache-controlled prefix (survey + taxonomy) reused every iteration.
    context = (
        PROPOSE_SYSTEM
        + "\n\n### PRIOR-ART SURVEY (JSON)\n"
        + json.dumps(survey)
        + "\n\n### DESIGN-SPACE TAXONOMY & GAPS (JSON)\n"
        + json.dumps(taxonomy)
    )
    system = _cached_system(context)

    if history:
        prior = "\n\n".join(
            f"--- Attempt {h['iteration']}: {h['proposal']['name']} ---\n"
            f"Targeted gap: {h['proposal']['targets_gap']}\n"
            f"One-liner: {h['proposal']['one_liner']}\n"
            f"VERDICT: {h['verdict']['verdict'].upper()} — {h['verdict']['reason']}\n"
            f"Fatal flaws: {', '.join(h['verdict']['fatal_flaws']) or 'none'}\n"
            f"Fixable concerns: {', '.join(h['verdict']['fixable_concerns']) or 'none'}"
            for h in history
        )
        user = (
            "Your earlier attempts and the critic's verdicts:\n\n" + prior
            + "\n\nPropose your NEXT algorithm. Either fix the strongest 'maybe' or "
            "attack a fresh gap — do not resubmit a rejected idea."
        )
    else:
        user = "Propose your first new algorithm targeting one of the identified gaps."

    return client.structured(
        system=system, user=user, schema=PROPOSAL_SCHEMA,
        think=True, label="proposal",
    )


def run_validation(client: ForgeClient, survey: dict, proposal: dict) -> dict:
    # Independent / adversarial context: only the survey + the proposal, never the
    # proposer's private reasoning or its self-assessment of novelty.
    user = (
        "### PRIOR-ART SURVEY (JSON)\n"
        + json.dumps(survey)
        + "\n\n### PROPOSED NEW ALGORITHM (JSON)\n"
        + json.dumps(proposal)
        + "\n\nSanity-check it. Return your verdict."
    )
    return client.structured(
        system=VALIDATE_SYSTEM, user=user, schema=VERDICT_SCHEMA,
        think=True, label="validation",
    )


# --------------------------------------------------------------------------- #
# Driver loop
# --------------------------------------------------------------------------- #


def forge(args: argparse.Namespace) -> dict:
    client = ForgeClient(args.model, mock=args.mock, verbose=not args.quiet)

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    t0 = time.time()
    log(f"[forge] model={args.model} mock={args.mock} "
        f"budget=${args.budget_usd} max_iters={args.max_iterations} "
        f"target={args.target_count}x>={args.target_verdict}")

    log("[1/2] surveying prior art ...")
    survey = run_survey(client)
    log(f"      surveyed {len(survey['algorithms'])} algorithms  (${client.cost.usd:.4f})")

    log("[2/2] building taxonomy + gap analysis ...")
    taxonomy = run_taxonomy(client, survey)
    log(f"      found {len(taxonomy['gaps'])} gaps  (${client.cost.usd:.4f})")

    history: list[dict] = []
    accepted = 0
    stop_reason = "max_iterations"

    for i in range(1, args.max_iterations + 1):
        if client.cost.usd >= args.budget_usd:
            stop_reason = "budget_exhausted"
            break

        log(f"[loop {i}/{args.max_iterations}] proposing ...")
        proposal = run_proposal(client, survey, taxonomy, history)
        log(f"      proposal: {proposal['name']} -> gap '{proposal['targets_gap']}'")

        verdict = run_validation(client, survey, proposal)
        v = verdict["verdict"]
        log(f"      VERDICT: {v.upper()} — {verdict['reason'][:120]}"
            f"  (${client.cost.usd:.4f})")

        history.append({"iteration": i, "proposal": proposal, "verdict": verdict})
        if VERDICT_RANK[v] >= VERDICT_RANK[args.target_verdict]:
            accepted += 1
            if accepted >= args.target_count:
                stop_reason = "target_reached"
                break

    elapsed = time.time() - t0
    log(f"[done] stop={stop_reason} iters={len(history)} accepted={accepted} "
        f"cost=${client.cost.usd:.4f} time={elapsed:.1f}s")

    return {
        "config": {
            "model": args.model,
            "mock": args.mock,
            "budget_usd": args.budget_usd,
            "max_iterations": args.max_iterations,
            "target_verdict": args.target_verdict,
            "target_count": args.target_count,
        },
        "stop_reason": stop_reason,
        "accepted": accepted,
        "elapsed_seconds": round(elapsed, 1),
        "cost": dataclasses.asdict(client.cost),
        "survey": survey,
        "taxonomy": taxonomy,
        "iterations": history,
    }


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def render_markdown(run: dict) -> str:
    cfg, cost = run["config"], run["cost"]
    out: list[str] = []
    w = out.append

    w("# Algorithm Forge — run report\n")
    w(f"- **Model:** `{cfg['model']}`{'  _(mock)_' if cfg['mock'] else ''}")
    w(f"- **Stopped because:** `{run['stop_reason']}` after {len(run['iterations'])} iteration(s)")
    w(f"- **Accepted (>= {cfg['target_verdict']}):** {run['accepted']} / target {cfg['target_count']}")
    w(f"- **Cost:** ${cost['usd']:.4f} over {cost['calls']} calls "
      f"({cost['input_tokens']:,} in / {cost['output_tokens']:,} out tokens)")
    w(f"- **Wall time:** {run['elapsed_seconds']}s\n")

    w("## Gaps identified\n")
    for g in run["taxonomy"]["gaps"]:
        w(f"### {g['id']}: {g['title']}")
        w(f"{g['description']}\n")
        w(f"- **Branches from:** {', '.join(g['branch_from'])}")
        w(f"- **Why still open:** {g['why_unaddressed']}")
        w(f"- **Opportunity:** {g['opportunity']}\n")

    w("## Proposals & verdicts\n")
    badge = {"yes": "✅ YES", "maybe": "🟡 MAYBE", "failed": "❌ FAILED"}
    for h in run["iterations"]:
        p, v = h["proposal"], h["verdict"]
        w(f"### Attempt {h['iteration']}: {p['name']}  —  {badge[v['verdict']]}")
        w(f"_{p['one_liner']}_\n")
        w(f"- **Targets gap:** {p['targets_gap']}")
        w(f"- **Builds on:** {', '.join(p['builds_on'])}")
        w(f"- **Complexity:** {p['complexity']}")
        w(f"- **Mechanism:** {p['mechanism']}\n")
        w("```text")
        w(p["pseudocode"].rstrip())
        w("```\n")
        w(f"**Verdict — {v['verdict'].upper()}:** {v['reason']}\n")
        if v["fatal_flaws"]:
            w(f"- **Fatal flaws:** {', '.join(v['fatal_flaws'])}")
        if v["fixable_concerns"]:
            w(f"- **Fixable concerns:** {', '.join(v['fixable_concerns'])}")
        if v["suggested_improvements"]:
            w(f"- **Suggested improvements:** {', '.join(v['suggested_improvements'])}")
        w("")

    w("## Surveyed prior art\n")
    w("| Algorithm | Year | Family | Core idea |")
    w("| --- | --- | --- | --- |")
    for a in run["survey"]["algorithms"]:
        year = a["year"] or "?"
        idea = a["core_idea"].replace("|", "\\|")
        w(f"| {a['name']} | {year} | {a['family']} | {idea} |")
    w("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Mock responses (offline pipeline testing)
# --------------------------------------------------------------------------- #


def _mock_response(schema: dict, label: str) -> dict:
    """Deterministic stand-in objects so --mock exercises the full pipeline."""
    if label == "survey":
        return {
            "algorithms": [
                {"name": "Levenshtein", "year": 1965, "inventor": "V. Levenshtein",
                 "family": "edit-distance", "core_idea": "Min single-char insert/delete/substitute edits.",
                 "strengths": ["intuitive", "exact"], "weaknesses": ["O(nm)", "position-blind cost"],
                 "complexity": "O(nm)", "metric_properties": ["true metric"]},
                {"name": "Jaro-Winkler", "year": 1990, "inventor": "Winkler",
                 "family": "edit-distance", "core_idea": "Matching chars + transpositions, prefix-boosted.",
                 "strengths": ["good on short names"], "weaknesses": ["not a metric", "ad hoc prefix"],
                 "complexity": "O(nm)", "metric_properties": ["bounded [0,1]", "asymmetric prefix"]},
                {"name": "Jaccard (q-gram)", "year": 1901, "inventor": "Jaccard",
                 "family": "token-set", "core_idea": "Set overlap of q-grams.",
                 "strengths": ["order-insensitive"], "weaknesses": ["loses local order"],
                 "complexity": "O(n+m)", "metric_properties": ["bounded [0,1]"]},
            ],
            "notes": "Mock survey for offline testing.",
        }
    if label == "taxonomy":
        return {
            "dimensions": ["unit of comparison", "order sensitivity", "symmetry", "learnability", "error model"],
            "clusters": [
                {"name": "edit-distance", "members": ["Levenshtein", "Jaro-Winkler"],
                 "shared_trait": "operate on character operations"},
                {"name": "set-based", "members": ["Jaccard (q-gram)"], "shared_trait": "bag/set overlap"},
            ],
            "gaps": [
                {"id": "G1", "title": "Position-aware cost learned from data",
                 "description": "Edit costs are uniform; no family learns per-position substitution cost from a corpus cheaply.",
                 "branch_from": ["Levenshtein"], "why_unaddressed": "Learned costs usually need heavy training.",
                 "opportunity": "Light-weight empirical cost table keyed by position bucket."},
                {"id": "G2", "title": "Order-aware set similarity",
                 "description": "Set methods discard local order; edit methods are slow.",
                 "branch_from": ["Jaccard (q-gram)", "Levenshtein"], "why_unaddressed": "Hybrids are ad hoc.",
                 "opportunity": "Weight q-gram overlap by positional displacement."},
            ],
            "summary": "Mock taxonomy for offline testing.",
        }
    if label == "proposal":
        return {
            "name": "PosGram", "targets_gap": "G2",
            "one_liner": "q-gram overlap weighted by how far each shared gram moved.",
            "mechanism": "For each shared q-gram, compute positional displacement between the two strings; "
                         "weight its contribution by exp(-|displacement|/L). Sum and normalize.",
            "builds_on": ["Jaccard (q-gram)", "Jaro-Winkler"],
            "pseudocode": "for g in qgrams(a) & qgrams(b):\n    d = |posA[g] - posB[g]|\n    score += exp(-d/L)\nreturn score / max(|A|,|B|)",
            "complexity": "O(n+m)", "metric_properties": ["bounded [0,1]", "symmetric"],
            "expected_advantages": ["keeps speed of set methods", "recovers some order sensitivity"],
            "known_risks": ["positional index ambiguous for repeated grams"],
            "novelty_argument": "No surveyed method blends q-gram sets with positional decay at O(n+m).",
        }
    if label == "validation":
        # Cycle verdicts so a multi-iteration mock run shows all branches.
        _mock_response.n = getattr(_mock_response, "n", 0) + 1  # type: ignore[attr-defined]
        v = ["maybe", "failed", "yes"][(_mock_response.n - 1) % 3]  # type: ignore[attr-defined]
        return {
            "verdict": v,
            "reason": f"Mock verdict '{v}' for offline testing of the loop and report.",
            "fatal_flaws": ["repeated-gram position is ambiguous"] if v == "failed" else [],
            "fixable_concerns": ["define position for repeated grams"] if v == "maybe" else [],
            "novelty_assessment": "Plausibly distinct blend of set + positional decay.",
            "suggested_improvements": ["benchmark against Jaro-Winkler on a name corpus"],
        }
    raise ValueError(f"no mock for label {label!r}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Survey, structure, invent, and sanity-check string-similarity algorithms.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--model", default=MODEL, help="Anthropic model id.")
    ap.add_argument("--budget-usd", type=float, default=5.0, help="Stop once estimated spend exceeds this.")
    ap.add_argument("--max-iterations", type=int, default=6, help="Max propose/validate loops.")
    ap.add_argument("--target-verdict", choices=["maybe", "yes"], default="yes",
                    help="Verdict level that counts as acceptable.")
    ap.add_argument("--target-count", type=int, default=1,
                    help="Stop after this many proposals reach >= target-verdict.")
    ap.add_argument("--out", default="forge_run", help="Output path stem (writes .json and .md).")
    ap.add_argument("--mock", action="store_true", help="Run the pipeline offline with no API calls.")
    ap.add_argument("--quiet", action="store_true", help="Suppress progress logging.")
    args = ap.parse_args(argv)

    run = forge(args)

    json_path = f"{args.out}.json"
    md_path = f"{args.out}.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(run, f, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(run))

    if not args.quiet:
        print(f"\nWrote {json_path} and {md_path}")
        accepted = [h for h in run["iterations"]
                    if VERDICT_RANK[h["verdict"]["verdict"]] >= VERDICT_RANK[args.target_verdict]]
        if accepted:
            best = accepted[-1]["proposal"]
            print(f"Best accepted proposal: {best['name']} — {best['one_liner']}")
        else:
            print("No proposal reached the target verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
