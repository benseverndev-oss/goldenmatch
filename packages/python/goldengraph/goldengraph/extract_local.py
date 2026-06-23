"""In-house, network-free extraction backends -- the prototype-eval for replacing
the per-document LLM extraction call (the build's measured perf bottleneck) with a
small LOCAL model. The tasks are narrow (text -> typed entities + relations), so a
distilled/off-the-shelf model is feasible; this module exists to MEASURE the
answer_match-vs-speed tradeoff against the API extractor before committing to
training one.

Each backend returns a `text -> Extraction` callable matching `extract.extract`'s
shape (it takes `(text, llm=None)` and ignores the llm). Heavy deps (transformers /
gliner / torch) are imported lazily inside the factory, so importing this module is
free; only selecting a backend pulls them in.
"""

from __future__ import annotations

from .extract import Extraction, Mention, Relationship


def parse_rebel_triplets(text: str) -> list[tuple[str, str, str]]:
    """Decode REBEL's linearized output into (head, relation, tail) triples.

    REBEL (Babelscape/rebel-large) emits a string with `<triplet>`/`<subj>`/`<obj>`
    control tokens; this is the canonical decoder from the REBEL repo. Pure (no
    model), so it is unit-tested directly."""
    triplets: list[tuple[str, str, str]] = []
    relation = subject = object_ = ""
    text = (
        text.replace("<s>", "").replace("<pad>", "").replace("</s>", "").strip()
    )
    current = "x"
    for token in text.split():
        if token == "<triplet>":
            current = "t"
            if relation != "":
                triplets.append((subject.strip(), relation.strip(), object_.strip()))
                relation = ""
            subject = ""
        elif token == "<subj>":
            current = "s"
            if relation != "":
                triplets.append((subject.strip(), relation.strip(), object_.strip()))
            object_ = ""
        elif token == "<obj>":
            current = "o"
            relation = ""
        else:
            if current == "t":
                subject += " " + token
            elif current == "s":
                object_ += " " + token
            elif current == "o":
                relation += " " + token
    if subject.strip() and relation.strip() and object_.strip():
        triplets.append((subject.strip(), relation.strip(), object_.strip()))
    return triplets


def triplets_to_extraction(
    triplets: list[tuple[str, str, str]], *, default_type: str = "entity"
) -> Extraction:
    """Build an `Extraction` from (head, relation, tail) triples: dedup the head/tail
    spans into mentions (REBEL does not type entities -- a real in-house model would;
    `default_type` is the placeholder), and one relationship per triple keyed by the
    mention indices."""
    name_to_idx: dict[str, int] = {}
    mentions: list[Mention] = []
    rels: list[Relationship] = []

    def _idx(name: str) -> int:
        if name not in name_to_idx:
            name_to_idx[name] = len(mentions)
            mentions.append(Mention(name=name, typ=default_type, context=""))
        return name_to_idx[name]

    for head, rel, tail in triplets:
        if not head or not tail or not rel:
            continue
        s, o = _idx(head), _idx(tail)
        if s != o:
            rels.append(Relationship(subj=s, predicate=rel, obj=o))
    return Extraction(mentions=mentions, relationships=rels)


def rebel_extractor(model: str = "Babelscape/rebel-large", *, max_length: int = 256):
    """A local `text -> Extraction` extractor backed by REBEL (BART end-to-end
    relation extraction). The model loads ONCE here; the returned callable runs a
    network-free forward pass per document. Needs `transformers` + `torch`.

    Loads the seq2seq model directly rather than via `pipeline("text2text-generation")`:
    transformers v5 removed that task alias from the pipeline registry, and the direct
    `AutoModelForSeq2SeqLM` + `generate` path is stable across versions. The decode keeps
    REBEL's `<triplet>`/`<subj>`/`<obj>` control tokens (`skip_special_tokens=False`) --
    `parse_rebel_triplets` keys off them."""
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model)
    mdl = AutoModelForSeq2SeqLM.from_pretrained(model)

    def _extract(text: str, llm=None) -> Extraction:
        inputs = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=max_length
        )
        out = mdl.generate(**inputs, max_length=max_length)
        decoded = tokenizer.decode(out[0], skip_special_tokens=False)
        return triplets_to_extraction(parse_rebel_triplets(decoded))

    return _extract


def gliner_extractor(
    model: str = "urchade/gliner_mediumv2.1",
    *,
    labels: tuple[str, ...] = ("person", "organization", "location", "work", "event", "date"),
    threshold: float = 0.4,
):
    """A local NER-only `text -> Extraction` extractor backed by GLiNER (typed
    entities, CPU-friendly). It produces typed MENTIONS but no relationships, so the
    graph has nodes without edges -- useful to isolate the entity-coverage half of
    the tradeoff. Needs `gliner`."""
    from gliner import GLiNER

    gl = GLiNER.from_pretrained(model)

    def _extract(text: str, llm=None) -> Extraction:
        ents = gl.predict_entities(text, list(labels), threshold=threshold)
        seen: dict[str, int] = {}
        mentions: list[Mention] = []
        for e in ents:
            name = e["text"].strip()
            if name and name not in seen:
                seen[name] = len(mentions)
                mentions.append(Mention(name=name, typ=e.get("label", "entity"), context=""))
        return Extraction(mentions=mentions, relationships=[])

    return _extract
