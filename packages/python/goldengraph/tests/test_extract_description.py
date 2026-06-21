"""Extraction captures a per-entity `description` -> Mention.context (the field
that sharpens resolution). Pure parse test -- no goldenmatch, no LLM."""

from __future__ import annotations

import json

from goldengraph.extract import Mention, parse_extraction


def test_parse_extraction_populates_context_from_description():
    raw = json.dumps(
        {
            "entities": [
                {"name": "IBM", "type": "org", "description": "American technology corporation"},
                {"name": "Apple", "type": "org"},  # no description -> context ""
            ],
            "relationships": [],
        }
    )
    ex = parse_extraction(raw)
    assert ex.mentions[0].context == "American technology corporation"
    assert ex.mentions[1].context == ""  # default when the model omits it


def test_mention_context_defaults_empty():
    m = Mention(name="X", typ="org")
    assert m.context == ""
