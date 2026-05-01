"""Example 9 — Domain dictionaries (new in v0.3)

Shows how to boost mapping accuracy on domain-specific schemas by loading
curated alias dictionaries. Without the dictionary, `mrn` and `patient_id`
are only connected via fuzzy name similarity (~0.27 confidence). With
`domains=["healthcare"]`, the AliasScorer fires a direct match at 0.95.

Shipped domains: generic (default), healthcare, finance, ecommerce.

Run:
    python examples/09_domain_dictionaries.py
"""
from __future__ import annotations

from infermap import FieldInfo, MapEngine, SchemaInfo
from infermap.dictionaries import available_domains


def make_schema(name: str, fields: list[tuple[str, str, list[str]]]) -> SchemaInfo:
    return SchemaInfo(
        fields=[
            FieldInfo(name=n, dtype=dt, sample_values=samples, value_count=len(samples))
            for n, dt, samples in fields
        ],
        source_name=name,
    )


def main() -> None:
    print(f"Available domains: {available_domains()}\n")

    # Typical EHR-to-research mapping task
    source = make_schema(
        "epic_extract",
        [
            ("MRN", "string", ["MRN001", "MRN002", "MRN003"]),
            ("DOB", "string", ["1980-01-15", "1992-06-30", "1975-11-22"]),
            ("admit_dt", "string", ["2026-01-01", "2026-01-02", "2026-01-03"]),
            ("dx_code", "string", ["E11.9", "I10", "J45.901"]),
        ],
    )
    target = make_schema(
        "research_datamart",
        [
            ("patient_id", "string", ["P100", "P200", "P300"]),
            ("date_of_birth", "string", ["1990-01-01", "1988-04-12", "1976-08-30"]),
            ("admission_date", "string", ["2026-02-01", "2026-02-02", "2026-02-03"]),
            ("diagnosis_code", "string", ["G40.901", "N18.6", "F32.9"]),
        ],
    )

    # --- Without domain dictionary ---
    base = MapEngine()
    base_result = base.map_schemas(source, target)
    print("=== Without domain dictionary ===")
    for m in base_result.mappings:
        print(f"  {m.source:>10} -> {m.target:<16} conf={m.confidence:.3f}")
    print(f"  Unmapped: {base_result.unmapped_source}\n")

    # --- With healthcare domain ---
    healthcare = MapEngine(domains=["healthcare"])
    hc_result = healthcare.map_schemas(source, target)
    print("=== With domains=['healthcare'] ===")
    for m in hc_result.mappings:
        print(f"  {m.source:>10} -> {m.target:<16} conf={m.confidence:.3f}")
    print(f"  Unmapped: {hc_result.unmapped_source}\n")

    # --- Finance example ---
    fin_src = make_schema("ledger", [
        ("txn_id", "string", ["T1", "T2"]),
        ("amt", "float64", ["19.99", "42.00"]),
        ("ccy", "string", ["USD", "EUR"]),
    ])
    fin_tgt = make_schema("warehouse", [
        ("transaction_id", "string", ["X1", "X2"]),
        ("amount", "float64", ["100.0", "200.0"]),
        ("currency", "string", ["GBP", "JPY"]),
    ])
    fin = MapEngine(domains=["finance"])
    fin_result = fin.map_schemas(fin_src, fin_tgt)
    print("=== Finance domain ===")
    for m in fin_result.mappings:
        print(f"  {m.source:>10} -> {m.target:<16} conf={m.confidence:.3f}")


if __name__ == "__main__":
    main()
