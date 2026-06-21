"""SP6 Half 2 -- deterministic fact-completeness eval (the downstream win).

Measures the fact co-location that CAUSES the README's `(ER_accuracy)^hops`
decay: a RESOLVED KG puts all of an entity's facts on one node, so querying the
entity returns them all; an unresolved/exact-match KG strands them on separate
surface-form nodes. We do NOT traverse hops (the KG model has no edges) -- the
metric is a single resolved-vs-unresolved comparison over the authored QA layer.

Landed-node selection reuses the `demo/narrative.under_merge_answer` model (the
node whose surface forms include `seed_surface`), applied to facts.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from demo import kg  # pyright: ignore[reportMissingImports]  # namespace pkg from bench root

from erkgbench.qa_loader import QAItem, load_qa, load_qa_facts

_RECORDS = Path(__file__).resolve().parent.parent / "dataset" / "records.csv"
_RESULTS_QA = Path(__file__).resolve().parent.parent / "results" / "RESULTS_QA.md"

_DISCLAIMER = (
    "> The QA layer is **authored / synthetic** (facts hand-attached to surface "
    "forms). This measures the *fact co-location* that causes the "
    "`(ER_accuracy)^hops` decay -- NOT real-world QA accuracy, and NOT the "
    "hop-exponent (the KG model has no edges to traverse)."
)


class ExactMatchFloorAdapter:
    """The unresolved-KG baseline: one node per distinct surface string (exact
    match). Deterministic, no deps -- the controlled floor goldengraph beats."""

    name = "exact-match-floor"
    fidelity = "validated"
    deterministic = True
    defaults = "one node per distinct surface string (no entity resolution)"

    def resolve(self, records) -> list[list[int]]:
        from collections import defaultdict

        by_form: dict[str, list[int]] = defaultdict(list)
        for r in records:
            by_form[r.mention].append(r.index)
        return list(by_form.values())


def load_corpus(path: Path | None = None):
    """records.csv -> (mentions, types, contexts, failure_class) keyed by record_id."""
    p = path or _RECORDS
    mentions: dict[int, str] = {}
    types: dict[int, str] = {}
    contexts: dict[int, str] = {}
    failure_class: dict[int, str] = {}
    for row in csv.DictReader(p.open(encoding="utf-8")):
        i = int(row["record_id"])
        mentions[i] = row["mention"]
        types[i] = row["entity_type"]
        contexts[i] = row["context"]
        failure_class[i] = row["failure_class"]
    return mentions, types, contexts, failure_class


def _landed_facts(graph: kg.KG, seed_surface: str) -> set[str]:
    """Facts the engine co-retrieves for the entity queried by `seed_surface`:
    the facts on the node whose surface forms include the query (the
    under_merge_answer landed-node model). Empty if no node matches."""
    node = next((n for n in graph.nodes if seed_surface in n.names), None)
    return set(node.facts) if node else set()


def _item_retrieved(
    partition: list[list[int]],
    item: QAItem,
    mentions: dict[int, str],
    types: dict[int, str],
    contexts: dict[int, str],
    facts_by_record: dict[int, list[str]],
) -> tuple[set[str], set[str]]:
    """(gold_facts, retrieved_facts) for one item under one partition."""
    graph = kg.build_kg(partition, mentions, types, contexts, facts=facts_by_record)
    return set(item.gold_facts), _landed_facts(graph, item.seed_surface)


def item_completeness(
    partition: list[list[int]],
    item: QAItem,
    mentions: dict[int, str],
    types: dict[int, str],
    contexts: dict[int, str],
    facts_by_record: dict[int, list[str]],
) -> float:
    gold, retrieved = _item_retrieved(
        partition, item, mentions, types, contexts, facts_by_record
    )
    if not gold:
        return 1.0
    return len(gold & retrieved) / len(gold)


def engine_completeness(
    partition: list[list[int]],
    items: list[QAItem],
    mentions: dict[int, str],
    types: dict[int, str],
    contexts: dict[int, str],
    facts_by_record: dict[int, list[str]],
    failure_class: dict[int, str] | None = None,
    judge=None,
) -> dict:
    """Mean fact-completeness for one engine's partition, with a per-item +
    per-failure-class breakdown.

    `judge` (opt-in, non-gated) is `callable(item, retrieved_facts) -> float` in
    [0,1] (LLM-judged answer correctness). When given, per-item `correctness` +
    `mean_correctness` are added. Default `None` -> deterministic path only."""
    per_item = []
    for it in items:
        gold, retrieved = _item_retrieved(
            partition, it, mentions, types, contexts, facts_by_record
        )
        c = 1.0 if not gold else len(gold & retrieved) / len(gold)
        fc = None
        if failure_class:
            rid = next(iter(it.facts))  # the entity's failure class (any member)
            fc = failure_class.get(rid)
        row = {"qa_id": it.qa_id, "completeness": c, "failure_class": fc}
        if judge is not None:
            row["correctness"] = float(judge(it, retrieved))
        per_item.append(row)
    mean = sum(p["completeness"] for p in per_item) / len(per_item) if per_item else 0.0
    by_class: dict[str, list[float]] = {}
    for p in per_item:
        if p["failure_class"]:
            by_class.setdefault(p["failure_class"], []).append(p["completeness"])
    per_class = {k: sum(v) / len(v) for k, v in by_class.items()}
    out = {"mean_completeness": mean, "items": per_item, "per_class": per_class}
    if judge is not None and per_item:
        out["mean_correctness"] = sum(p["correctness"] for p in per_item) / len(per_item)
    return out


def run_qa_eval(
    adapters: list,
    records: list,
    items: list[QAItem] | None = None,
    facts_by_record: dict[int, list[str]] | None = None,
    mentions: dict[int, str] | None = None,
    types: dict[int, str] | None = None,
    contexts: dict[int, str] | None = None,
    failure_class: dict[int, str] | None = None,
    judge=None,
) -> list[dict]:
    """Run each adapter over `records`, score fact-completeness on the QA layer.

    Adapters that raise (e.g. missing optional dep) yield a `skipped` row.
    `judge` (opt-in) adds LLM-judged correctness -- see `engine_completeness`."""
    items = items if items is not None else load_qa()
    facts_by_record = facts_by_record if facts_by_record is not None else load_qa_facts(items)
    if mentions is None:
        mentions, types, contexts, failure_class = load_corpus()
    rows = []
    for ad in adapters:
        name = getattr(ad, "name", ad.__class__.__name__)
        try:
            partition = ad.resolve(records)
        except Exception as exc:  # noqa: BLE001 - record + continue, never fatal
            rows.append({"name": name, "status": "skipped", "error": str(exc)[:200]})
            continue
        res = engine_completeness(
            partition, items, mentions, types, contexts, facts_by_record,
            failure_class, judge=judge,
        )
        rows.append({"name": name, "status": "ok", **res})
    return rows


def render_results_qa(rows: list[dict]) -> str:
    """Markdown: per-engine mean fact-completeness (+ opt LLM correctness) +
    per-failure-class breakdown + the synthetic-corpus disclaimer."""
    lines = [
        "# ER-KG-Bench -- QA fact-completeness (SP6)",
        "",
        "Does resolution buy complete retrieval? Mean fraction of an entity's gold",
        "facts co-located where a query for it lands -- resolved KGs put all facts on",
        "one node; an exact-match KG strands them across surface forms.",
        "",
        _DISCLAIMER,
        "",
        "| Engine | status | mean fact-completeness | mean correctness (LLM) |",
        "|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: x.get("mean_completeness", -1.0), reverse=True):
        if r.get("status") != "ok":
            lines.append(f"| {r['name']} | {r.get('status', '?')} | - | - |")
            continue
        corr = f"{r['mean_correctness']:.3f}" if "mean_correctness" in r else "-"
        lines.append(f"| {r['name']} | ok | **{r['mean_completeness']:.3f}** | {corr} |")
    ok = [r for r in rows if r.get("status") == "ok" and r.get("per_class")]
    if ok:
        classes = sorted({c for r in ok for c in r["per_class"]})
        lines += [
            "",
            "## Per-failure-class fact-completeness",
            "",
            "| Engine | " + " | ".join(classes) + " |",
            "|---|" + "|".join("---" for _ in classes) + "|",
        ]
        for r in ok:
            cells = " | ".join(
                f"{r['per_class'][c]:.2f}" if c in r["per_class"] else "-" for c in classes
            )
            lines.append(f"| {r['name']} | {cells} |")
    return "\n".join(lines) + "\n"


def _load_records() -> list:
    from erkgbench.adapters import Record

    return [
        Record(
            index=int(row["record_id"]),
            mention=row["mention"],
            entity_type=row["entity_type"],
            context=row["context"],
        )
        for row in csv.DictReader(_RECORDS.open(encoding="utf-8"))
    ]


def _openai_judge():
    """Best-effort LLM judge (opt-in): grade the retrieved facts vs gold_answer.
    Lazy; needs OPENAI_API_KEY + goldengraph.llm.OpenAIClient."""
    from goldengraph.llm import OpenAIClient

    client = OpenAIClient()

    def judge(item, retrieved: set[str]) -> float:
        answer = "; ".join(sorted(retrieved)) or "(no facts retrieved)"
        prompt = (
            f"Question: {item.question}\nProposed answer (from retrieved facts): "
            f"{answer}\nReference answer: {item.gold_answer}\n"
            "Does the proposed answer capture the reference answer? Reply 1 or 0."
        )
        return 1.0 if client.complete(prompt).strip().startswith("1") else 0.0

    return judge


def _framework_adapters() -> list:
    """Opt-in real-framework KGs (Task 7); empty + best-effort by default."""
    try:
        from erkgbench.qa_frameworks import framework_adapters

        return framework_adapters()
    except Exception:  # noqa: BLE001 - frameworks are best-effort, never fatal
        return []


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="SP6 QA fact-completeness eval")
    ap.add_argument("--out", default=str(_RESULTS_QA))
    ap.add_argument(
        "--assert-margin",
        type=float,
        default=None,
        help="exit 1 if goldengraph mean - exact-match-floor mean < margin (the gate)",
    )
    ap.add_argument("--with-llm", action="store_true", help="LLM-judged correctness (needs OPENAI_API_KEY)")
    ap.add_argument("--with-frameworks", action="store_true", help="add real-framework KGs (best-effort)")
    args = ap.parse_args(argv)

    records = _load_records()
    from erkgbench.adapters.goldengraph_adapter import GoldenGraphAdapter

    adapters = [ExactMatchFloorAdapter(), GoldenGraphAdapter()]
    if args.with_frameworks:
        adapters.extend(_framework_adapters())
    judge = _openai_judge() if args.with_llm else None

    rows = run_qa_eval(adapters, records, judge=judge)
    md = render_results_qa(rows)
    Path(args.out).write_text(md, encoding="utf-8")
    print(md)

    if args.assert_margin is not None:
        by = {r["name"]: r for r in rows}
        gg, floor = by.get("goldengraph"), by.get("exact-match-floor")
        if not gg or gg.get("status") != "ok":
            print("::error:: goldengraph engine did not run", file=sys.stderr)
            return 1
        margin = gg["mean_completeness"] - floor["mean_completeness"]
        if margin < args.assert_margin:
            print(f"::error:: margin {margin:.3f} < {args.assert_margin}", file=sys.stderr)
            return 1
        print(
            f"gate OK: goldengraph {gg['mean_completeness']:.3f} - floor "
            f"{floor['mean_completeness']:.3f} = {margin:.3f} >= {args.assert_margin}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
