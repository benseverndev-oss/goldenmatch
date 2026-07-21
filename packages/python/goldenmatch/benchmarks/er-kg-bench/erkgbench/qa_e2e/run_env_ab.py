"""CLI: same-graph env-A/B for the goldengraph QA engine.

Builds the KG ONCE, then answers every question under N answer-time env-configs
against the IDENTICAL graph. Because the graph is shared, the metric deltas are
purely the effect of the env knob, not build variance -- the confound that made a
head_to_head rebuild-per-run comparison of a downstream-only change unreliable (a
retrieval-only metric, support_recall, swung +-0.26 between rebuilds of the "same"
corpus, swamping the change under test).

Generic over ANY env-gated answer-time behavior: `--env NAME --values v1,v2,...`
produces one arm per value (`NAME=v1`, `NAME=v2`, ...). The headline use is
`--env GOLDENGRAPH_SYNTH_SAMPLES --values 1,5` -- does self-consistency voting
(complete_many + majority vote) actually help, measured on the identical graph?

`--self-test` uses a mock engine (no LLM) whose answer reads the env under test, so
the A/B plumbing is CI-validated without a provider, mirroring run_qa_e2e.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .harness import AnswerResult, BuildResult, run_engine_ab_env
from .run_qa_e2e import _build_engine, _chat_model, _load_corpus, _make_judge


class _MockEnvABEngine:
    """No-LLM engine whose answer VARIES by the env var under test, so the self-test
    exercises the A/B split (distinct arms) without a provider. It reads
    `GOLDENGRAPH_SYNTH_SAMPLES`: value "1" gives a non-answer, anything else names the
    gold -- a stand-in for "the env knob changes the answer"."""

    name = "mock-env-ab"
    fidelity = "self-test"

    def build_kg(self, corpus) -> BuildResult:
        return BuildResult(handle={"n": len(corpus.documents)}, input_tokens=1, output_tokens=1)

    def answer(self, handle, question: str) -> AnswerResult:
        samples = os.environ.get("GOLDENGRAPH_SYNTH_SAMPLES", "1")
        text = "(no answer)" if samples == "1" else "Ada"
        return AnswerResult(text=text, retrieved_fact_ids=("d1",), input_tokens=1, output_tokens=1)


def _write_ab(result: dict, *, md_path: Path, json_path: Path, env_name: str) -> None:
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    arms = result["arms"]
    labels = list(arms)
    lines = [
        f"# GoldenGraph QA -- same-graph env-A/B (`{env_name}`)",
        "",
        f"- corpus: `{arms[labels[0]]['corpus']}`  model: `{arms[labels[0]]['model']}`",
        f"- questions answered (per arm): **{result['n_answered']}**",
        f"- build cost: ${result['build_cost_usd']}  total (build + all arms): "
        f"${result['total_cost_usd']}",
        "",
        "| metric | " + " | ".join(labels) + " | delta (2nd - 1st) |",
        "| --- | " + " | ".join("---" for _ in labels) + " | --- |",
    ]
    for metric, per_label in result["comparison"].items():
        cells = []
        for label in labels:
            v = per_label.get(label)
            cells.append("-" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v))
        d = per_label.get("delta")
        d_cell = "-" if d is None else f"{d:+.4f}"
        lines.append(f"| {metric} | " + " | ".join(cells) + f" | {d_cell} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", default="goldengraph")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--corpus", choices=("engineered", "musique", "hotpotqa", "2wikimultihop"),
                   required=True)
    p.add_argument("--max-questions", type=int, default=50)
    p.add_argument("--ambiguity", type=float, default=0.5)
    p.add_argument("--musique-path", default=None)
    p.add_argument("--corpus-path", default=None)
    p.add_argument(
        "--env", required=True,
        help="name of the answer-time env var to A/B (e.g. GOLDENGRAPH_SYNTH_SAMPLES)",
    )
    p.add_argument(
        "--values", required=True,
        help="comma-separated values to A/B, in order (delta = 2nd - 1st). "
        "e.g. '1,5' -> arms GOLDENGRAPH_SYNTH_SAMPLES=1 and =5.",
    )
    p.add_argument(
        "--model", default=None,
        help="chat model override. When given, sets OPENAI_MODEL so _chat_model() + the "
        "engine both honor it; omit to use the env/default (gpt-4o-mini).",
    )
    p.add_argument("--budget-usd", type=float, default=25.0)
    p.add_argument("--judge", action="store_true", help="score the format-fair LLM-judge metric too")
    p.add_argument("--judge-model", default="gpt-4o-mini")
    p.add_argument("--out-md", required=True)
    p.add_argument("--out-json", required=True)
    args = p.parse_args(argv)

    values = [v.strip() for v in args.values.split(",") if v.strip()]
    if len(values) < 2:
        raise SystemExit("--values must list at least two values to A/B (e.g. '1,5')")
    # One arm per value: (label, env_dict) where env_dict overrides ONLY the var under test.
    arms = [(f"{args.env}={v}", {args.env: v}) for v in values]

    # Honor --model by exporting OPENAI_MODEL BEFORE building the engine / reading
    # _chat_model() (both are env-driven); omitted -> the env/default is used.
    if args.model:
        os.environ["OPENAI_MODEL"] = args.model

    out_md = Path(args.out_md).resolve()
    out_json = Path(args.out_json).resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)

    corpus = _load_corpus(
        args.corpus, args.max_questions, args.musique_path, args.ambiguity,
        corpus_path=args.corpus_path,
    )
    engine = _MockEnvABEngine() if args.self_test else _build_engine(args.engine)
    judge = _make_judge(args.judge_model) if (args.judge and not args.self_test) else None

    result = run_engine_ab_env(
        engine, corpus, model=_chat_model(), budget_usd=args.budget_usd, arms=arms, judge=judge,
    )
    _write_ab(result, md_path=out_md, json_path=out_json, env_name=args.env)

    cmp = result["comparison"]
    labels = list(result["arms"])
    print(f"wrote {out_md} ({result['n_answered']} q/arm, ${result['total_cost_usd']})")
    for metric in ("answer_match", "token_f1", "support_recall"):
        per = cmp.get(metric, {})
        cells = "  ".join(
            f"{label}={per.get(label):.4f}" for label in labels if isinstance(per.get(label), float)
        )
        d = per.get("delta")
        print(f"  {metric}: {cells}" + (f"  (delta {d:+.4f})" if isinstance(d, float) else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
