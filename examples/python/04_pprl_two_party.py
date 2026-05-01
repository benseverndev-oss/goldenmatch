"""04 — Privacy-preserving record linkage (PPRL) between two parties.

Each party's raw PII never crosses the boundary — both encode their records
into Bloom filters locally, then a third party (or one of the two, if mutually
trusted for the linkage step only) compares the encoded sets.

This script simulates both parties locally for demonstration. In a real
deployment each party runs `auto_configure_pprl` + bloom encoding on their
own machines and ships only the encoded shards.

Run:
    pip install goldenmatch[pprl] polars
    python 04_pprl_two_party.py
"""
from __future__ import annotations

import polars as pl

from goldenmatch.pprl.protocol import PPRLConfig, run_pprl


# Two parties with overlapping but messy PII.
party_a = pl.DataFrame({
    "id_a":       [1, 2, 3, 4],
    "first_name": ["Jane", "Robert", "Alice", "Mark"],
    "last_name":  ["Smith", "Jones", "Lee", "Davis"],
    "dob":        ["1990-03-15", "1985-07-22", "1992-11-30", "1978-01-05"],
    "zip":        ["10001", "94110", "60601", "30303"],
})

party_b = pl.DataFrame({
    "id_b":       [101, 102, 103, 104, 105],
    "first_name": ["Jane", "Bob", "Alicia", "Mark", "Jenny"],
    "last_name":  ["Smithe", "Jones", "Li", "Davis", "Wong"],
    "dob":        ["1990-03-15", "1985-07-22", "1992-11-30", "1978-01-05", "1995-06-10"],
    "zip":        ["10001", "94110", "60601", "30303", "98101"],
})


def main() -> None:
    config = PPRLConfig(
        fields=["first_name", "last_name", "dob", "zip"],
        threshold=0.85,
        security_level="high",  # standard | high | paranoid
    )

    # In production each party encodes locally and ships only the encoded
    # bloom-filter columns. run_pprl handles the encoding here for demo.
    result = run_pprl(party_a=party_a, party_b=party_b, config=config)

    print(f"matched pairs: {len(result.matches)}")
    for a_id, b_id, score in result.matches[:10]:
        a_row = party_a.filter(pl.col("id_a") == a_id).to_dicts()[0]
        b_row = party_b.filter(pl.col("id_b") == b_id).to_dicts()[0]
        print(f"  {a_id:3d} ↔ {b_id:3d}  score={score:.3f}  "
              f"{a_row['first_name']} {a_row['last_name']} ↔ "
              f"{b_row['first_name']} {b_row['last_name']}")

    rate_a = len(result.matches) / party_a.height
    print(f"\n{rate_a:.0%} of party_a records found a match in party_b")


if __name__ == "__main__":
    main()
