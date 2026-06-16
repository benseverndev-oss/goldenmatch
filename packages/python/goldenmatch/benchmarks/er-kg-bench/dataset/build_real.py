"""Build records.csv from REAL, authoritative, citable sources.

Replaces the hand-authored synthetic seeds with surface-form variants pulled from
public reference data, so nothing is invented and the ground truth is external:

* Wikidata (`wbgetentities`): per-QID labels + `altLabel` aliases + multilingual
  labels. The **QID is the ground-truth entity_id** -- two mentions match iff they
  share a QID. Cross-lingual exonyms (Munich / Munchen / Monaco di Baviera) are
  real `label`s in different languages; abbreviations (IBM / International Business
  Machines) are real `altLabel`s; same-name collisions are *distinct QIDs* that
  share a surface form. `description` becomes the (real) context field.
* RxNorm (RxNav REST): an ingredient (`tty=IN`) and its brand names (`tty=BN`) --
  authoritative brand<->generic synonyms (Coumadin / warfarin). The **RxCUI is the
  ground-truth entity_id**.

`typo` rows are a controlled synthetic corruption layer over real names -- typos
are synthetic by nature and labelled as such.

stdlib only (urllib); run locally (HTTP, no heavy ER). Reads dataset/sources.jsonl,
writes dataset/records.csv with a `source` provenance column.

    python dataset/build_real.py            # build records.csv
    python dataset/build_real.py --dry-run  # print what each source returns, no write
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SOURCES = HERE / "sources.jsonl"
RECORDS = HERE / "records.csv"

# Cap surface forms per entity so one noisy alias list (ibuprofen -> 19 brands)
# can't dominate the board.
MAX_MENTIONS = 6
# Precision-critical negatives test via a surface form shared across DISTINCT
# entities, so a single mention per entity is legitimate (the collision is
# cross-entity). Positive classes need >= 2 forms to test recall.
# (cross_document_exact is the same string repeated for ONE entity -> handled as
# a separate synthetic-repeat layer, not here.)
_MIN1_CLASSES = {"same_name_collision", "temporal_version"}
# Drop alias junk that isn't a real name variant: URLs/domains, and short
# digit-bearing OCR noise ("18M"), while keeping real abbreviations (IBM, U.N.).
_DOMAIN_RE = re.compile(r"^\S+\.\S{2,}$")


def _is_namelike(s: str) -> bool:
    s = s.strip()
    if len(s) < 2 or "://" in s or _DOMAIN_RE.match(s):
        return False
    if len(s) <= 4 and any(c.isdigit() for c in s):
        return False
    return True

_UA = "ER-KG-Bench/1.0 (https://github.com/benseverndev-oss/goldenmatch; benchmark data build)"
_WIKIDATA = "https://www.wikidata.org/w/api.php"
_RXNAV = "https://rxnav.nlm.nih.gov/REST"

FIELDNAMES = ["record_id", "mention", "entity_type", "context", "entity_id", "failure_class", "source"]


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https endpoints
        return json.loads(resp.read().decode("utf-8"))


def _clean(items: list[str]) -> list[str]:
    """Dedupe (case-insensitive, order-preserving) + drop non-namelike junk."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        k = x.strip()
        if k and k.lower() not in seen and _is_namelike(k):
            seen.add(k.lower())
            out.append(k)
    return out


def wikidata_mentions(spec: dict) -> tuple[list[str], str, str]:
    """Return (mentions, entity_id, context) for one Wikidata QID."""
    qid = spec["qid"]
    langs = spec.get("langs", ["en"])
    q = urllib.parse.urlencode(
        {
            "action": "wbgetentities",
            "ids": qid,
            "props": "labels|aliases|descriptions",
            "languages": "|".join(dict.fromkeys([*langs, "en"])),
            "format": "json",
        }
    )
    data = _get_json(f"{_WIKIDATA}?{q}")
    ent = data["entities"][qid]
    mentions: list[str] = []
    for lang in langs:
        lab = ent.get("labels", {}).get(lang, {}).get("value")
        if lab:
            mentions.append(lab)
    if spec.get("aliases"):
        for al in ent.get("aliases", {}).get("en", []):
            mentions.append(al["value"])
    desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
    return _clean(mentions), qid, desc


def rxnorm_mentions(spec: dict) -> tuple[list[str], str, str]:
    """Return (mentions, entity_id, context) for one RxNorm ingredient + its brands."""
    name = spec["ingredient"]
    rx = _get_json(f"{_RXNAV}/rxcui.json?{urllib.parse.urlencode({'name': name, 'search': 1})}")
    ids = (rx.get("idGroup", {}) or {}).get("rxnormId", []) or []
    if not ids:
        return [], "", ""
    rxcui = ids[0]
    rel = _get_json(f"{_RXNAV}/rxcui/{rxcui}/related.json?{urllib.parse.urlencode({'tty': 'IN BN'})}")
    mentions = [name]
    for group in (rel.get("relatedGroup", {}) or {}).get("conceptGroup", []) or []:
        for c in group.get("conceptProperties", []) or []:
            if c.get("name"):
                mentions.append(c["name"])
    return _clean(mentions), f"rxcui:{rxcui}", f"drug ingredient and brand names ({name})"


