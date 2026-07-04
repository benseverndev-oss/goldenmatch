"""Re-DocRED -> ATLOP feature preprocessing.

Faithful port of `read_docred` from wzhouad/ATLOP, generalised so the tokenizer is
INJECTED (any object with `.tokenize(str)->list[str]` and
`.build_inputs_with_special_tokens(list[int])->list[int]` /
`.convert_tokens_to_ids`). That lets the entity-marker insertion, the entity-position
mapping, and the pair/label matrix construction be unit-tested offline with a trivial
stub tokenizer -- no torch, no transformers, no model download.

Each example dict:
  input_ids:   list[int]  (with CLS/SEP special tokens)
  entity_pos:  list[list[(start,end)]]  offsets into the NON-special-token stream; the
               model adds +1 for the leading special token. `start` points at the "*"
               marker inserted before each mention.
  labels:      list[list[int]]  one 97-dim multi-hot per pair in `hts` (index 0 = TH/NA)
  hts:         list[[h,t]]  every ordered entity pair h!=t (positives first, then negs)
  title:       str

`num_class = 97`: index 0 is the adaptive-threshold class, relations occupy 1..96.
"""
from __future__ import annotations

NUM_CLASS = 97  # 96 relations + the TH/NA class at index 0


def build_rel2id(*doc_sets: list[dict]) -> dict[str, int]:
    """Deterministic {relation_Pid -> class_index in 1..96}. Sorted union of every
    relation across the given splits (Re-DocRED train/dev/test all share the 96)."""
    rels: set[str] = set()
    for docs in doc_sets:
        for d in docs:
            for label in d.get("labels", []):
                rels.add(label["r"])
    return {r: i + 1 for i, r in enumerate(sorted(rels))}


def read_docred(docs: list[dict], tokenizer, rel2id: dict[str, int],
                max_seq_length: int = 1024, with_evidence: bool = False) -> list[dict]:
    """Convert raw Re-DocRED docs into ATLOP features. Mirrors the reference marker
    insertion (a literal ``*`` before and after every mention span).

    ``with_evidence=True`` additionally emits, per feature, ``sent_pos`` (the
    ``(start, end)`` token span of each sentence in the NON-special-token stream) and
    ``evidence`` (a list aligned with ``hts``: for each pair, the gold evidence sentence
    ids -- empty for negatives / pairs without evidence). These drive DREEAM-style
    evidence supervision; the default path is byte-identical to the plain ATLOP feature."""
    id2 = rel2id
    features: list[dict] = []
    for doc in docs:
        vertex = doc["vertexSet"]

        entity_start: set[tuple[int, int]] = set()
        entity_end: set[tuple[int, int]] = set()
        for e in vertex:
            for m in e:
                sid = m["sent_id"]
                entity_start.add((sid, m["pos"][0]))
                entity_end.add((sid, m["pos"][1] - 1))

        sents: list[str] = []
        sent_map: list[dict[int, int]] = []
        sent_pos: list[tuple[int, int]] = []
        for i_s, sent in enumerate(doc["sents"]):
            new_map: dict[int, int] = {}
            sent_start = len(sents)
            for i_t, token in enumerate(sent):
                pieces = tokenizer.tokenize(token)
                if (i_s, i_t) in entity_start:
                    pieces = ["*"] + pieces
                if (i_s, i_t) in entity_end:
                    pieces = pieces + ["*"]
                new_map[i_t] = len(sents)
                sents.extend(pieces)
            new_map[len(sent)] = len(sents)
            sent_map.append(new_map)
            sent_pos.append((sent_start, len(sents)))

        # truncate to the wordpiece budget (minus 2 special tokens); the long-input
        # splitter handles up to 2*512, so max_seq_length is typically 1024.
        cap = max_seq_length - 2
        sents = sents[:cap]
        # clamp sentence spans to the truncated stream; drop sentences that fall off
        sent_pos = [(a, min(b, cap)) for (a, b) in sent_pos if a < cap]

        entity_pos: list[list[tuple[int, int]]] = []
        for e in vertex:
            spans: list[tuple[int, int]] = []
            for m in e:
                start = sent_map[m["sent_id"]][m["pos"][0]]
                end = sent_map[m["sent_id"]][m["pos"][1]]
                spans.append((start, end))
            entity_pos.append(spans)

        # positive labels + evidence grouped by (h,t)
        train_triple: dict[tuple[int, int], list[int]] = {}
        pair_evi: dict[tuple[int, int], set[int]] = {}
        for label in doc.get("labels", []):
            h, t, r = label["h"], label["t"], id2[label["r"]]
            train_triple.setdefault((h, t), []).append(r)
            pair_evi.setdefault((h, t), set()).update(label.get("evidence", []))

        hts: list[list[int]] = []
        labels: list[list[int]] = []
        evidence: list[list[int]] = []
        n_e = len(vertex)
        pos_pairs = set(train_triple.keys())
        n_sents = len(sent_pos)
        # positives first
        for (h, t), rels in train_triple.items():
            vec = [0] * NUM_CLASS
            for r in rels:
                vec[r] = 1
            labels.append(vec)
            hts.append([h, t])
            evidence.append(sorted(s for s in pair_evi.get((h, t), set()) if s < n_sents))
        # then all remaining ordered pairs as negatives (TH class = 1)
        for h in range(n_e):
            for t in range(n_e):
                if h != t and (h, t) not in pos_pairs:
                    vec = [0] * NUM_CLASS
                    vec[0] = 1
                    labels.append(vec)
                    hts.append([h, t])
                    evidence.append([])

        if not hts:
            continue

        input_ids = tokenizer.convert_tokens_to_ids(sents)
        input_ids = tokenizer.build_inputs_with_special_tokens(input_ids)

        feat = {
            "input_ids": input_ids,
            "entity_pos": entity_pos,
            "labels": labels,
            "hts": hts,
            "title": doc["title"],
        }
        if with_evidence:
            feat["sent_pos"] = sent_pos
            feat["evidence"] = evidence
        features.append(feat)
    return features
