"""CLEAR-KG Track A — real-prose extraction on Re-DocRED (the competitive number).

Re-DocRED (Tan et al., 2022) is the revised, higher-quality relabelling of DocRED:
real Wikipedia documents with gold entity clusters and gold document-level
relation triples over a closed schema of ~95 Wikidata properties. It is the
standard the SPEC benchmarks Track A against (Re-DocRED relation-F1 SOTA ~80.7
fine-tuned BERT / ~74.6 strong LLM).

This loads the dev split + resolves relation names from Wikidata, and shapes each
document as the standard DocRED relation-extraction task: given the text + the
gold ENTITY set, extract the relation triples. Network is isolated in the fetch
functions (cached to a gitignored `data/`); `load_docs(offline=...)` is pure for
tests.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

_DEV_URL = "https://raw.githubusercontent.com/tonytan48/Re-DocRED/main/data/dev_revised.json"
_WD_API = "https://www.wikidata.org/w/api.php"
_UA = "clear-kg-bench/0.1 (https://github.com/benseverndev-oss/goldenmatch; research)"
DATA_DIR = Path(__file__).parent / "data" / "redocred"


def _get(url: str, *, retries: int = 4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
                return r.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:  # type: ignore[attr-defined]
            last = e
            time.sleep(2 * (attempt + 1))
    raise OSError(f"fetch failed after {retries} attempts: {last}")


def _fetch_dev(cache_dir: Path) -> list[dict]:
    cache = cache_dir / "dev_revised.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = _get(_DEV_URL)
    cache.write_bytes(raw)
    return json.loads(raw)


def _fetch_relation_names(rel_ids: list[str], cache_dir: Path) -> dict[str, str]:
    """Wikidata property-id -> English label, cached, batched at 50 ids/call."""
    cache = cache_dir / "rel_names.json"
    names: dict[str, str] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    missing = [r for r in rel_ids if r not in names]
    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        url = _WD_API + "?" + urllib.parse.urlencode(
            {"action": "wbgetentities", "ids": "|".join(chunk), "props": "labels",
             "languages": "en", "format": "json"})
        ent = json.loads(_get(url)).get("entities", {})
        for k, v in ent.items():
            lab = v.get("labels", {}).get("en")
            if lab:
                names[k] = lab["value"]
        time.sleep(0.3)
    if missing:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
    return names


def _shape(raw_docs: list[dict], rel_names: dict[str, str]) -> list[dict]:
    docs: list[dict] = []
    for d in raw_docs:
        text = " ".join(" ".join(sent) for sent in d["sents"])
        entities = []
        for v in d["vertexSet"]:
            surfaces = sorted({m["name"] for m in v})
            entities.append({"names": surfaces, "type": v[0]["type"],
                             "canonical": max(surfaces, key=len)})
        gold = {(lab["h"], rel_names.get(lab["r"], lab["r"]), lab["t"])
                for lab in d.get("labels", [])}
        docs.append({"title": d["title"], "text": text, "entities": entities, "gold": gold})
    return docs


def load_docs(limit: int = 25, *, cache_dir: Path = DATA_DIR,
              offline: tuple[list[dict], dict[str, str]] | None = None) -> tuple[list[dict], list[str]]:
    """Return (docs, relation_schema). Pass ``offline=(raw_docs, rel_names)`` to
    skip the network (tests). ``relation_schema`` is the sorted list of relation
    NAMES the extractor must choose from."""
    if offline is not None:
        full, rel_names = offline
    else:
        full = _fetch_dev(cache_dir)
        # the closed schema is EVERY relation in the full dev set (not just the
        # sliced docs) -- otherwise the prompt would leak which relations occur
        rel_ids = sorted({lab["r"] for d in full for lab in d.get("labels", [])})
        rel_names = _fetch_relation_names(rel_ids, cache_dir)
    docs = _shape(full[:limit], rel_names)
    schema = sorted(set(rel_names.values()))
    return docs, schema
