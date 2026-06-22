#!/usr/bin/env python
"""Entity-aware RAG -- collapse duplicate / contradictory facts before the LLM (#1092).

A knowledge base holds several near-duplicate, partly-conflicting facts about the
same companies (different spellings, missing fields, disagreeing values). A naive
RAG retriever would hand the LLM every matching chunk -- duplicates and
contradictions included. ``entity_aware_retrieve`` instead:

    retrieve  ->  resolve to entities (dedupe)  ->  conflict-aware fact merge

so the model sees ONE reconciled record per real-world entity, each field tagged
with the source it came from.

Zero cloud: the in-house embedder + deterministic most-complete merge run with no
network or torch. Pass ``llm_call=`` / ``budget=`` to let an LLM reconcile
borderline fields instead.
"""
import goldenmatch as gm
import polars as pl

# A knowledge base with duplicate + conflicting facts.
kb = pl.DataFrame(
    {
        "company": [
            "Acme Corp",
            "Acme Corp",          # dup spelling, fills the missing CEO
            "Acme Corp",          # dup spelling, adds revenue
            "Globex",
            "Globex",             # dup, fills founded year
            "Initech",
        ],
        "ceo": [
            "Jane Doe",
            "Jane A. Doe",        # more complete name
            None,
            "John Smith",
            "John Smith",
            "Bill Lumbergh",
        ],
        "founded": ["1999", "1999", "1999", None, "2005", "1998"],
        "revenue": [None, None, "$10M", "$50M", "$50M", "$2M"],
    }
)

print(f"Knowledge base: {kb.height} raw facts\n")

# Retrieve everything about "Acme" and reconcile it into entities.
# exact=["company"] resolves rows that share a company name into one entity;
# threshold=-1.0 keeps every retrieved row so the demo focuses on the merge.
result = gm.entity_aware_retrieve(
    kb,
    query="Acme Corp",
    column="company",
    exact=["company"],
    threshold=-1.0,
    k=10,
)

print(
    f"Retrieved {result.retrieved} raw records -> {result.n_entities} distinct "
    f"entities ({result.collapsed} duplicate/contradictory records collapsed "
    f"out of the LLM context)\n"
)

for e in result:
    print(f"Entity #{e.entity_id}  (merged from {e.size} record(s), "
          f"best similarity {e.score:.3f})")
    for field_name, value in e.record.items():
        prov = e.canonical.provenance.get(field_name)
        src = "synthesized" if prov is None or prov.source_index is None \
            else f"record {prov.source_index}"
        print(f"    {field_name:<8} = {str(value):<14} (from {src})")
    print()

print(
    f"The LLM now sees {result.n_entities} clean entities instead of "
    f"{result.retrieved} noisy rows."
)
