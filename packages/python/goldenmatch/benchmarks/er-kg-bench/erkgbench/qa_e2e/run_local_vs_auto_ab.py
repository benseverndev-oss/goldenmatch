"""CLI: same-run local-vs-auto A/B for the goldengraph QA engine.

Builds the KG ONCE, then answers every question under BOTH `mode="local"` (the
default answer path -- now with the template-free NL chain routing firing before
synthesis, `GOLDENGRAPH_QA_LOCAL_CHAIN`, default on) AND `mode="auto"` (the explicit
query-router path) against the identical graph. Because the graph is shared, the
metric deltas are purely the effect of answer-time routing, not build variance.

This is the headline that answers "does making chain routing the default in `local`
actually move the number, and how close does it get to `auto`?" -- run it after the
default-path change (`goldengraph.answer._local_chain_enabled`). `--self-test` uses a
mock engine (no LLM) so the A/B plumbing is CI-validated, mirroring run_qa_e2e.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .harness import AnswerResult, BuildResult, run_engine_ab
from .run_qa_e2e import _build_engine, _chat_model, _load_corpus, _make_judge


class _MockABEngine:
    """No-LLM engine whose answer VARIES by mode, so the self-test exercises the A/B
    split (two distinct arms) without a provider. `auto` names the gold; `local` does
    not -- a stand-in for "routing changes the answer"."""

    name = "mock-ab"
    fidelity = "self-test"

    def build_kg(self, corpus) -> BuildResult:
        return BuildResult(handle={"n": len(corpus.documents)}, input_tokens=1, output_tokens=1)

    def answer(self, handle, question: str, mode: str | None = None) -> AnswerResult:
        text = "Ada" if mode == "auto" else "(no answer)"
        return AnswerResult(text=text, retrieved_fact_ids=("d1",), input_tokens=1, output_tokens=1)


def _write_ab(result: dict, *, md_path: Path, json_path: Path) -> None:
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    arms = result["arms"]
    modes = list(arms)
    lines = [
        "# GoldenGraph QA -- same-run local-vs-auto A/B",
        "",
        f"- corpus: `{arms[modes[0]]['corpus']}`  model: `{arms[modes[0]]['model']}`",
        f"- questions answered (per arm): **{result['n_answered']}**",
        f"- build cost: ${result['build_cost_usd']}  total (build + both arms): "
        f"${result['total_cost_usd']}",
        "",
        "| metric | " + " | ".join(modes) + " | delta (2nd - 1st) |",
        "| --- | " + " | ".join("---" for _ in modes) + " | --- |",
    ]
    for metric, per_mode in result["comparison"].items():
        cells = []
        for m in modes:
            v = per_mode.get(m)
            cells.append("-" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v))
        d = per_mode.get("delta")
        d_cell = "-" if d is None else f"{d:+.4f}"
        lines.append(f"| {metric} | " + " | ".join(cells) + f" | {d_cell} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", default="goldengraph", help="only goldengraph supports the mode A/B")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--corpus", choices=("engineered", "musique", "hotpotqa", "2wikimultihop"),
                   required=True)
    p.add_argument("--max-questions", type=int, default=50)
    p.add_argument("--ambiguity", type=float, default=0.5)
    p.add_argument("--musique-path", default=None)
    p.add_argument("--corpus-path", default=None)
    p.add_argument(
        "--modes", default="local,auto",
        help="comma-separated answer modes to A/B, in order (delta = 2nd - 1st). "
        "Default 'local,auto' -- the headline comparison.",
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

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes:
        raise SystemExit("--modes must list at least one answer mode (e.g. 'local,auto')")
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
    engine = _MockABEngine() if args.self_test else _build_engine(args.engine)
    judge = _make_judge(args.judge_model) if (args.judge and not args.self_test) else None

    result = run_engine_ab(
        engine, corpus, model=_chat_model(), budget_usd=args.budget_usd, modes=modes, judge=judge,
    )
    _write_ab(result, md_path=out_md, json_path=out_json)

    cmp = result["comparison"]
    print(f"wrote {out_md} ({result['n_answered']} q/arm, ${result['total_cost_usd']})")
    for metric in ("answer_match", "token_f1", "support_recall"):
        per = cmp.get(metric, {})
        cells = "  ".join(
            f"{m}={per.get(m):.4f}" for m in modes if isinstance(per.get(m), float)
        )
        d = per.get("delta")
        print(f"  {metric}: {cells}" + (f"  (delta {d:+.4f})" if isinstance(d, float) else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
