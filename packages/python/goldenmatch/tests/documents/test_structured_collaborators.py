"""Task 4: the structured collaborator seams -- three Protocols, their Fakes, the
VLM impls, and `resolve_structured`. Offline only: every test drives a scripted
transport or a Fake, never a live network call."""
import json

import pytest
from goldenmatch.documents.config import resolve_structured
from goldenmatch.documents.extractor import (
    Classifier,
    FakeClassifier,
    FakeFallbackExtractor,
    FakeTemplateExtractor,
    FallbackExtractor,
    TemplateExtractor,
)
from goldenmatch.documents.structured import VLMTemplateExtractor
from goldenmatch.documents.templates import get_template
from goldenmatch.documents.types import (
    ClassifyResult,
    ExtractedRow,
    ExtractResult,
    PageImage,
    StructuredResult,
)
from goldenmatch.documents.vlm_classifier import VLMClassifier, VLMFallbackExtractor

PAGES = [PageImage(b"\x89PNG\r\n\x1a\n0", 10, 10, 0)]


def scripted_transport(responses):
    """Returns pre-scripted responses in order; an Exception item is raised
    (drives the retry paths). Records payloads on `.calls`. Mirrors the
    lambda/`calls` pattern in test_vlm_backend.py."""
    it = iter(responses)
    calls = []

    def send(payload):
        calls.append(payload)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r

    send.calls = calls
    return send


def _msg(text):
    return {"choices": [{"message": {"content": text}}]}


# ---------------------------------------------------------------- the Fakes


def test_fake_classifier_returns_scripted_and_counts():
    cr = ClassifyResult("invoice", 0.9)
    fake = FakeClassifier([cr])
    assert fake.calls == 0
    assert fake.classify(PAGES) is cr
    assert fake.calls == 1


def test_fake_template_extractor_returns_scripted_and_counts():
    sr = StructuredResult(header=None, line_items=[], error="x")
    fake = FakeTemplateExtractor([sr])
    t = get_template("invoice")
    assert fake.calls == 0
    assert fake.extract_structured(PAGES, t) is sr
    assert fake.calls == 1


def test_fake_fallback_extractor_returns_scripted_and_counts():
    er = ExtractResult(rows=[])
    fake = FakeFallbackExtractor([er])
    assert fake.calls == 0
    assert fake.suggest_and_extract(PAGES) is er
    assert fake.calls == 1


# ---------------------------------------------------------------- VLMClassifier


def test_vlm_classifier_builds_payload_and_parses():
    from goldenmatch.documents.classify import classify_prompt

    send = scripted_transport([_msg(json.dumps({"doctype": "invoice", "confidence": 0.87}))])
    out = VLMClassifier(transport=send, model="gpt-4o").classify(PAGES)
    assert out == ClassifyResult("invoice", 0.87)
    # payload: classify prompt text + an image block, temperature 0, small max_tokens
    payload = send.calls[0]
    assert payload["model"] == "gpt-4o"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] <= 500
    content = payload["messages"][0]["content"]
    assert content[0]["text"] == classify_prompt()
    assert any(b.get("type") == "image_url" for b in content[1:])


def test_vlm_classifier_transport_failure_raises_valueerror():
    send = scripted_transport([OSError("boom"), OSError("boom"), OSError("boom")])
    with pytest.raises(ValueError):
        VLMClassifier(transport=send, model="gpt-4o", max_attempts=3).classify(PAGES)
    assert len(send.calls) == 3  # exhausted the retry budget


def test_vlm_classifier_parse_failure_raises_and_is_not_retried():
    # A successful-but-malformed response: parse is deterministic (temp 0), so the
    # ValueError propagates and is NOT retried (would just reproduce the bad JSON).
    send = scripted_transport([_msg("not json at all")])
    with pytest.raises(ValueError):
        VLMClassifier(transport=send, model="gpt-4o", max_attempts=3).classify(PAGES)
    assert len(send.calls) == 1


def test_vlm_classifier_rejects_bad_max_attempts():
    with pytest.raises(ValueError):
        VLMClassifier(transport=scripted_transport([]), model="gpt-4o", max_attempts=0)


# ---------------------------------------------------------------- VLMTemplateExtractor


