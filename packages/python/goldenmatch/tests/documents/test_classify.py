import pytest
from goldenmatch.documents.classify import classify_prompt, parse_classify
from goldenmatch.documents.types import ClassifyResult

_EXPECTED_PROMPT = (
    'You are shown a document. Classify it as exactly one of these types: '
    'invoice, po, statement, receipt. If it is none of these, answer "generic". '
    'Return ONLY JSON: {"doctype": "<one of: invoice|po|statement|receipt|generic>", '
    '"confidence": <0..1>}. No prose.'
)


def test_classify_prompt_is_the_fixed_constant():
    assert classify_prompt() == _EXPECTED_PROMPT


def test_parse_clean_json():
    r = parse_classify('{"doctype":"invoice","confidence":0.9}')
    assert isinstance(r, ClassifyResult)
    assert r.doctype == "invoice"
    assert r.confidence == 0.9


def test_parse_fenced_blob():
    r = parse_classify('```json\n{"doctype":"receipt","confidence":0.5}\n```')
    assert r.doctype == "receipt"
    assert r.confidence == 0.5


def test_generic_is_valid():
    r = parse_classify('{"doctype":"generic","confidence":0.2}')
    assert r.doctype == "generic"


def test_unknown_doctype_raises():
    with pytest.raises(ValueError):
        parse_classify('{"doctype":"nope","confidence":0.9}')


def test_missing_confidence_raises():
    with pytest.raises(ValueError):
        parse_classify('{"doctype":"invoice"}')


def test_out_of_range_confidence_clamps():
    assert parse_classify('{"doctype":"po","confidence":1.5}').confidence == 1.0
    assert parse_classify('{"doctype":"po","confidence":-0.3}').confidence == 0.0


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_confidence_raises(token):
    # Python json.loads accepts bare NaN/Infinity tokens; Rust serde_json rejects
    # them (strict JSON). Match Rust -- both legs must error, not silently clamp.
    with pytest.raises(ValueError):
        parse_classify(f'{{"doctype":"po","confidence":{token}}}')


def test_confidence_coercion_boundaries():
    # integer confidence coerces to float; direct 0/1 are valid (not via clamp)
    assert parse_classify('{"doctype":"po","confidence":1}').confidence == 1.0
    assert parse_classify('{"doctype":"po","confidence":0}').confidence == 0.0
    # present-but-null, string, and bool confidence all error (parity with as_f64)
    for bad in ("null", '"0.9"', "true"):
        with pytest.raises(ValueError):
            parse_classify(f'{{"doctype":"po","confidence":{bad}}}')
