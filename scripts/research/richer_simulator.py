"""Richer ER simulator — close step 5's sim-vs-real decision-boundary gap.

Step 5 (`RESULTS-pretrained-transfer.md`): a frozen pretrained encoder fixed
most of the sim-to-real transfer failure (real F1 0.03->0.42), but the head now
OVER-MERGES real data (33 clusters vs 60 true). Diagnosis: the toy simulator in
`real_schema_encoder.py` has tiny between-entity diversity (20 first names, 20
surnames, 10 cities) and mild corruption, so DIFFERENT entities look similar in
embedding space during training and the head learns a too-loose join boundary.

This module provides a richer simulator to tighten that boundary, WITHOUT
leaking target-dataset values (so zero-shot stays honest):
  * far larger generic vocabularies (hundreds of names / streets / cities) ->
    higher between-entity diversity -> the head must learn that "same entity"
    means genuinely close;
  * a realistic, multi-mode corruption process (keyboard-adjacent typos,
    transposition, deletion/insertion, phonetic swaps, abbreviation, nicknames,
    field swaps, missing fields) with VARIED severity (some clean, some heavy) ->
    a within-entity distance distribution that spans the real range;
  * a Febrl-like 9-field schema so record token-richness matches real data.

Used by `pretrained_transfer_er.py --simulator rich`. Microclustering cluster
sizes reuse `amortized_partition_er._sample_cluster_sizes`.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from amortized_partition_er import _sample_cluster_sizes  # noqa: E402

# --- generic vocabularies (real-ish tokens MiniLM embeds well; no target leakage) ---
FIRST = ("james john robert michael william david richard joseph thomas charles "
         "christopher daniel matthew anthony donald mark paul steven andrew kenneth "
         "mary patricia jennifer linda elizabeth barbara susan jessica sarah karen "
         "nancy lisa margaret betty sandra ashley dorothy kimberly emily donna "
         "george edward brian ronald timothy jason jeffrey ryan jacob gary nicholas "
         "eric jonathan stephen larry justin scott brandon frank benjamin gregory "
         "samuel raymond patrick alexander jack dennis jerry tyler aaron jose henry "
         "amanda melissa deborah stephanie rebecca laura helen sharon cynthia kathleen "
         "amy angela shirley anna brenda pamela nicole emma samantha katherine").split()
LAST = ("smith johnson williams brown jones garcia miller davis rodriguez martinez "
        "hernandez lopez gonzalez wilson anderson thomas taylor moore jackson martin "
        "lee perez thompson white harris sanchez clark ramirez lewis robinson walker "
        "young allen king wright scott torres nguyen hill flores green adams nelson "
        "baker hall rivera campbell mitchell carter roberts gomez phillips evans turner "
        "diaz parker cruz edwards collins reyes stewart morris morales murphy cook "
        "rogers gutierrez ortiz morgan cooper peterson bailey reed kelly howard ramos").split()
STREET = ("oak maple cedar pine elm washington lake hill park main church spring "
          "highland sunset ridge river valley forest meadow franklin lincoln jackson "
          "willow chestnut walnut birch spruce dogwood sycamore aspen juniper poplar "
          "summit grove orchard prairie clinton madison jefferson adams monroe").split()
SUFFIX = ("st ave rd ln dr blvd ct way pl ter").split()
CITY = ("boston denver austin seattle miami chicago portland phoenix dallas atlanta "
        "houston philadelphia columbus charlotte indianapolis nashville detroit memphis "
        "louisville baltimore milwaukee albuquerque tucson fresno sacramento mesa "
        "kansas omaha raleigh oakland tulsa wichita arlington tampa orlando pittsburgh "
        "cincinnati cleveland richmond spokane boise reno fargo dayton akron toledo").split()
STATE = ("al ak az ar ca co ct de fl ga id il in ia ks ky la me md ma mi mn ms mo "
         "mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy").split()
_NICK = {"robert": "bob", "william": "bill", "richard": "rick", "james": "jim",
         "joseph": "joe", "thomas": "tom", "charles": "charlie", "michael": "mike",
         "elizabeth": "liz", "margaret": "peggy", "patricia": "pat", "jennifer": "jen",
         "katherine": "kate", "samantha": "sam", "daniel": "dan", "anthony": "tony",
         "christopher": "chris", "nicholas": "nick", "stephen": "steve", "edward": "ed"}
_KB = {"a": "sqz", "b": "vgn", "c": "xvd", "d": "sfe", "e": "wrd", "f": "dgr",
       "g": "fht", "h": "gjy", "i": "uok", "j": "hkn", "k": "jlm", "l": "ko",
       "m": "nj", "n": "bm", "o": "ipl", "p": "ol", "q": "wa", "r": "et",
       "s": "adw", "t": "ry", "u": "yi", "v": "cb", "w": "qes", "x": "zc",
       "y": "tu", "z": "xa"}


def _typo(s: str, rng: random.Random) -> str:
    if not s:
        return s
    out = list(s)
    p = rng.randrange(len(out))
    op = rng.random()
    ch = out[p].lower()
    if op < 0.30 and ch in _KB:                 # keyboard-adjacent substitution
        out[p] = rng.choice(_KB[ch])
    elif op < 0.50 and p + 1 < len(out):        # transpose
        out[p], out[p + 1] = out[p + 1], out[p]
    elif op < 0.70:                             # delete
        out.pop(p)
    elif op < 0.85:                             # insert
        out.insert(p, rng.choice("abcdefghijklmnopqrstuvwxyz"))
    else:                                       # phonetic-ish
        s2 = "".join(out)
        for a, b in (("ph", "f"), ("ck", "k"), ("ie", "y"), ("ll", "l"), ("nn", "n")):
            if a in s2:
                return s2.replace(a, b, 1)
        out[p] = rng.choice("abcdefghijklmnopqrstuvwxyz")
    return "".join(out)


def _corrupt_field(s: str, n_ops: int, rng: random.Random) -> str:
    for _ in range(n_ops):
        s = _typo(s, rng)
    if rng.random() < 0.12 and len(s) > 2:      # abbreviate
        s = s[:rng.randint(1, max(1, len(s) - 1))]
    return s


def _truth(rng: random.Random) -> list[str]:
    return [
        rng.choice(FIRST),
        rng.choice(LAST),
        str(rng.randint(1, 9999)),                          # street number
        f"{rng.choice(STREET)} {rng.choice(SUFFIX)}",        # street
        rng.choice(CITY),
        rng.choice(STATE),
        f"{rng.randint(1, 99999):05d}",                      # postcode
        f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1940, 2005)}",
        f"{rng.randint(0, 999999999):09d}",                  # ssn-like id
    ]


def _make_record(truth: list[str], rng: random.Random) -> list[str]:
    rec = list(truth)
    # nickname substitution on the first name
    if rng.random() < 0.20 and rec[0] in _NICK:
        rec[0] = _NICK[rec[0]]
    # severity: 30% clean, 50% light, 20% heavy
    r = rng.random()
    if r < 0.30:
        targets = []
    elif r < 0.80:
        targets = rng.sample(range(len(rec)), rng.randint(1, 2))
    else:
        targets = rng.sample(range(len(rec)), rng.randint(3, 5))
    for idx in targets:
        rec[idx] = _corrupt_field(rec[idx], rng.randint(1, 2), rng)
    # realistic data-entry errors
    if rng.random() < 0.06:                      # given/surname swap
        rec[0], rec[1] = rec[1], rec[0]
    if rng.random() < 0.08:                      # a field goes missing
        rec[rng.randrange(len(rec))] = ""
    return rec


def simulate_strings_rich(n_entities: int, rng: random.Random):
    """Richer latent-entity simulator. Returns (records: list[list[str]], labels)."""
    sizes = _sample_cluster_sizes(n_entities, rng)
    records, labels = [], []
    for eid, sz in enumerate(sizes):
        truth = _truth(rng)
        for _ in range(sz):
            records.append(_make_record(truth, rng))
            labels.append(eid)
    idx = list(range(len(records)))
    rng.shuffle(idx)
    return [records[i] for i in idx], [labels[i] for i in idx]
