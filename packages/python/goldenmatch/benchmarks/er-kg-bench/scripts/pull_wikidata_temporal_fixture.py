#!/usr/bin/env python3
"""One-off Wikidata SPARQL puller that WRITES the committed CEO-history temporal fixture
(`erkgbench/qa_e2e/fixtures/wikidata_ceo_temporal_v1.json`) for the Phase 1 temporal as-of
capability slice.

Run BY HAND, never in CI and never imported by the bench. The bench reads only the committed
JSON. Pulls companies (P31/P279* Q4830453) with >=2 dated CEO statements (P169 + P580 start),
and for each keeps the LAST TWO consecutive CEOs as a binary succession: a_qid (earlier,
valid before the succession) -> b_qid (later, from tc = b's start year). start_a = a's start
year. Deterministic (sorted by qid) so the committed fixture is stable.

Usage:
    python scripts/pull_wikidata_temporal_fixture.py \
        --out erkgbench/qa_e2e/fixtures/wikidata_ceo_temporal_v1.json --limit 40000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "goldenmatch-er-kg-bench/1.0 (https://github.com/benseverndev-oss/goldenmatch; "
    "ben@goldentruth.io) temporal-capability-fixture-puller"
)

CEO_QUERY = """SELECT ?company ?companyLabel ?ceo ?ceoLabel ?start WHERE {
  ?company wdt:P31/wdt:P279* wd:Q4830453 .
  ?company p:P169 ?st . ?st ps:P169 ?ceo .
  ?st pq:P580 ?start .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
LIMIT %d"""


def _run_sparql(query: str, *, retries: int = 4, timeout: int = 60) -> dict:
    data = urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(
        WDQS_ENDPOINT, data=data,
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
    )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - network puller, retry on anything
            last = exc
            wait = 2 ** attempt
            sys.stderr.write(f"WDQS attempt {attempt + 1} failed ({exc}); retry in {wait}s\n")
            time.sleep(wait)
    raise RuntimeError(f"WDQS query failed after {retries} attempts: {last}")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _year(iso: str) -> int | None:
    # Wikidata dates are ISO like "2015-10-02T00:00:00Z" (or "+2015-...").
    s = iso.lstrip("+")
    try:
        return int(s[:4])
    except ValueError:
        return None


def build_fixture(*, limit: int) -> dict:
    rows = _run_sparql(CEO_QUERY % limit)["results"]["bindings"]
    # company qid -> {label, ceos: {ceo_qid: (label, start_year)}}
    companies: dict[str, dict] = {}
    for r in rows:
        cq = _qid(r["company"]["value"])
        ceoq = _qid(r["ceo"]["value"])
        y = _year(r["start"]["value"])
        if y is None or cq == ceoq:
            continue
        c = companies.setdefault(cq, {"label": "", "ceos": {}})
        lbl = r.get("companyLabel", {}).get("value", "")
        if lbl and lbl != cq:
            c["label"] = lbl
        ceo_lbl = r.get("ceoLabel", {}).get("value", "")
        # keep the EARLIEST start seen per ceo (a ceo can have multiple P580 across roles)
        prev = c["ceos"].get(ceoq)
        if prev is None or y < prev[1]:
            c["ceos"][ceoq] = (ceo_lbl if ceo_lbl and ceo_lbl != ceoq else ceoq, y)

    entities: dict[str, str] = {}
    facts: list[dict] = []
    for cq in sorted(companies):
        c = companies[cq]
        if not c["label"]:
            continue
        # distinct CEOs by start year, ascending; need >=2 with DIFFERENT start years.
        ceos = sorted(c["ceos"].items(), key=lambda kv: (kv[1][1], kv[0]))
        if len(ceos) < 2:
            continue
        (a_qid, (a_lbl, a_year)), (b_qid, (b_lbl, b_year)) = ceos[-2], ceos[-1]
        if b_year <= a_year:  # need a real succession (distinct years) to test as-of
            continue
        entities[cq] = c["label"]
        entities[a_qid] = a_lbl
        entities[b_qid] = b_lbl
        facts.append({"anchor_qid": cq, "relation": "chief_executive_officer",
                      "a_qid": a_qid, "b_qid": b_qid, "tc": b_year, "start_a": a_year})

    sparql_sha = hashlib.sha256(CEO_QUERY.encode("utf-8")).hexdigest()[:16]
    return {
        "meta": {"source": "wikidata", "pulled": time.strftime("%Y-%m-%d"),
                 "sparql_sha": sparql_sha, "domain": "ceo_temporal",
                 "current_year": int(time.strftime("%Y"))},
        "entities": [{"qid": q, "canonical": entities[q]} for q in sorted(entities)],
        "temporal_facts": facts,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=40000)
    args = p.parse_args(argv)
    fx = build_fixture(limit=args.limit)
    Path(args.out).write_text(json.dumps(fx, indent=2), encoding="utf-8")
    n_facts = len(fx["temporal_facts"])
    n_ents = len(fx["entities"])
    sys.stderr.write(f"wrote {args.out}: {n_facts} succession facts, {n_ents} entities "
                     f"(current_year={fx['meta']['current_year']})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
