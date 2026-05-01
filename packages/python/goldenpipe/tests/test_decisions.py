"""Tests for built-in decision functions."""
from goldenpipe.decisions import severity_gate, pii_router, row_count_gate
from goldenpipe.models.context import PipeContext


class TestSeverityGate:
    def test_no_findings(self):
        ctx = PipeContext()
        d = severity_gate(ctx)
        assert d is None

    def test_no_critical(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"severity": "warning", "check": "nulls"}]
        d = severity_gate(ctx)
        assert d is None

    def test_critical_aborts(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"severity": "critical", "check": "schema_mismatch"}]
        d = severity_gate(ctx)
        assert d is not None
        assert d.abort is True

    def test_mixed_with_critical_aborts(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [
            {"severity": "critical", "check": "schema"},
            {"severity": "warning", "check": "nulls"},
        ]
        d = severity_gate(ctx)
        assert d is not None
        assert d.abort is True


class TestPiiRouter:
    def test_no_findings(self):
        ctx = PipeContext()
        d = pii_router(ctx)
        assert d is None

    def test_no_pii(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"check": "nulls"}]
        d = pii_router(ctx)
        assert d is None

    def test_pii_detected(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"check": "pii_detection"}]
        d = pii_router(ctx)
        assert d is not None
        assert "goldenmatch.dedupe" in d.skip
        assert "goldenmatch.dedupe_pprl" in d.insert

    def test_pii_preserves_other_stages(self):
        ctx = PipeContext()
        ctx.artifacts["findings"] = [{"check": "pii_detection"}]
        d = pii_router(ctx)
        assert d.abort is False


class TestRowCountGate:
    def test_zero_rows(self):
        ctx = PipeContext(metadata={"input_rows": 0})
        d = row_count_gate(ctx)
        assert d is not None
        assert "goldenmatch.dedupe" in d.skip

    def test_one_row(self):
        ctx = PipeContext(metadata={"input_rows": 1})
        d = row_count_gate(ctx)
        assert d is not None

    def test_two_rows_no_skip(self):
        ctx = PipeContext(metadata={"input_rows": 2})
        d = row_count_gate(ctx)
        assert d is None

    def test_many_rows_no_skip(self):
        ctx = PipeContext(metadata={"input_rows": 1000})
        d = row_count_gate(ctx)
        assert d is None
