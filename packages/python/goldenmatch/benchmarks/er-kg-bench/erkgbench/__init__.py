"""ER-KG-Bench -- a neutral, reproducible scoreboard for entity-resolution
quality in knowledge-graph / agent-memory frameworks.

It runs each framework's *documented default* dedup rule (exact thresholds and
citations live in ``adapters/modeled.py``) against goldenmatch over a labelled
record set stratified by failure class, and reports pairwise precision / recall
/ F1 per class. See ``../README.md`` and ``../TAXONOMY.md``.
"""

__all__ = ["metrics", "adapters"]
