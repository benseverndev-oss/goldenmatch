"""Deterministic relational ER fixture generator.

Generates a synthetic academic co-authorship dataset where entity resolution
is only possible via relational evidence (co-author neighborhoods), not names.

Structure
---------
* Each real *entity* has a small fixed set of collaborator entities.
* Each entity gets several author RECORDS; every record is linked (via papers)
  to co-author records drawn from that entity's collaborator entities.
* **Homonyms:** some distinct entities share a surface name token  =>
  name-only ER wrongly merges them, but their neighborhoods differ.
* **Synonyms:** some entity's records carry name variants ("J Smith",
  "John Smith")  =>  name-only ER wrongly splits them, but same neighborhood.

The invariant: two records share the same true entity IFF their (resolved)
co-author neighborhoods overlap; name similarity is deliberately unreliable.

Dependencies: stdlib ``random`` + ``polars`` only; no goldenmatch imports.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import polars as pl


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelationalFixture:
    """Immutable container for a generated ER fixture."""

    authors: pl.DataFrame
    """Columns: __row_id__ (int), name (str), author_truth (int).
    author_truth is the true entity id — the label, never fed to ER."""

    papers: pl.DataFrame
    """Columns: __row_id__ (int), paper_id (str)."""

    authorship: pl.DataFrame
    """Columns: paper_row_id (int), author_row_id (int)."""

    truth: dict  # author_row_id -> author_truth


def generate_relational_fixture(
    seed: int,
    n_entities: int,
    *,
    homonym_rate: float = 0.30,
    synonym_rate: float = 0.30,
    papers_per_author: int = 3,
    coauthors_per_paper: int = 2,
    records_per_entity_min: int = 2,
    records_per_entity_max: int = 4,
    collaborators_per_entity_min: int = 2,
    collaborators_per_entity_max: int = 4,
) -> RelationalFixture:
    """Generate a deterministic relational ER fixture.

    Parameters
    ----------
    seed:
        RNG seed; identical seed + params => identical output.
    n_entities:
        Number of distinct real-world author entities.
    homonym_rate:
        Fraction of entities whose surface name is collapsed to a shared
        "ambiguous" token (e.g. "J Smith") — forcing false-merge errors for
        name-only ER.
    synonym_rate:
        Fraction of entities whose records carry name *variants* rather than
        one canonical form — forcing false-split errors for name-only ER.
    papers_per_author:
        Number of papers each author record appears on.
    coauthors_per_paper:
        Number of co-author records added per paper (from collaborator entities).
    records_per_entity_min / max:
        Range for the number of author records per entity (uniform random).
    collaborators_per_entity_min / max:
        Range for collaborator-entity count (uniform random).
    """
    rng = random.Random(seed)

    # ------------------------------------------------------------------
    # 1. Entity-level collaboration graph
    # ------------------------------------------------------------------
    # collaborators[entity_id] = list of collaborator entity_ids
    collaborators: dict[int, list[int]] = {}
    for eid in range(n_entities):
        n_collab = rng.randint(
            collaborators_per_entity_min,
            collaborators_per_entity_max,
        )
        # Pick distinct collaborators from the other entities
        others = [x for x in range(n_entities) if x != eid]
        if len(others) < n_collab:
            n_collab = len(others)
        collaborators[eid] = rng.sample(others, n_collab)

    # ------------------------------------------------------------------
    # 2. Entity-level base names + homonym/synonym designation
    # ------------------------------------------------------------------
    # Base name pool: varied enough to be realistic
    _FIRST = [
        "John", "Jane", "Robert", "Mary", "William", "Linda", "James",
        "Patricia", "Michael", "Barbara", "David", "Susan", "Richard",
        "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Daniel",
        "Nancy", "Matthew", "Lisa", "Anthony", "Betty", "Donald",
    ]
    _LAST = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
        "Miller", "Davis", "Wilson", "Moore", "Taylor", "Anderson",
        "Thomas", "Jackson", "White", "Harris", "Martin", "Thompson",
        "Young", "Robinson", "Walker", "Hall", "Allen", "Wright",
        "Scott", "Green", "Baker", "Nelson", "Carter", "Mitchell",
    ]

    # Each entity gets a canonical (first, last) pair
    entity_canonical: dict[int, tuple[str, str]] = {}
    used = set()
    for eid in range(n_entities):
        # Try to pick a unique pair; if exhausted, allow repeats
        for _ in range(100):
            first = rng.choice(_FIRST)
            last = rng.choice(_LAST)
            key = (first, last)
            if key not in used:
                used.add(key)
                entity_canonical[eid] = key
                break
        else:
            entity_canonical[eid] = (rng.choice(_FIRST), rng.choice(_LAST))

    # Designate homonym entities: groups of DISTINCT entities that all surface
    # under the *same* abbreviated token ("X. Last"), forcing name-only ER to
    # wrongly merge them.  We explicitly build collision groups so that the
    # test assertion (same surface name -> multiple true entities) is guaranteed
    # regardless of which canonical names the RNG drew.
    n_homonym_entities = max(2, round(n_entities * homonym_rate))

    # Sample which entities become homonyms (at least 2 so a collision exists)
    all_eids = list(range(n_entities))
    rng.shuffle(all_eids)
    homonym_pool = all_eids[:n_homonym_entities]

    # Group the homonym pool into collision groups of size 2-3.
    # All members of a group share one forced surface token "X. Last".
    # We pick a "representative" entity per group and use that entity's
    # initial + last name as the shared token for all group members.
    homonym_group_token: dict[int, str] = {}   # eid -> forced surface token
    group_size = 2
    for i in range(0, len(homonym_pool), group_size):
        group = homonym_pool[i : i + group_size]
        if len(group) < 2:
            # Odd entity out: pair it into the previous group's token
            prev_start = max(0, i - group_size)
            rep = homonym_pool[prev_start]
        else:
            rep = group[0]
        first_rep, last_rep = entity_canonical[rep]
        token = f"{first_rep[0]}. {last_rep}"
        for eid in group:
            homonym_group_token[eid] = token

    homonym_entities: set[int] = set(homonym_pool)

    # Designate synonym entities: their records carry name variants
    synonym_entities: set[int] = set(
        rng.sample(range(n_entities), max(1, round(n_entities * synonym_rate)))
    )

    def _surface_name(eid: int, record_variant_idx: int) -> str:
        """Return the surface name for the idx-th record of entity eid."""
        first, last = entity_canonical[eid]
        initial = first[0]

        if eid in homonym_entities:
            # Use the pre-assigned shared token so multiple DISTINCT entities
            # surface under the same string, guaranteeing a name collision.
            return homonym_group_token[eid]

        if eid in synonym_entities:
            # Cycle through name variants across records of the same entity
            variants = [
                f"{first} {last}",
                f"{initial}. {last}",
                f"{last}, {first}",
                f"{last}, {initial}.",
            ]
            return variants[record_variant_idx % len(variants)]

        return f"{first} {last}"

    # ------------------------------------------------------------------
    # 3. Materialize author records
    # ------------------------------------------------------------------
    # entity_records[eid] = list of author row_ids belonging to that entity
    entity_records: dict[int, list[int]] = {}
    author_rows: list[dict[str, Any]] = []
    row_id_counter = 0

    for eid in range(n_entities):
        n_records = rng.randint(records_per_entity_min, records_per_entity_max)
        rids = []
        for variant_idx in range(n_records):
            rid = row_id_counter
            row_id_counter += 1
            name = _surface_name(eid, variant_idx)
            author_rows.append(
                {"__row_id__": rid, "name": name, "author_truth": eid}
            )
            rids.append(rid)
        entity_records[eid] = rids

    # ------------------------------------------------------------------
    # 4. Materialize papers and authorship edges
    # ------------------------------------------------------------------
    paper_rows: list[dict[str, Any]] = []
    authorship_rows: list[dict[str, Any]] = []
    paper_row_id_counter = 0

    for eid in range(n_entities):
        collab_eids = collaborators[eid]
        for author_rid in entity_records[eid]:
            for paper_idx in range(papers_per_author):
                # Create a paper
                paper_rid = paper_row_id_counter
                paper_row_id_counter += 1
                paper_id = f"p-e{eid}-a{author_rid}-{paper_idx}"
                paper_rows.append({"__row_id__": paper_rid, "paper_id": paper_id})

                # The focal author is on this paper
                authorship_rows.append(
                    {"paper_row_id": paper_rid, "author_row_id": author_rid}
                )

                # Add co-authors drawn from collaborator entities
                # Pick collaborator records (one record per collaborator entity)
                n_co = min(coauthors_per_paper, len(collab_eids))
                chosen_collabs = rng.sample(collab_eids, n_co)
                for co_eid in chosen_collabs:
                    co_rid = rng.choice(entity_records[co_eid])
                    authorship_rows.append(
                        {"paper_row_id": paper_rid, "author_row_id": co_rid}
                    )

    # ------------------------------------------------------------------
    # 5. Build truth dict
    # ------------------------------------------------------------------
    truth: dict[int, int] = {
        row["__row_id__"]: row["author_truth"] for row in author_rows
    }

    # ------------------------------------------------------------------
    # 6. Assemble DataFrames
    # ------------------------------------------------------------------
    authors_df = pl.DataFrame(
        author_rows,
        schema={"__row_id__": pl.Int64, "name": pl.String, "author_truth": pl.Int64},
    )
    papers_df = pl.DataFrame(
        paper_rows,
        schema={"__row_id__": pl.Int64, "paper_id": pl.String},
    )
    authorship_df = pl.DataFrame(
        authorship_rows,
        schema={"paper_row_id": pl.Int64, "author_row_id": pl.Int64},
    )

    return RelationalFixture(
        authors=authors_df,
        papers=papers_df,
        authorship=authorship_df,
        truth=truth,
    )
