from goldencheck_types import (
    DomainPack,
    FieldMapping,
    FieldSpec,
    InferredSchema,
    is_unknown,
    unmapped_cols,
)


def test_fieldspec_minimal():
    fs = FieldSpec(name="ssn", name_hints=["ssn"], value_signals={}, suppress=[])
    assert fs.confidence_threshold is None


def test_domainpack_holds_types():
    fs = FieldSpec(name="ssn", name_hints=["ssn"], value_signals={}, suppress=[])
    pack = DomainPack(name="hc", description="", types={"ssn": fs})
    assert pack.types["ssn"].name_hints == ["ssn"]


def test_fieldmapping_unknown():
    m = FieldMapping(source_col="x", canonical=None, type="unknown",
                     confidence=0.4, evidence={})
    assert m.is_unknown


def test_inferred_schema_unmapped_list():
    m_known = FieldMapping("a", "ssn", "ssn", 0.9, {})
    m_unk = FieldMapping("b", None, "unknown", 0.3, {})
    s = InferredSchema(
        domain="hc",
        fields={"a": m_known, "b": m_unk},
        confidence=0.3,
    )
    assert s.unmapped == ["b"]


def test_predicate_parity_with_typescript():
    """Free-function predicates mirror the TS API. Both shapes return
    identical results — newer code should prefer the free functions."""
    m_known = FieldMapping("a", "ssn", "ssn", 0.9, {})
    m_unk = FieldMapping("b", None, "unknown", 0.3, {})
    s = InferredSchema(domain="hc", fields={"a": m_known, "b": m_unk}, confidence=0.3)

    assert is_unknown(m_unk) is True
    assert is_unknown(m_known) is False
    assert is_unknown(m_unk) == m_unk.is_unknown

    assert unmapped_cols(s) == ["b"]
    assert unmapped_cols(s) == s.unmapped
