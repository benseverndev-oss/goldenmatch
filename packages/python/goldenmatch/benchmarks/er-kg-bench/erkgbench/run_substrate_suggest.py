"""SP-C suggester smoke: run suggest_substrate_config on the HOMOGRAPH engineered corpus with the real
LLM + build. The LLM should detect the injected homographs -> expect_homographs -> name_ci_type, and the
self-verify should ACCEPT it (beats the naive name_ci baseline on relational F1 via precision recovery).
Needs the native store + an LLM -> Modal only.
"""
from __future__ import annotations

import argparse
import os

from erkgbench.qa_e2e.engineered import emit_gold_mentions, generate_engineered
from erkgbench.substrate_suggest import build_and_score_real, suggest_substrate_config


def _chat(prompt: str) -> str:
    from goldengraph.llm import OpenAIClient

    # json_mode=True (response_format=json_object): the 7B homograph perception is exactly the weak-OSS-
    # model "emits prose/fenced/invalid JSON" failure mode -- forcing JSON avoids a null result being
    # MISATTRIBUTED to "the LLM can't perceive homographs" when it's really a fixable formatting artifact.
    return OpenAIClient(model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")._chat(prompt, json_mode=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="SP-C suggester smoke (homograph engineered corpus).")
    ap.add_argument("--homograph", type=int, default=4)
    ap.add_argument("--ambiguity", type=float, default=0.0)
    ap.add_argument("--out-md", default="SUBSTRATE_SUGGEST.md")
    ap.add_argument("--score-beta", type=float, default=0.5,
                    help="F-beta for the accept metric; <1 favors precision (the homograph win). "
                         "0.5 accepts the SP-C precision win the F1 default (1.0) hid.")
    args = ap.parse_args()

    os.environ["GOLDENGRAPH_SUBSTRATE_SCORE_BETA"] = str(args.score_beta)
    os.environ["GOLDENGRAPH_BENCH_HOMOGRAPH"] = str(args.homograph)
    os.environ.pop("GOLDENGRAPH_BENCH_COOCCUR", None)
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=args.ambiguity)
    gold = emit_gold_mentions(corpus.documents)
    # PASS Document objects (with .text/.id), NOT [d.text] -- build_and_score_real needs the real doc-ids.
    # Sample the WHOLE corpus for the proposer: with ~139 SHORT docs, a small sample gives an INCOMPLETE
    # relation set (schema_canon then drops out-of-vocab edges -> F1 collapse) and misses both halves of
    # the diluted homograph pairs. Whole-corpus sampling is the fair perception test (prompt stays small).
    res = suggest_substrate_config(corpus.documents, gold=gold, qid_aliases=None,
                                   build_and_score=build_and_score_real, chat=_chat,
                                   sample_docs=len(corpus.documents))

    b, p = res.baseline_scorecard, res.proposed_scorecard
    print(f"[suggest] accepted={res.accepted} flags={res.flags} "
          f"baseline_F1={b['relational']['f1']:.4f} proposed_F1={p['relational']['f1']:.4f} "
          f"winner_xdoc={res.config.xdoc_key} canon={res.config.entity_type_canon}", flush=True)
    md = (
        "# SP-C Suggester Smoke (homograph engineered)\n\n"
        f"- accepted: `{res.accepted}`  flags: `{res.flags}`\n"
        f"- baseline relational F1: {b['relational']['f1']:.4f} (P={b['relational']['precision']:.4f})\n"
        f"- proposed relational F1: {p['relational']['f1']:.4f} (P={p['relational']['precision']:.4f})\n"
        f"- winner: xdoc_key=`{res.config.xdoc_key}` entity_type_canon={res.config.entity_type_canon}\n"
    )
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md, flush=True)


if __name__ == "__main__":
    main()