def test_vlm_template_extractor_parses_good_invoice():
    resp = {
        "header": {"values": {"invoice_number": "INV-1", "total_amount": "100"},
                   "confidence": {"invoice_number": 0.9}},
        "line_items": [{"values": {"description": "Widget", "quantity": "2"},
                        "confidence": {}}],
    }
    send = scripted_transport([_msg(json.dumps(resp))])
    t = get_template("invoice")
    out = VLMTemplateExtractor(transport=send, model="gpt-4o").extract_structured(PAGES, t)
    assert out.error is None
    assert isinstance(out.header, ExtractedRow)
    assert out.header.values["invoice_number"] == "INV-1"
    assert out.header.values["vendor_name"] is None  # missing -> null
    assert len(out.line_items) == 1
    assert out.line_items[0].values["description"] == "Widget"
    # payload names the header + line-item fields so the VLM knows the shape
    text = json.dumps(send.calls[0])
    assert "invoice_number" in text and "line_total" in text


def test_vlm_template_extractor_transport_failure_wraps_not_raises():
    send = scripted_transport([OSError("boom"), OSError("boom"), OSError("boom")])
    t = get_template("invoice")
    out = VLMTemplateExtractor(transport=send, model="gpt-4o",
                               max_attempts=3).extract_structured(PAGES, t)
    assert out.header is None and out.line_items == []
    assert out.error is not None  # WRAPPED, never raised (batch-continues contract)


def test_vlm_template_extractor_parse_failure_wraps_and_is_not_retried():
    # A successful-but-malformed response: WRAP into StructuredResult(error=...),
    # never raise, and don't retry a deterministic parse failure.
    send = scripted_transport([_msg("not json at all")])
    t = get_template("invoice")
    out = VLMTemplateExtractor(transport=send, model="gpt-4o",
                               max_attempts=3).extract_structured(PAGES, t)
    assert out.header is None and out.line_items == []
    assert out.error is not None
    assert len(send.calls) == 1


def test_vlm_template_extractor_receipt_flat_no_line_items():
    # Receipt has empty line_item_fields -> the flat branch of the instruction, and
    # any stray line_items in the response are forced to [].
    resp = {"header": {"values": {"merchant_name": "Cafe", "total_amount": "9"},
                       "confidence": {}},
            "line_items": [{"values": {"description": "ignored"}, "confidence": {}}]}
    send = scripted_transport([_msg(json.dumps(resp))])
    t = get_template("receipt")
    out = VLMTemplateExtractor(transport=send, model="gpt-4o").extract_structured(PAGES, t)
    assert out.error is None
    assert out.header.values["merchant_name"] == "Cafe"
    assert out.line_items == []  # flat doctype ignores stray items
    instruction = send.calls[0]["messages"][0]["content"][0]["text"]
    assert "merchant_name" in instruction and '"line_items": []' in instruction


# ---------------------------------------------------------------- VLMFallbackExtractor


def test_vlm_fallback_extractor_two_calls_suggest_then_extract():
    suggest_resp = _msg(json.dumps({"fields": [
        {"name": "full_name", "kind": "text", "hint": "the person"},
    ]}))
    extract_resp = _msg(json.dumps({"records": [
        {"values": {"full_name": "Ada"}, "confidence": {"full_name": 0.9}},
    ]}))
    send = scripted_transport([suggest_resp, extract_resp])
    out = VLMFallbackExtractor(transport=send, model="gpt-4o").suggest_and_extract(PAGES)
    assert isinstance(out, ExtractResult)
    assert out.error is None
    assert len(out.rows) == 1
    assert out.rows[0].values["full_name"] == "Ada"
    assert len(send.calls) == 2  # suggest then extract


def test_vlm_fallback_extractor_suggest_failure_wraps_not_raises():
    # suggest_schema RAISES on transport-exhaust, but the fallback seam's contract
    # is ExtractResult -- so a suggest failure must WRAP (batch-continues in Task 6),
    # never bubble out of the batch loop. No extract call is made.
    send = scripted_transport([OSError("boom"), OSError("boom"), OSError("boom")])
    out = VLMFallbackExtractor(transport=send, model="gpt-4o").suggest_and_extract(PAGES)
    assert isinstance(out, ExtractResult)
    assert out.rows == [] and out.error is not None
    assert len(send.calls) == 3  # suggest retried to exhaustion, no extract call


# ---------------------------------------------------------------- resolve_structured


def test_resolve_structured_bad_backend_raises():
    with pytest.raises(ValueError):
        resolve_structured("nope", "gpt-4o")


def test_resolve_structured_returns_three_protocol_objects(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY_PERSONAL", "dummy-key-offline")
    clf, tex, fb = resolve_structured("vlm", "gpt-4o")
    assert isinstance(clf, Classifier)
    assert isinstance(tex, TemplateExtractor)
    assert isinstance(fb, FallbackExtractor)
    # all three share ONE resolved transport (the stated purpose of resolve_structured)
    assert clf._send is tex._send is fb._send
