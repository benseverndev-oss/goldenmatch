from goldencheck.llm.prompts import LLMResponse, LLMColumnAssessment, LLMIssue, LLMUpgrade, LLMRelation

def test_parse_full_response():
    data = {
        "columns": {
            "email": {
                "semantic_type": "email",
                "issues": [{"severity": "error", "check": "format", "message": "bad emails", "affected_values": ["x"]}],
                "upgrades": [{"original_check": "nullability", "original_severity": "info", "new_severity": "warning", "reason": "emails should not be null"}],
                "downgrades": [],
            }
        },
        "relations": [{"type": "temporal_order", "columns": ["start", "end"], "reasoning": "start before end"}],
    }
    resp = LLMResponse(**data)
    assert "email" in resp.columns
    assert resp.columns["email"].semantic_type == "email"
    assert len(resp.columns["email"].issues) == 1
    assert len(resp.relations) == 1

def test_parse_empty_response():
    resp = LLMResponse()
    assert resp.columns == {}
    assert resp.relations == []

def test_parse_minimal_column():
    col = LLMColumnAssessment(semantic_type="identifier")
    assert col.issues == []
    assert col.upgrades == []
    assert col.downgrades == []
