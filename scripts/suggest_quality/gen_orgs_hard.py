#!/usr/bin/env python3
"""Generate `orgs_hard`: a curated, deliberately hard B2B organisation corpus.

## Why this exists

The quality panel had seven loadable datasets and six were the same shape --
a person with a name plus an address or an email:

    anchor_sparse_zip    person   npi, email, phone, first/last, zip
    anchor_shared_email  person   first, last, email, phone
    anchor_person_match  person   first_name, last_name, email, zip
    synthetic            person   first_name, last_name, email, zip
    ncvr_synthetic       person   ncid, first/last/middle, address, city, state
    historical_50k       person   full_name, surname, dob, birth_place, postcode
    dblp_acm             biblio   id, title, authors, venue, year   <- the only one

That mattered: every red on the benchmarks lane in one week (#2250, #2461,
#2659) was on a NON-person path -- domain extraction over `__brand__` /
`__model__` / `__title_key__`, and the DBLP-ACM adapter. Person data never
tripped any of them. A panel of seven person shapes structurally cannot catch
that class.

This replaces the borrowed dataset with a curated one. Organisations rather
than bibliography, because that shape is free-text-dominant (fills the gap)
AND is the product's own domain -- "the same company spelled four different
ways" is the README's opening line.

## The trap this is designed against

A synthetic corpus whose ground truth its own authors define will be easy in
exactly the ways its own matcher is good at, unless it is built adversarially.
So hardness here is NOT "add more typos". Every hard case is a named,
realistic failure mode, stamped per-record in `hardness` so a regression is
diagnosable rather than merely visible:

  legal_form   Acme Corporation / Acme Corp. / ACME Inc
               A suffix carries no identity but dominates token overlap.
  acronym      International Business Machines / I.B.M. / IBM
               Defeats character-level similarity outright.
  word_order   Smith & Sons Ltd / Sons, Smith and / Smith and Sons
               Defeats character-level, survives token-set.
  abbrev       Northern Manufacturing / Nthn Mfg
               Defeats both unless a domain transform runs.
  missing      one side has no website / no phone / no postcode
               Punishes configs that lean on one discriminative field.
  branch       HARD NEGATIVE. Same name, different site (Leeds vs Bristol).
               Distinct entities. Over-merging these is the classic org-ER
               failure and no amount of name similarity should join them.
  parent_sub   HARD NEGATIVE. Acme Holdings vs Acme Manufacturing, shared
               postcode. Related, NOT the same legal entity.
  common_token HARD NEGATIVE. Unrelated firms sharing "Group"/"Services"/
               "Solutions", the tokens that make text blocking degenerate.

The two negative classes are the point. A corpus of only positives measures
recall and flatters any config that merges aggressively; `branch` and
`parent_sub` pairs are built to score JUST BELOW true duplicates, which is
where a threshold actually gets tested. Same reasoning as
`anchors.gen_household_hardneg`.

## Determinism

Seeded, no clock, no network. The CSVs are COMMITTED, and
`test_orgs_hard_corpus.py` asserts this generator still reproduces them
byte-for-byte -- so a generator edit cannot silently move the data a blessed
baseline is measured against.

Usage:
    python -m scripts.suggest_quality.gen_orgs_hard --write
"""
from __future__ import annotations

import argparse
import csv
import random
from itertools import combinations
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent / "corpora" / "orgs_hard"
RECORDS_CSV = CORPUS_DIR / "records.csv"
TRUTH_CSV = CORPUS_DIR / "truth.csv"

FIELDS = ["record_id", "org_name", "street", "city", "postcode",
          "website", "phone", "industry", "hardness"]

_STEMS = [
    "Acme", "Northern", "Blackwell", "Harrow", "Pinnacle", "Sterling",
    "Cavendish", "Redgate", "Thornton", "Ashcroft", "Baxter", "Colville",
    "Draycott", "Elmhurst", "Fairbourne", "Garrick", "Hollingsworth",
    "Ingleby", "Jarrow", "Kirkland", "Langmere", "Mowbray", "Netherby",
    "Oakhampton", "Penhale", "Quarrenden", "Rushmoor", "Stanbridge",
]
_TAILS = ["Manufacturing", "Logistics", "Chemicals", "Textiles", "Engineering",
          "Foods", "Packaging", "Instruments", "Motors", "Plastics"]
_GENERIC = ["Group", "Services", "Solutions", "Holdings", "Partners", "Systems"]
_FORMS = ["Limited", "Ltd", "Ltd.", "PLC", "Inc", "Incorporated", "Corp",
          "Corporation", "LLP", "& Co"]
_CITIES = ["Leeds", "Bristol", "Sheffield", "Glasgow", "Cardiff", "Norwich",
           "Derby", "Plymouth", "Dundee", "Swansea"]
_STREETS = ["Mill Lane", "Station Road", "High Street", "Kings Way",
            "Victoria Road", "Queens Park", "Foundry Road", "Canal Street"]
_INDUSTRY = ["manufacturing", "logistics", "chemicals", "wholesale",
             "engineering", "food-production"]

_ABBREV = {
    "Manufacturing": "Mfg", "Logistics": "Log.", "Engineering": "Eng",
    "Chemicals": "Chem", "Instruments": "Instr", "Packaging": "Pkg",
    "Northern": "Nthn", "Limited": "Ltd", "Incorporated": "Inc",
}


def _postcode(rng: random.Random) -> str:
    return f"{rng.choice('ABDEGLMNS')}{rng.randrange(1, 99)} {rng.randrange(1, 9)}{rng.choice('ABDEFGHJ')}{rng.choice('ABDEFGHJ')}"


