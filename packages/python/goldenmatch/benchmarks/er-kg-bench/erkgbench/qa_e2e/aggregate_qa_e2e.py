"""Aggregate the per-(engine, ambiguity) QA-e2e result JSONs into the single headline
``RESULTS_QA_E2E.md``: the engine x ambiguity answer-match table (the
``(ER_accuracy)^hops`` thesis instrument) plus the engine x hop decay curve pooled
across the sweep. Pure stdlib + pure functions, so it is unit-testable on synthetic
result dicts with no LLM and no network.

Each input file is what ``harness.write_results`` writes: a JSON list of result dicts
(one per run). A sweep produces one file per (engine, ambiguity); this merges them.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_results(results_dir: str | Path) -> list[dict]:
    """Read every ``results_qa_e2e_*.json`` under ``results_dir`` and flatten the
    one-or-more result dicts each contains."""
    out: list[dict] = []
    for path in sorted(Path(results_dir).glob("results_qa_e2e_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.extend(payload if isinstance(payload, list) else [payload])
    return out


def _fmt(x: float) -> str:
    return f"{x:.3f}"


def _pooled_hop_decay(runs: list[dict]) -> dict[int, float]:
    """Mean answer_match by hop_count, pooled over every per-question record across an
    engine's runs (falls back to the stored decay_curve when per_question is absent)."""
    by_hop: dict[int, list[float]] = defaultdict(list)
    saw_per_question = False
    for r in runs:
        for rec in r.get("per_question", []):
            saw_per_question = True
            by_hop[int(rec["hop_count"])].append(float(rec["answer_match"]))
    if not saw_per_question:
        # decay_curve keys are strings after a JSON round-trip.
        for r in runs:
            for hop, val in (r.get("decay_curve") or {}).items():
                by_hop[int(hop)].append(float(val))
    return {hop: sum(v) / len(v) for hop, v in sorted(by_hop.items())}


def render_markdown(results: list[dict]) -> str:
    engines = sorted({r["engine"] for r in results})
    ambiguities = sorted({round(float(r.get("ambiguity", 0.0)), 4) for r in results})
    by_engine: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_engine[r["engine"]].append(r)
    # (engine, ambiguity) -> run (last one wins if duplicated)
    cell: dict[tuple[str, float], dict] = {}
    for r in results:
        cell[(r["engine"], round(float(r.get("ambiguity", 0.0)), 4))] = r

    corpora = sorted({r.get("corpus", "?") for r in results})
    models = sorted({r.get("model", "?") for r in results})
    n_q = sorted({r.get("n_questions", 0) for r in results})

    lines: list[str] = []
    lines.append("# ER-KG-Bench -- end-to-end multi-hop QA head-to-head (evidence program #1)")
    lines.append("")
    lines.append(
        f"- Corpus: {', '.join(corpora)} | Model: {', '.join(models)} | "
        f"Questions/run: {', '.join(str(n) for n in n_q)}"
    )
    lines.append(
        "- **answer-match** = normalized gold answer appears as a contiguous token run "
        "in the prediction (the correctness signal for generative answers; EM reads ~0)."
    )
    lines.append(
        "- The engineered corpus is the thesis instrument: accuracy that decays *slower* "
        "in ambiguity and hops reflects entity resolution that strands fewer facts."
    )
    lines.append("")

    # Table 1: engine x ambiguity answer-match (the decay-by-ambiguity curve).
    lines.append("## answer-match by ambiguity")
    lines.append("")
    header = "| engine | " + " | ".join(f"amb={a}" for a in ambiguities) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(ambiguities) + 1))
    for eng in engines:
        cells = []
        for a in ambiguities:
            r = cell.get((eng, a))
            cells.append(_fmt(r["answer_match"]) if r else "-")
        lines.append(f"| {eng} | " + " | ".join(cells) + " |")
    lines.append("")

    # Table 2: engine x hop answer-match, pooled across the ambiguity sweep.
    all_hops = sorted(
        {h for eng in engines for h in _pooled_hop_decay(by_engine[eng])}
    )
    if all_hops:
        lines.append("## answer-match by hop count (pooled across the ambiguity sweep)")
        lines.append("")
        lines.append("| engine | " + " | ".join(f"{h}-hop" for h in all_hops) + " |")
        lines.append("|" + "---|" * (len(all_hops) + 1))
        for eng in engines:
            decay = _pooled_hop_decay(by_engine[eng])
            cells = [_fmt(decay[h]) if h in decay else "-" for h in all_hops]
            lines.append(f"| {eng} | " + " | ".join(cells) + " |")
        lines.append("")

    # Summary: overall answer-match + cost + answered, per engine.
    lines.append("## summary")
    lines.append("")
    lines.append("| engine | mean answer-match | mean token-F1 | total cost (USD) | runs |")
    lines.append("|---|---|---|---|---|")
    for eng in engines:
        runs = by_engine[eng]
        mam = sum(r["answer_match"] for r in runs) / len(runs)
        mf1 = sum(r["token_f1"] for r in runs) / len(runs)
        cost = sum(float(r.get("cost_usd", 0.0)) for r in runs)
        lines.append(f"| {eng} | {_fmt(mam)} | {_fmt(mf1)} | {cost:.4f} | {len(runs)} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    results = load_results(args.results_dir)
    if not results:
        raise SystemExit(f"no results_qa_e2e_*.json found under {args.results_dir}")
    Path(args.out).write_text(render_markdown(results), encoding="utf-8")
    print(f"wrote {args.out} ({len(results)} runs, "
          f"{len({r['engine'] for r in results})} engines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