def wikidata_label(qid: str) -> tuple[str, str]:
    """Just the English label + description for a QID (real base for synth layers)."""
    q = urllib.parse.urlencode(
        {"action": "wbgetentities", "ids": qid, "props": "labels|descriptions", "languages": "en", "format": "json"}
    )
    ent = _get_json(f"{_WIKIDATA}?{q}")["entities"][qid]
    return (
        ent.get("labels", {}).get("en", {}).get("value", ""),
        ent.get("descriptions", {}).get("en", {}).get("value", ""),
    )


def _typos(name: str) -> list[str]:
    """Deterministic single-edit typos over a REAL name (typos are synthetic)."""
    out = [name]
    letters = [i for i, c in enumerate(name) if c.isalpha()]
    if len(letters) >= 4:
        i = letters[len(letters) // 2]
        out.append(name[:i] + name[i + 1 :])  # drop a middle letter
    if len(letters) >= 5:
        j = letters[len(letters) // 3]
        out.append(name[:j] + name[j] + name[j:])  # double a letter
    return _dedupe_preserve(out)


def _suffixes(name: str) -> list[str]:
    """Real company name + common legal-suffix variants (suffix forms synthetic)."""
    base = re.sub(r"\b(Inc|Incorporated|Corp|Corporation|Ltd|Limited|Co|LLC)\.?$", "", name).strip()
    return _dedupe_preserve([base, f"{base} Inc", f"{base} Corporation", f"{base} Ltd", f"{base} Co"])


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def synth_mentions(spec: dict) -> tuple[list[str], str, str]:
    """typo / suffix / repeat / temporal layers over real base names."""
    fc = spec["failure_class"]
    if fc == "temporal_version":
        # Distinct real editions with near-identical surfaces (real events; the
        # surface patterns are how people actually write them).
        base, year = spec["base"], str(spec["year"])
        eid = f"{base.lower().replace(' ', '-')}-{year}"
        mentions = [f"{base} {year}", f"{year} {base}", f"{base} ({year})"]
        return _dedupe_preserve(mentions), eid, f"{base}, {year} edition"
    qid = spec["qid"]
    label, desc = wikidata_label(qid)
    if not label:
        return [], qid, ""
    if fc == "typo":
        return _typos(label), qid, desc
    if fc == "org_suffix":
        return _suffixes(label), qid, desc
    if fc == "cross_document_exact":
        return [label] * int(spec.get("n", 3)), qid, desc
    return [label], qid, desc


def build_rows(dry_run: bool = False) -> list[dict]:
    rows: list[dict] = []
    rid = 0
    with SOURCES.open(encoding="utf-8") as fh:
        specs = [json.loads(line) for line in fh if line.strip()]
    for spec in specs:
        src = spec["source"]
        try:
            if src == "wikidata":
                mentions, eid, context = wikidata_mentions(spec)
            elif src == "rxnorm":
                mentions, eid, context = rxnorm_mentions(spec)
            elif src == "synth":
                mentions, eid, context = synth_mentions(spec)
            else:
                print(f"  [skip] unknown source {src!r}")
                continue
        except Exception as exc:  # noqa: BLE001 - one flaky source must not sink the build
            print(f"  [skip] {src} {spec.get('qid') or spec.get('ingredient')}: {type(exc).__name__}: {exc}")
            continue
        mentions = mentions[:MAX_MENTIONS]
        min_needed = 1 if spec["failure_class"] in _MIN1_CLASSES else 2
        if len(mentions) < min_needed:
            print(f"  [warn] {eid} ({spec['failure_class']}): only {len(mentions)} mention(s) {mentions} -- skipping")
            continue
        print(f"  {eid:>16} [{spec['failure_class']:<18}] {len(mentions)} mentions: {mentions}")
        for m in mentions:
            rows.append(
                {
                    "record_id": rid,
                    "mention": m,
                    "entity_type": spec["type"],
                    "context": context,
                    "entity_id": eid,
                    "failure_class": spec["failure_class"],
                    "source": src,
                }
            )
            rid += 1
        time.sleep(0.2)  # be polite to the public APIs
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print what sources return; do not write")
    args = ap.parse_args()
    rows = build_rows(dry_run=args.dry_run)
    n_entities = len({r["entity_id"] for r in rows})
    n_classes = len({r["failure_class"] for r in rows})
    print(f"\n{len(rows)} records / {n_entities} entities / {n_classes} failure classes")
    if args.dry_run:
        print("(dry-run: records.csv not written)")
        return
    with RECORDS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {RECORDS}")


if __name__ == "__main__":
    main()
