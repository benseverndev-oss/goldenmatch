"""Real-world (Wikidata) capability corpus: a committed SPARQL-pull fixture turned
into the SAME `_Entity` / `Document` / `AggQuestion` types the synthetic aggregation
bench uses, so ALL scoring / floor / bucket / gate logic in `aggregation.py` is reused
unchanged. The only thing that changes is the data: real company names + aliases +
real subsidiary sets instead of the synthetic fan-out corpus.

The bench NEVER hits live Wikidata -- it reads the committed fixture. Only
`scripts/pull_wikidata_capability_fixture.py` touches the network (run by hand)."""
from __future__ import annotations

import json
from pathlib import Path

from .engineered import _Entity

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_realworld_entities(fixture_path) -> list[_Entity]:
    """Load the committed Wikidata fixture into the harness's `_Entity` type.
    qid -> id (ground truth), canonical -> canonical, aliases -> variants
    (real name variation; canonical is never duplicated into variants)."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    out = []
    for e in data["entities"]:
        variants = tuple(a for a in e.get("aliases", ()) if a != e["canonical"])
        out.append(_Entity(id=e["qid"], canonical=e["canonical"], variants=variants))
    return out
