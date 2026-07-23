"""One-off Wikidata SPARQL puller that WRITES the committed company-capability fixture
(`erkgbench/qa_e2e/fixtures/wikidata_companies_v1.json`).

Run BY HAND, never in CI and never imported by the bench. The bench reads only the
committed fixture -- this is the reproducible dataset generator. Wikidata is CC0, so
the pulled fixture is redistributable.

    python scripts/pull_wikidata_capability_fixture.py \
        --out erkgbench/qa_e2e/fixtures/wikidata_companies_v1.json --min-set-size 2

Deterministic: entities are sorted by qid and facts by anchor qid (with sorted member
lists), so the committed file is stable across pulls. Prints a size-bucket histogram of
the anchor fan-out so the operator can confirm the 11-20 and 21+ buckets are populated
(the whole point of the bench -- that is where the RAG passage-window floor collapses)."""
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
    "ben@goldentruth.io) capability-benchmark-fixture-puller"
)

# Pass 1: business (P31/P279* Q4830453) -> subsidiary (P355) edges. Kept lean (no
# labels/aliases) so the cross-product stays small; labels/aliases come in pass 2.
EDGE_QUERY = """SELECT ?company ?sub WHERE {
  ?company wdt:P31/wdt:P279* wd:Q4830453 .
  ?company wdt:P355 ?sub .
}
LIMIT %d"""

# Pass 2 (batched via VALUES): English label + alt-labels for every qid we kept.
LABEL_QUERY_TMPL = """SELECT ?item ?itemLabel ?alias WHERE {
  VALUES ?item { %s }
  OPTIONAL { ?item skos:altLabel ?alias FILTER(LANG(?alias) = "en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}"""

_BUCKETS = ((2, 4), (5, 10), (11, 20))


def _size_bucket(n: int) -> str:
    for lo, hi in _BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi}"
    return f">{_BUCKETS[-1][1]}"


def _run_sparql(query: str, *, retries: int = 4, timeout: int = 60) -> dict:
    """POST a SPARQL query to WDQS, JSON results, descriptive User-Agent, backoff."""
    data = urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(
        WDQS_ENDPOINT, data=data,
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
    )
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - network puller, retry on anything
            last_exc = exc
            wait = 2 ** attempt
            sys.stderr.write(f"WDQS attempt {attempt + 1} failed ({exc}); retry in {wait}s\n")
            time.sleep(wait)
    raise RuntimeError(f"WDQS query failed after {retries} attempts: {last_exc}")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _fetch_edges(limit: int) -> dict[str, set[str]]:
    """anchor qid -> set of subsidiary qids."""
    rows = _run_sparql(EDGE_QUERY % limit)["results"]["bindings"]
    members: dict[str, set[str]] = {}
    for r in rows:
        anchor = _qid(r["company"]["value"])
        sub = _qid(r["sub"]["value"])
        if anchor != sub:
            members.setdefault(anchor, set()).add(sub)
    return members


def _fetch_labels(qids: list[str], *, batch: int = 300) -> dict[str, dict]:
    """qid -> {"label": str, "aliases": set[str]} for every qid (batched VALUES)."""
    out: dict[str, dict] = {}
    ordered = sorted(qids)
    for i in range(0, len(ordered), batch):
        chunk = ordered[i:i + batch]
        values = " ".join(f"wd:{q}" for q in chunk)
        rows = _run_sparql(LABEL_QUERY_TMPL % values)["results"]["bindings"]
        for r in rows:
            q = _qid(r["item"]["value"])
            rec = out.setdefault(q, {"label": "", "aliases": set()})
            label = r.get("itemLabel", {}).get("value", "")
            # The label service returns the bare Q-id as label when none exists; skip.
            if label and label != q:
                rec["label"] = label
            alias = r.get("alias", {}).get("value")
            if alias:
                rec["aliases"].add(alias)
        sys.stderr.write(f"labels: {min(i + batch, len(ordered))}/{len(ordered)}\n")
    return out


def build_fixture(*, limit: int, min_set_size: int) -> dict:
    members = _fetch_edges(limit)
    # Keep only anchors with enough members up front (bounds the label pass).
    kept = {a: subs for a, subs in members.items() if len(subs) >= min_set_size}
    all_qids = set(kept)
    for subs in kept.values():
        all_qids |= subs
    labels = _fetch_labels(sorted(all_qids))

    def _canon(q: str) -> str:
        return labels.get(q, {}).get("label") or q

    # Entities: every anchor + member that has a real (non-qid) label. A member
    # without a label is dropped from both the entity list and the member sets so
    # gold never references an unnamed node.
    named = {q for q in all_qids if labels.get(q, {}).get("label")}
    entities = []
    for q in sorted(named):
        canon = _canon(q)
        aliases = sorted(a for a in labels.get(q, {}).get("aliases", set()) if a != canon)
        entities.append({"qid": q, "canonical": canon, "aliases": aliases})

    facts = []
    for anchor in sorted(kept):
        if anchor not in named:
            continue
        member_qids = sorted(m for m in kept[anchor] if m in named)
        if len(member_qids) >= min_set_size:
            facts.append({
                "anchor_qid": anchor,
                "relation": "has_subsidiary",
                "member_qids": member_qids,
            })

    sparql_sha = hashlib.sha256(
        (EDGE_QUERY + LABEL_QUERY_TMPL).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "meta": {
            "source": "wikidata",
            "pulled": time.strftime("%Y-%m-%d"),
            "sparql_sha": sparql_sha,
            "domain": "companies",
        },
        "entities": entities,
        "facts": facts,
    }


def _print_histogram(fixture: dict) -> None:
    hist: dict[str, int] = {}
    for fact in fixture["facts"]:
        hist[_size_bucket(len(fact["member_qids"]))] = (
            hist.get(_size_bucket(len(fact["member_qids"])), 0) + 1
        )
    order = [f"{lo}-{hi}" for lo, hi in _BUCKETS] + [f">{_BUCKETS[-1][1]}"]
    sys.stderr.write("\nanchor fan-out size-bucket histogram:\n")
    for b in order:
        sys.stderr.write(f"  {b:>6}: {hist.get(b, 0)}\n")
    sys.stderr.write(
        f"  entities={len(fixture['entities'])} facts={len(fixture['facts'])}\n"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pull the Wikidata company capability fixture")
    p.add_argument("--out", required=True, help="output JSON path (the committed fixture)")
    p.add_argument("--limit", type=int, default=20000, help="P355 edge query LIMIT")
    p.add_argument("--min-set-size", type=int, default=2,
                   help="keep only anchors with at least this many named members")
    args = p.parse_args(argv)

    fixture = build_fixture(limit=args.limit, min_set_size=args.min_set_size)
    _print_histogram(fixture)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    sys.stderr.write(f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
