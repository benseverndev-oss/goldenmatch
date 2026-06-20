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
from pathlib import Path

from demo import kg  # pyright: ignore[reportMissingImports]  # namespace pkg from bench root

from erkgbench.qa_loader import QAItem, load_qa, load_qa_facts

_RECORDS = Path(__file__).resolve().parent.parent / "dataset" / "records.csv"


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


def _landed_facts(graph: "kg.KG", seed_surface: str) -> set[str]:
    """Facts the engine co-retrieves for the entity queried by `seed_surface`:
    the facts on the node whose surface forms include the query (the
    under_merge_answer landed-node model). Empty if no node matches."""
    node = next((n for n in graph.nodes if seed_surface in n.names), None)
    return set(node.facts) if node else set()


def item_completeness(
    partition: list[list[int]],
    item: QAItem,
    mentions: dict[int, str],
    types: dict[int, str],
    contexts: dict[int, str],
    facts_by_record: dict[int, list[str]],
) -> float:
    graph = kg.build_kg(partition, mentions, types, contexts, facts=facts_by_record)
    gold = set(item.gold_facts)
    if not gold:
        return 1.0
    retrieved = _landed_facts(graph, item.seed_surface)
    return len(gold & retrieved) / len(gold)


def engine_completeness(
    partition: list[list[int]],
    items: list[QAItem],
    mentions: dict[int, str],
    types: dict[int, str],
    contexts: dict[int, str],
    facts_by_record: dict[int, list[str]],
    failure_class: dict[int, str] | None = None,
) -> dict:
    """Mean fact-completeness for one engine's partition, with a per-item +
    per-failure-class breakdown."""
    per_item = []
    for it in items:
        c = item_completeness(partition, it, mentions, types, contexts, facts_by_record)
        fc = None
        if failure_class:
            rid = next(iter(it.facts))  # the entity's failure class (any member)
            fc = failure_class.get(rid)
        per_item.append({"qa_id": it.qa_id, "completeness": c, "failure_class": fc})
    mean = sum(p["completeness"] for p in per_item) / len(per_item) if per_item else 0.0
    by_class: dict[str, list[float]] = {}
    for p in per_item:
        if p["failure_class"]:
            by_class.setdefault(p["failure_class"], []).append(p["completeness"])
    per_class = {k: sum(v) / len(v) for k, v in by_class.items()}
    return {"mean_completeness": mean, "items": per_item, "per_class": per_class}


def run_qa_eval(
    adapters: list,
    records: list,
    items: list[QAItem] | None = None,
    facts_by_record: dict[int, list[str]] | None = None,
    mentions: dict[int, str] | None = None,
    types: dict[int, str] | None = None,
    contexts: dict[int, str] | None = None,
    failure_class: dict[int, str] | None = None,
) -> list[dict]:
    """Run each adapter over `records`, score fact-completeness on the QA layer.

    Adapters that raise (e.g. missing optional dep) yield a `skipped` row."""
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
            partition, items, mentions, types, contexts, facts_by_record, failure_class
        )
        rows.append({"name": name, "status": "ok", **res})
    return rows
