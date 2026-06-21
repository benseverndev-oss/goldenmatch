from __future__ import annotations

import json

from goldenmatch.synonym import train as T
from goldenmatch.synonym.train import _norm, load_groups

# Normalized surface strings of the ER-KG-Bench eval drug families — MUST NOT
# appear in training (held-out, so GS3's re-measure is a true generalization test).
_HELD_OUT = {
    "ibuprofen", "acetaminophen", "paracetamol", "sildenafil", "warfarin",
    "advil", "motrin", "duexis", "brufen", "nurofen", "proprinal", "ibudone",
    "tylenol", "panadol", "viagra", "revatio", "liqrev", "vybrique",
    "coumadin", "jantoven",
}


def test_training_data_is_eval_disjoint():
    groups = load_groups()
    assert len(groups) >= 30
    surfaces = {_norm(m) for g in groups for m in g}
    leaked = surfaces & _HELD_OUT
    assert not leaked, f"eval surface strings leaked into training: {leaked}"


def test_has_morphological_pairs():
    morph = 0
    for raw in T._DATA.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        d = json.loads(line)
        if d.get("morph"):
            morph += 1
    assert morph >= 5
