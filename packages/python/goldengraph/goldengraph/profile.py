"""Virtual Fingerprint synthesis + cross-document resolution (Semantic Signature).

The pivot that repairs the multi-hop knowledge-graph "shatter": instead of
resolving graph elements on raw extraction text or raw neighborhood text, we
synthesize a *rigid, standardized fingerprint* for every element (entity AND
relationship) from its local neighborhood, then resolve across documents by
comparing those fingerprints in the `goldenprofile` engine.

    <name> | <category> | <temporal/spatial anchor> | <defining attribute>

Why this beats the two failure modes that shatter MuSiQue-style graphs:

- **Under-merge (disjoint neighborhoods).** Doc A says "Nabbes wrote Play X";
  Doc B says "Nabbes born 1605". Raw neighborhoods share ~0%, so they never
  merge and the multi-hop path breaks. The fingerprint's *defining attribute* is
  EXPECTED to differ across documents, so the engine treats it as
  evidence-that-only-adds-confidence, never a veto -- the disjoint mentions
  reunite on their shared name + category.
- **Over-merge (semantic bleeding).** Raw-text embeddings blur "Nabbes" and
  "Shakespeare" (both "17th-century playwright"). The rigid name + category gate
  keeps them apart no matter how close the vectors are.

LLM access is provider-agnostic (`LLMClient`, reused from `.llm`); the engine is
`goldenprofile_native` (the pyo3 binding over the pyo3-free Rust core), imported
lazily so this module imports without the wheel. Embeddings (for the semantic
signature) are provider-agnostic too (`Embedder`, reused from `.embed`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .extract import Extraction
from .llm import LLMClient

# --- the rigid schema -------------------------------------------------------

UNKNOWN = "UNKNOWN"

_SYNTH_PROMPT = """You generate a rigid identifying FINGERPRINT for each entity \
in a knowledge graph, using the entity and its local relationships. Return \
STRICT JSON only, no prose: {{"fingerprints": ["<name> | <category> | <anchor> \
| <attribute>", ...]}} with EXACTLY one string per entity, in the SAME ORDER as \
the numbered list below. Each fingerprint has 4 pipe-delimited parts:
- name: the normalized canonical name of the entity
- category: its primary class or role (e.g. Person, Company, Playwright)
- anchor: a temporal OR spatial anchor (a year, an era, or a place); UNKNOWN if none
- attribute: the single most defining attribute; UNKNOWN if none
Use the literal UNKNOWN for any unknown part. Keep every part terse -- a few \
words, never a sentence (flowing prose reintroduces the semantic over-merge this \
fingerprint exists to prevent). Entities (with their relationships):
{entities}"""


@dataclass
class Fingerprint:
    """One synthesized Virtual Fingerprint and what it describes.

    `kind` is "node" or "edge"; `ref` is the index into the extraction's
    `mentions` (node) or `relationships` (edge) list; `text` is the rigid
    pipe-delimited fingerprint string.
    """

    kind: str
    ref: int
    text: str


@dataclass
class ProfileResolution:
    """Result of resolving fingerprints into cross-document entities.

    `clusters` partitions fingerprint indices (into the resolved list) into
    cross-document elements; `edges` is the list of scored merges (each a dict
    with `a`, `b`, and a `score` breakdown) -- the never-black-box audit trail.
    """

    fingerprints: list[Fingerprint]
    clusters: list[list[int]]
    edges: list[dict[str, Any]]


# --- synthesis (the genuinely new host logic) -------------------------------


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _neighborhood_lines(extraction: Extraction) -> list[str]:
    """Render each entity with its local relationships, the context the LLM
    compresses into a fingerprint. Disjoint across documents by design -- that is
    the whole point: the fingerprint globalizes the local view."""
    names = [m.name for m in extraction.mentions]
    rels_by_entity: dict[int, list[str]] = {i: [] for i in range(len(names))}
    for r in extraction.relationships:
        if 0 <= r.subj < len(names) and 0 <= r.obj < len(names):
            rels_by_entity[r.subj].append(f"{r.predicate} {names[r.obj]}")
            rels_by_entity[r.obj].append(f"{names[r.subj]} {r.predicate}")
    lines = []
    for i, m in enumerate(extraction.mentions):
        rels = "; ".join(rels_by_entity[i]) or "(no relationships)"
        ctx = f" -- {m.context}" if m.context else ""
        lines.append(f"{i}. {m.name} [{m.typ}]{ctx} | relationships: {rels}")
    return lines


def _fingerprint_from_mention(m: Any) -> str:
    """Deterministic fallback fingerprint when no LLM is available or its output
    is malformed/misaligned. Uses only the mention's own fields, so it never
    fabricates -- name | type | UNKNOWN anchor | first words of the context."""
    name = (m.name or UNKNOWN).strip() or UNKNOWN
    cat = (m.typ or UNKNOWN).strip() or UNKNOWN
    attr = " ".join((m.context or "").split()[:6]).strip() or UNKNOWN
    return f"{name} | {cat} | {UNKNOWN} | {attr}"


def synthesize_node_fingerprints(
    extraction: Extraction, llm: LLMClient | None = None
) -> list[str]:
    """One fingerprint string per entity (in `mentions` order). With an `llm`,
    a single call synthesizes all of them from their neighborhoods; without one
    (or on malformed/misaligned output) falls back to the deterministic
    per-mention fingerprint so the pipeline never drops an element."""
    n = len(extraction.mentions)
    if n == 0:
        return []
    fallback = [_fingerprint_from_mention(m) for m in extraction.mentions]
    if llm is None:
        return fallback
    prompt = _SYNTH_PROMPT.format(entities="\n".join(_neighborhood_lines(extraction)))
    try:
        data = json.loads(_strip_fence(llm.complete(prompt)))
        fps = [str(x) for x in data.get("fingerprints", [])]
    except (json.JSONDecodeError, AttributeError, TypeError):
        return fallback
    # Defensive: the LLM must return exactly one fingerprint per entity, aligned.
    # On any count drift, fall back wholesale rather than misattribute a row.
    if len(fps) != n:
        return fallback
    return [fp if fp.count("|") >= 1 else fallback[i] for i, fp in enumerate(fps)]


def synthesize_edge_fingerprints(
    extraction: Extraction, node_fingerprints: list[str]
) -> list[str]:
    """One fingerprint per relationship, derived deterministically from the
    predicate and its resolved endpoint names: `predicate | predicate |
    UNKNOWN | <subj> -> <obj>`. Edge fingerprints let the engine merge redundant
    relationships across documents (e.g. "WROTE" vs "PENNED" via the semantic
    signature), which rebuilds the physical multi-hop pathways."""
    names = [fp.split("|", 1)[0].strip() for fp in node_fingerprints]
    out = []
    for r in extraction.relationships:
        subj = names[r.subj] if 0 <= r.subj < len(names) else UNKNOWN
        obj = names[r.obj] if 0 <= r.obj < len(names) else UNKNOWN
        pred = (r.predicate or UNKNOWN).strip() or UNKNOWN
        out.append(f"{pred} | {pred} | {UNKNOWN} | {subj} -> {obj}")
    return out


def synthesize_profiles(
    extraction: Extraction,
    llm: LLMClient | None = None,
    *,
    include_edges: bool = True,
) -> list[Fingerprint]:
    """Fingerprint every graph element. Nodes first (LLM-synthesized when `llm`
    is given), then -- when `include_edges` -- one fingerprint per relationship."""
    node_fps = synthesize_node_fingerprints(extraction, llm)
    out = [Fingerprint("node", i, fp) for i, fp in enumerate(node_fps)]
    if include_edges:
        edge_fps = synthesize_edge_fingerprints(extraction, node_fps)
        out.extend(Fingerprint("edge", i, fp) for i, fp in enumerate(edge_fps))
    return out


# --- resolution (thin call into the native engine) --------------------------


def _engine():
    """Lazy import of the goldenprofile engine. Raises a clear, actionable error
    when the wheel is absent (mirrors how the goldengraph store requires
    goldengraph_native)."""
    try:
        from goldenprofile_native import resolve_json
    except ImportError as e:  # pragma: no cover - exercised only without the wheel
        raise ImportError(
            "goldenprofile_native is required to resolve profiles. Build it with "
            "`maturin develop` in packages/rust/extensions/goldenprofile-native, "
            "or `pip install goldenprofile-native`."
        ) from e
    return resolve_json


def resolve_profiles(
    fingerprints: list[Fingerprint],
    *,
    embedder: Any | None = None,
    config: dict[str, Any] | None = None,
) -> ProfileResolution:
    """Resolve fingerprints into cross-document elements via the engine.

    When `embedder` is given, each fingerprint string is embedded and passed as
    the semantic signature (enabling SimHash-band blocking + the embedding-cosine
    gate that bridges synonym categories). The CATEGORY field is ALSO embedded
    separately and passed as `category_embeddings`: the category gate's synonym
    escape hatch needs a category-specific signal -- the whole-fingerprint cosine
    is polluted by the (by-design divergent) defining attribute, so an exact-name
    bridge whose category label drifted ("Country" vs "Nation") would otherwise
    never bridge. `config` is an optional partial override of the engine's
    `ResolveConfig` (e.g. `{"scoring": {"merge_threshold": 0.75}}`).
    """
    resolve_json = _engine()
    profiles = []
    for fp in fingerprints:
        parts = [p.strip() for p in fp.text.split("|")]
        parts += [UNKNOWN] * (4 - len(parts))
        profiles.append(
            {
                "kind": fp.kind,
                "name": parts[0] or UNKNOWN,
                "category": parts[1] or UNKNOWN,
                "anchor": parts[2] or UNKNOWN,
                # keep any pipes that belonged to the attribute
                "attribute": " | ".join(parts[3:]).strip() or UNKNOWN,
            }
        )
    request: dict[str, Any] = {"profiles": profiles}
    if embedder is not None and fingerprints:
        vecs = embedder.embed([fp.text for fp in fingerprints])
        request["embeddings"] = [[float(x) for x in row] for row in vecs]
        # Category-only embeddings for the gate's synonym escape hatch. Embedding
        # the (few distinct) category strings is cheap and cache-friendly.
        cat_vecs = embedder.embed([p["category"] for p in profiles])
        request["category_embeddings"] = [[float(x) for x in row] for row in cat_vecs]
    if config:
        request["config"] = config

    result = json.loads(resolve_json(json.dumps(request)))
    return ProfileResolution(
        fingerprints=fingerprints,
        clusters=[list(c) for c in result.get("clusters", [])],
        edges=list(result.get("edges", [])),
    )
