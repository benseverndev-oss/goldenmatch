from pathlib import Path

from goldenmatch.core.distributed_routing_rules import ROUTING_DOC_ANCHORS

_TUNING = Path(__file__).resolve().parents[4] / "docs-site" / "goldenmatch" / "tuning.mdx"


def test_tuning_doc_exists():
    assert _TUNING.is_file(), f"missing {_TUNING}"


def test_every_routing_rule_has_a_doc_anchor():
    text = _TUNING.read_text(encoding="utf-8")
    missing = [a for a in ROUTING_DOC_ANCHORS.values() if a not in text]
    assert not missing, (
        f"tuning.mdx is missing anchors for routing rules: {missing}. "
        f"Add a section per anchor so explain/lint links resolve.")
