"""Slice D: KG-vs-KG capability scorecard. Model each KG framework's documented ER strategy as
a record_key dial and run it through two ER-driven capability metrics (bridge-recall from slice A,
aggregation set-F1 from slice B1). Gates that goldengraph's fuzzy ER beats the exact-match
frameworks (LightRAG/MS-GraphRAG, which coincide on this single-entity-type corpus). Plus an
opt-in real-framework confirmation lane.

NO new dial: the scorecard maps framework labels to EXISTING dials.py keyfns. The deterministic
metrics + gate + render are wheel-free EXCEPT the per-dial graph helpers (they reach
ablation._build_store). The answer->set parser + gate shape are wheel-free.
"""
from __future__ import annotations


def parse_entity_set(answer: str, s2c: dict) -> set:
    """Scan the framework's free-text answer for known surfaces; return the set of canonical ids.
    `s2c` is a FIRST-WINS scalar surface->canonical map (matches set_f1's scalar gold members)."""
    low = answer.lower()
    out: set = set()
    for surf, canon in s2c.items():
        if surf.lower() in low:
            out.add(canon)
    return out
