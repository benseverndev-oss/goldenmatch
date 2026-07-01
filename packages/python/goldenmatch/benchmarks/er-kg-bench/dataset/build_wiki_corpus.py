"""Build the level-2 real-Wikipedia-prose substrate corpus (committed snapshot).

Seed + 1-hop expand over enwiki lead sections; gold = `[[Target|Surface]]` wikilinks whose Target resolves
to a Wikidata QID inside the closed fetched set. Emits `dataset/wiki_corpus.jsonl`:
`{doc_id: <article_QID>, title, revid, text: <plain prose>, gold: [[Target_QID, Surface], ...]}`.

stdlib only (urllib); run OFFLINE once, commit the output. The eval reads the snapshot (no eval-time
network). Reproducible: pins each article's `revid`.

    python dataset/build_wiki_corpus.py            # seeds + 1-hop -> wiki_corpus.jsonl
    python dataset/build_wiki_corpus.py --max 24   # cap total articles
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
SEEDS = HERE / "wiki_seeds.jsonl"
OUT = HERE / "wiki_corpus.jsonl"
_UA = "goldenmatch-erkgbench/1.0 (https://github.com/benseverndev-oss/goldenmatch; substrate eval corpus)"
_WP = "https://en.wikipedia.org/w/api.php"
_WD = "https://www.wikidata.org/w/api.php"

#: Mirrors `erkgbench.qa_e2e.wiki_corpus.parse_wikilinks` (kept in lockstep) -- inlined so this offline
#: builder stays self-contained and never imports the heavy erkgbench package.
_WIKILINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]|]+))?\]\]")


def parse_wikilinks(wikitext: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _WIKILINK.finditer(wikitext):
        target = m.group(1).strip()
        surface = (m.group(2) or m.group(1)).strip()
        if not target or target.startswith("#") or ":" in target:
            continue
        target = target.split("#", 1)[0].strip()
        if target and surface:
            out.append((surface, target))
    return out


_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_REF = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML = re.compile(r"<[^>]+>")
_WIKILINK_SUB = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]|]+))?\]\]")


def _get(url: str, params: dict, _tries: int = 5) -> dict:
    q = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": _UA})
    for attempt in range(_tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed api hosts
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < _tries - 1:
                time.sleep(2 ** attempt)  # 1,2,4,8s backoff on rate-limit
                continue
            raise
    return {}


def fetch_lead(title: str) -> tuple[str, int] | None:
    """(lead-section wikitext, revid) for an enwiki article title, or None if missing."""
    data = _get(_WP, {"action": "query", "prop": "revisions", "rvprop": "content|ids",
                      "rvslots": "main", "rvsection": "0", "titles": title, "formatversion": "2"})
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    rev = pages[0].get("revisions", [{}])[0]
    wt = rev.get("slots", {}).get("main", {}).get("content", "")
    return (wt, int(rev.get("revid", 0))) if wt else None


def resolve_qids(titles: list[str]) -> dict[str, str]:
    """enwiki title -> QID via Wikidata sitelinks, one title at a time (title->QID unambiguous this way;
    `wbgetentities` keys the response by QID, so per-title keeps the mapping clean). Unresolved omitted."""
    out: dict[str, str] = {}
    for t in titles:
        data = _get(_WD, {"action": "wbgetentities", "sites": "enwiki", "titles": t, "props": "info"})
        ents = data.get("entities", {})
        qids = [q for q in ents if q.startswith("Q") and "missing" not in ents[q]]
        if qids:
            out[t] = qids[0]
        time.sleep(0.5)
    return out


def wikitext_to_plain(wt: str) -> str:
    """Best-effort lead wikitext -> natural prose: wikilinks -> anchor text, drop templates/refs/comments/
    html/emphasis, collapse whitespace."""
    wt = _COMMENT.sub("", wt)
    wt = _REF.sub("", wt)
    for _ in range(5):  # nested templates: peel a few layers
        new = _TEMPLATE.sub("", wt)
        if new == wt:
            break
        wt = new
    wt = _WIKILINK_SUB.sub(lambda m: (m.group(2) or m.group(1)), wt)
    wt = _HTML.sub("", wt)
    wt = wt.replace("'''", "").replace("''", "")
    wt = re.sub(r"\n{2,}", "\n", wt)
    wt = re.sub(r"[ \t]{2,}", " ", wt)
    return wt.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max", type=int, default=24, help="cap total articles (seeds + 1-hop)")
    args = ap.parse_args()

    seeds = [json.loads(line)["title"] for line in SEEDS.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"[wiki] {len(seeds)} seeds", flush=True)

    leads: dict[str, tuple[str, int]] = {}      # title -> (wikitext, revid)
    for t in seeds:
        r = fetch_lead(t)
        if r:
            leads[t] = r
        time.sleep(0.5)

    # 1-hop expand: linked targets of the seed leads, until we hit --max
    expand: list[str] = []
    for t in seeds:
        for _surf, target in parse_wikilinks(leads.get(t, ("", 0))[0]):
            if target not in leads and target not in expand:
                expand.append(target)
    for t in expand:
        if len(leads) >= args.max:
            break
        r = fetch_lead(t)
        if r:
            leads[t] = r
        time.sleep(0.5)
    print(f"[wiki] {len(leads)} articles fetched", flush=True)

    # Resolve ONLY the fetched article titles -> QID. A wikilink is in-corpus iff its target TITLE is a
    # fetched article, so we never resolve arbitrary link targets (that caused the 429).
    title2qid = resolve_qids(sorted(leads))
    closed = {t for t in leads if t in title2qid}
    print(f"[wiki] {len(closed)}/{len(leads)} articles resolved to QIDs", flush=True)

    records = []
    for t in sorted(closed):
        wt, revid = leads[t]
        gold = [[title2qid[target], surface]
                for surface, target in parse_wikilinks(wt) if target in closed]
        records.append({"doc_id": title2qid[t], "title": t, "revid": revid,
                        "text": wikitext_to_plain(wt), "gold": gold})

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    ngold = sum(len(r["gold"]) for r in records)
    print(f"[wiki] wrote {OUT.name}: {len(records)} docs, {ngold} gold mentions", flush=True)


if __name__ == "__main__":
    main()