def _phone(rng: random.Random) -> str:
    return f"+44 {rng.randrange(1100, 1999)} {rng.randrange(100000, 999999)}"


def _site(name: str) -> str:
    slug = "".join(c for c in name.lower() if c.isalnum())[:18]
    return f"www.{slug}.co.uk"


def _acronym(words: list[str]) -> str:
    return "".join(w[0] for w in words if w[:1].isupper())


def build(seed: int = 20260818, n_entities: int = 180):
    """Return (rows, truth_pairs). Row order is shuffled; truth is by index."""
    rng = random.Random(seed)
    tagged: list[tuple[dict, int]] = []
    eid = 0

    def add(rec: dict, entity: int) -> None:
        tagged.append((rec, entity))

    for _ in range(n_entities):
        stem, tail = rng.choice(_STEMS), rng.choice(_TAILS)
        base_words = [stem, tail]
        form = rng.choice(_FORMS)
        city = rng.choice(_CITIES)
        street = f"{rng.randrange(1, 200)} {rng.choice(_STREETS)}"
        pc = _postcode(rng)
        ind = rng.choice(_INDUSTRY)
        canonical = f"{stem} {tail} {form}"
        eid += 1
        me = eid

        add({"record_id": f"r{len(tagged):04d}", "org_name": canonical,
             "street": street, "city": city, "postcode": pc,
             "website": _site(stem + tail), "phone": _phone(rng),
             "industry": ind, "hardness": "canonical"}, me)

        # --- positives: the same entity, written differently -------------
        variants: list[tuple[str, dict]] = []

        alt_form = rng.choice([f for f in _FORMS if f != form])
        variants.append(("legal_form", {"org_name": f"{stem} {tail} {alt_form}"}))

        if rng.random() < 0.45:
            variants.append(("acronym", {
                "org_name": f"{_acronym(base_words)} {rng.choice(_FORMS)}"}))
        if rng.random() < 0.45:
            variants.append(("word_order", {
                "org_name": f"{tail}, {stem} {form}"}))
        if rng.random() < 0.40:
            ab = " ".join(_ABBREV.get(w, w) for w in base_words)
            variants.append(("abbrev", {"org_name": f"{ab} {_ABBREV.get(form, form)}"}))
        if rng.random() < 0.40:
            # Field-sparse: the discriminative columns are simply absent.
            variants.append(("missing", {"org_name": f"{stem} {tail}",
                                         "website": "", "phone": "",
                                         "postcode": ""}))

        for label, over in variants:
            rec = {"record_id": f"r{len(tagged):04d}", "org_name": canonical,
                   "street": street, "city": city, "postcode": pc,
                   "website": _site(stem + tail), "phone": _phone(rng),
                   "industry": ind, "hardness": label}
            rec.update(over)
            add(rec, me)

        # --- hard negatives: DISTINCT entities that look alike -----------
        if rng.random() < 0.35:
            # Same trading name, different site. Not the same legal entity.
            eid += 1
            other_city = rng.choice([c for c in _CITIES if c != city])
            add({"record_id": f"r{len(tagged):04d}",
                 "org_name": f"{stem} {tail} {form}",
                 "street": f"{rng.randrange(1, 200)} {rng.choice(_STREETS)}",
                 "city": other_city, "postcode": _postcode(rng),
                 "website": _site(stem + tail), "phone": _phone(rng),
                 "industry": ind, "hardness": "branch"}, eid)

        if rng.random() < 0.30:
            # Parent vs subsidiary: shared stem AND shared postcode.
            eid += 1
            add({"record_id": f"r{len(tagged):04d}",
                 "org_name": f"{stem} {rng.choice(_GENERIC)} {form}",
                 "street": street, "city": city, "postcode": pc,
                 "website": _site(stem + "group"), "phone": _phone(rng),
                 "industry": ind, "hardness": "parent_sub"}, eid)

        if rng.random() < 0.25:
            # Unrelated firm sharing only a high-frequency token.
            eid += 1
            other = rng.choice([s for s in _STEMS if s != stem])
            add({"record_id": f"r{len(tagged):04d}",
                 "org_name": f"{other} {rng.choice(_GENERIC)} {form}",
                 "street": f"{rng.randrange(1, 200)} {rng.choice(_STREETS)}",
                 "city": rng.choice(_CITIES), "postcode": _postcode(rng),
                 "website": _site(other), "phone": _phone(rng),
                 "industry": rng.choice(_INDUSTRY),
                 "hardness": "common_token"}, eid)

    rng.shuffle(tagged)
    rows = []
    for pos, (rec, _e) in enumerate(tagged):
        rec = dict(rec)
        rec["record_id"] = f"r{pos:04d}"
        rows.append(rec)

    by_entity: dict[int, list[int]] = {}
    for pos, (_rec, e) in enumerate(tagged):
        by_entity.setdefault(e, []).append(pos)
    truth = set()
    for positions in by_entity.values():
        for a, b in combinations(sorted(positions), 2):
            truth.add((a, b))
    return rows, truth


def write(rows, truth) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with RECORDS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with TRUTH_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["row_a", "row_b"])
        for a, b in sorted(truth):
            w.writerow([a, b])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    rows, truth = build()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["hardness"]] = counts.get(r["hardness"], 0) + 1
    print(f"rows={len(rows)}  truth_pairs={len(truth)}")
    for k in sorted(counts):
        print(f"  {k:14s} {counts[k]:4d}")
    if args.write:
        write(rows, truth)
        print(f"wrote {RECORDS_CSV}\nwrote {TRUTH_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
