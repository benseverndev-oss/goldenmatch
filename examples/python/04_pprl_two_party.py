"""04 — Privacy-preserving record linkage (PPRL) between two parties.

Each party's raw PII never crosses the boundary — both encode their records
into Bloom filters locally, then a third party (or one of the two, if mutually
trusted for the linkage step only) compares the encoded sets.

This script simulates both parties locally for demonstration. In a real
deployment each party runs encoding on their own machines and ships only the
encoded shards.

Run:
    pip install goldenmatch[pprl] polars
    python 04_pprl_two_party.py
"""
from __future__ import annotations

import polars as pl

from goldenmatch.pprl.protocol import PPRLConfig, run_pprl


# Two parties with overlapping but messy PII.
party_a = pl.DataFrame({
    "id":         [1, 2, 3, 4],
    "first_name": ["Jane", "Robert", "Alice", "Mark"],
    "last_name":  ["Smith", "Jones", "Lee", "Davis"],
    "dob":        ["1990-03-15", "1985-07-22", "1992-11-30", "1978-01-05"],
    "zip":        ["10001", "94110", "60601", "30303"],
})

party_b = pl.DataFrame({
    "id":         [101, 102, 103, 104, 105],
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

    # Each party encodes locally; run_pprl handles encoding here for the demo.
    result = run_pprl(
        df_a=party_a, df_b=party_b, config=config,
        party_a_id="hospital_a", party_b_id="hospital_b",
    )

    print(f"linked clusters:   {len(result.clusters)}")
    print(f"matched-pair count: {result.match_count}")
    print(f"comparisons total:  {result.total_comparisons}")
    print()

    # Each cluster is a list of (party_id, record_id) tuples.
    # Show the first 10 cross-party clusters.
    shown = 0
    for cluster_id, members in result.clusters.items():
        a_member = next((m for m in members if m[0] == "hospital_a"), None)
        b_member = next((m for m in members if m[0] == "hospital_b"), None)
        if not (a_member and b_member):
            continue
        a_rec = party_a.filter(pl.col("id") == a_member[1]).to_dicts()[0]
        b_rec = party_b.filter(pl.col("id") == b_member[1]).to_dicts()[0]
        print(f"  cluster {cluster_id}:  "
              f"a:{a_member[1]} ↔ b:{b_member[1]}  "
              f"{a_rec['first_name']} {a_rec['last_name']} ↔ "
              f"{b_rec['first_name']} {b_rec['last_name']}")
        shown += 1
        if shown >= 10:
            break

    rate_a = result.match_count / max(party_a.height, 1)
    print(f"\n{rate_a:.0%} of party_a records linked into a cross-party cluster")


if __name__ == "__main__":
    main()
