"""The scorecard CLI parses + is import-clean. No real run (needs key + wheel)."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e import run_scorecard


def test_parser_defaults():
    args = run_scorecard._parser().parse_args([])
    assert args.seed == 7 and args.budget_usd == 2.0 and args.out_md == "SCORECARD.md"


def test_help_exits_clean():
    with pytest.raises(SystemExit) as e:
        run_scorecard.main(["--help"])
    assert e.value.code == 0


def test_main_no_key_is_noop(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert run_scorecard.main(["--n-questions", "1"]) == 0  # opt-in: exits 0, no run
