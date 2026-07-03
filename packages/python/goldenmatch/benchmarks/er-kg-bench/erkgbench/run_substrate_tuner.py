"""SP-B2 staged-tuner smoke: run the deterministic staged-ejection optimizer end-to-end on the wiki
corpus with the REAL build (build_and_score_real). Establishes the deterministic baseline scorecard
that SP-C's LLM proposer must beat. Needs the native store + an LLM -> Modal/CI, NOT box-safe.

The initial config is computed from the raw doc TEXTS (profile_corpus wants strings), then passed
explicitly so run_staged does not re-profile the Document objects it hands to the build.
"""
from __future__ import annotations

import argparse
import os

from goldengraph.config import for_profile, profile_corpus

from erkgbench.substrate_tuner import GateThresholds, build_and_score_real, run_staged


def _fmt_sc(sc: dict) -> str:
    p = sc.get("presence")
    pres = "None" if p is None else f"{p['coverage']:.4f}"
    r = sc["relational"]
    c = sc["connectivity"]
    return (f"presence={pres} relational_F1={r['f1']:.4f} R={r['recall']:.4f} P={r['precision']:.4f} "
            f"conn_edge_recall={c['edge_recall']:.4f} comp={sc['coherence']['components']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Staged-tuner smoke on the wiki corpus (real build).")
    ap.add_argument("--budget", type=int, default=3)
    ap.add_argument("--presence-min", type=float, default=0.90)
    ap.add_argument("--relational-f1-min", type=float, default=0.50)
    ap.add_argument("--out-md", default="SUBSTRATE_TUNER.md")
    args = ap.parse_args()

    from erkgbench.qa_e2e.wiki_corpus import load_wiki_corpus

    documents, gold, qid_aliases = load_wiki_corpus()
    # Profile off the raw texts (Document objects would choke profile_corpus); wiki is dense -> for_profile
    # enables chunking + name_ci. Pass it in so run_staged doesn't re-profile the Documents.
    init = for_profile(profile_corpus([d.text for d in documents]))
    thresholds = GateThresholds(presence_min=args.presence_min, relational_f1_min=args.relational_f1_min)

    res = run_staged(documents, gold, qid_aliases, build_and_score=build_and_score_real,
                     thresholds=thresholds, budget=args.budget, initial_config=init)

    print(f"[substrate-tuner] stopped={res.stopped_reason} rounds={len(res.trace)} "
          f"init_xdoc={init.xdoc_key} init_chunk={init.chunk_extract}", flush=True)
    for rr in res.trace:
        print(f"[tuner-round {rr.round}] xdoc={rr.config.xdoc_key} chunk={rr.config.chunk_extract} "
              f"schema_canon={rr.config.schema_canon} | {_fmt_sc(rr.scorecard)} | "
              f"gate_passed={rr.gate.passed} failing_axis={rr.gate.failing_axis} "
              f"escalated_to={rr.escalated_to}", flush=True)
    print(f"[substrate-tuner] WINNER xdoc={res.config.xdoc_key} chunk={res.config.chunk_extract} "
          f"schema_canon={res.config.schema_canon} | FULL {_fmt_sc(res.full_scorecard)}", flush=True)

    lines = [
        "# Substrate Staged-Tuner Smoke (wiki)\n",
        f"- stopped_reason: `{res.stopped_reason}`  rounds: {len(res.trace)}",
        f"- WINNER config: xdoc_key=`{res.config.xdoc_key}` chunk_extract={res.config.chunk_extract} "
        f"schema_canon={res.config.schema_canon}",
        f"- FULL scorecard: {_fmt_sc(res.full_scorecard)}\n",
        "| round | xdoc_key | chunk | presence | relational_F1 | gate_passed | failing_axis | escalated_to |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rr in res.trace:
        p = rr.scorecard.get("presence")
        pres = "None" if p is None else f"{p['coverage']:.4f}"
        lines.append(
            f"| {rr.round} | {rr.config.xdoc_key} | {rr.config.chunk_extract} | {pres} | "
            f"{rr.scorecard['relational']['f1']:.4f} | {rr.gate.passed} | {rr.gate.failing_axis} | "
            f"{rr.escalated_to} |"
        )
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    if os.environ.get("GOLDENGRAPH_TUNER_ECHO_MD", "") not in ("", "0", "false"):
        print("\n" + "\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
