"""CLEAR-KG real-data validity track: Wikipedia homographs.

The synthetic Track B (`generate.py`) can be dismissed with "you wrote the docs."
This track kills that objection: the corpus is REAL Wikipedia prose and the
ground truth is not authored by us --

  * an article TITLE is the exact entity id (Wikipedia disambiguates for us);
  * a curated set of ambiguous SURFACE strings ("Java", "Mercury", "Michael
    Jordan") each map to 2+ distinct articles -- real homographs;
  * an article's outbound LINKS are its real co-mention neighborhood, and two
    homograph articles have (near-)disjoint neighborhoods by construction of
    being about different things.

So neighborhood ER separates the homographs where name-only ER cannot -- on data
nobody in this repo authored. Same mechanism, same metric, real corpus.

Network is isolated in `fetch_article()`; `build_mentions()` is pure so the
offline test feeds a tiny hand-built `articles` dict (no network in CI). Fetched
articles cache to a gitignored `data/` dir -- nothing real-data is committed.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_API = "https://en.wikipedia.org/w/api.php"
_UA = "clear-kg-bench/0.1 (https://github.com/benseverndev-oss/goldenmatch; research)"
DATA_DIR = Path(__file__).parent / "data" / "wiki"

# Curated real homographs: one ambiguous SURFACE -> 2+ distinct Wikipedia
# articles about genuinely different things (disjoint neighborhoods). Titles are
# canonical article names; the fetcher follows redirects.
HOMOGRAPH_GROUPS: list[dict] = [
    {"surface": "Java",
     "entities": ["Java (programming language)", "Java", "Java coffee"]},
    {"surface": "Mercury",
     "entities": ["Mercury (planet)", "Mercury (element)", "Mercury (mythology)"]},
    {"surface": "Amazon",
     "entities": ["Amazon (company)", "Amazon rainforest"]},
    {"surface": "Jaguar",
     "entities": ["Jaguar", "Jaguar Cars"]},
    {"surface": "Michael Jordan",
     "entities": ["Michael Jordan", "Michael I. Jordan"]},
    {"surface": "Georgia",
     "entities": ["Georgia (country)", "Georgia (U.S. state)"]},
    {"surface": "Phoenix",
     "entities": ["Phoenix, Arizona", "Phoenix (mythology)"]},
    {"surface": "Cambridge",
     "entities": ["Cambridge", "Cambridge, Massachusetts"]},
]


def all_titles(groups: list[dict] = HOMOGRAPH_GROUPS) -> list[str]:
    seen: dict[str, None] = {}
    for g in groups:
        for t in g["entities"]:
            seen.setdefault(t, None)
    return list(seen)


def _api_get(*, retries: int = 4, **params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("action", "query")
    url = _API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (fixed host)
                return json.load(r)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last = e  # the agent proxy resets the first hit intermittently
            time.sleep(2 * (attempt + 1))
    raise OSError(f"Wikipedia API failed after {retries} attempts: {last}")


def fetch_article(title: str, *, cache_dir: Path = DATA_DIR, refresh: bool = False) -> dict:
    """Return ``{title, extract, links}`` for one article, hitting the Wikipedia
    action API once and caching the result as JSON under ``cache_dir``.

    NETWORK. Everything downstream (`build_mentions`) is pure and offline."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = urllib.parse.quote(title.replace(" ", "_"), safe="")
    cache = cache_dir / f"{slug}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))

    d = _api_get(
        prop="extracts|links", titles=title, explaintext=1,
        exsectionformat="plain", pllimit="max", plnamespace=0, redirects=1,
    )
    pages = d.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    art = {
        "title": page.get("title", title),
        "extract": page.get("extract", "") or "",
        "links": [l_["title"] for l_ in page.get("links", [])],
    }
    cache.write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
    return art


def fetch_all(groups: list[dict] = HOMOGRAPH_GROUPS, *, cache_dir: Path = DATA_DIR,
              refresh: bool = False) -> dict[str, dict]:
    """Fetch every article referenced by ``groups`` (cached). Keyed by the
    REQUESTED title so `build_mentions` can look them up by group entry."""
    out: dict[str, dict] = {}
    for title in all_titles(groups):
        out[title] = fetch_article(title, cache_dir=cache_dir, refresh=refresh)
    return out


def _chunks(text: str, *, max_chunks: int, min_len: int) -> list[str]:
    """Split an extract into paragraph chunks (>= ``min_len`` chars), capped."""
    out: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) >= min_len:
            out.append(para)
        if len(out) >= max_chunks:
            break
    # Fall back to the whole extract if paragraph splitting found nothing usable.
    if not out and text.strip():
        out = [text.strip()]
    return out


def build_mentions(
    articles: dict[str, dict],
    groups: list[dict] = HOMOGRAPH_GROUPS,
    *,
    max_mentions_per_entity: int = 4,
    top_k_neighbors: int = 25,
    min_chunk_len: int = 80,
) -> list[dict]:
    """Pure: turn fetched articles into Track-B mentions.

    Each entity's extract is chunked into up to ``max_mentions_per_entity``
    mentions. Every mention of an entity carries:
      * ``surface``          = the group's shared AMBIGUOUS string (so the two
                               homograph entities are surface-confusable);
      * ``gold_entity_id``   = the article title (exact, not authored by us);
      * ``neighbor_surfaces``= the article's top-K outbound links -- its real
                               co-mention neighborhood. Disjoint across
                               homograph entities, identical across a single
                               entity's mentions, so co-mention ER merges the
                               latter and splits the former.

    The neighbor signature is per-ARTICLE (stable across an entity's mentions),
    not per-chunk: real per-chunk co-mentions are sparse (the WhoIsWho SND
    lesson), and the honest claim under test is the homograph split, which turns
    on the neighborhoods being disjoint -- which they are, by article identity.
    """
    mentions: list[dict] = []
    for gi, g in enumerate(groups):
        surface = g["surface"]
        for ei, title in enumerate(g["entities"]):
            art = articles.get(title)
            if not art:
                continue
            neighbors = list(art.get("links", []))[:top_k_neighbors]
            chunks = _chunks(
                art.get("extract", ""),
                max_chunks=max_mentions_per_entity, min_len=min_chunk_len,
            )
            for ci, _chunk in enumerate(chunks):
                mentions.append({
                    "mention_id": f"g{gi}:e{ei}:c{ci}",
                    "doc_id": f"{title}#{ci}",
                    "surface": surface,
                    "gold_entity_id": art.get("title", title),
                    "neighbor_surfaces": neighbors,
                })
    return mentions


def load_corpus(
    *,
    groups: list[dict] = HOMOGRAPH_GROUPS,
    cache_dir: Path = DATA_DIR,
    offline_articles: dict[str, dict] | None = None,
    refresh: bool = False,
    **build_kw,
) -> dict:
    """Fetch (or accept offline) articles and build a Track-B corpus dict shaped
    like `generate.generate_corpus` output (only ``mentions`` is consumed by the
    runner). Pass ``offline_articles`` to skip the network entirely."""
    articles = offline_articles if offline_articles is not None else fetch_all(
        groups, cache_dir=cache_dir, refresh=refresh,
    )
    mentions = build_mentions(articles, groups, **build_kw)
    entities = sorted({m["gold_entity_id"] for m in mentions})
    return {
        "mentions": mentions,
        "entities": entities,
        "groups": groups,
        "homograph_surfaces": sorted({g["surface"] for g in groups}),
        "articles": articles,
    }
