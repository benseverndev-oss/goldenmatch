from pathlib import Path
from typer.testing import CliRunner
from goldencheck.cli.main import app

runner = CliRunner()
FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_scan_with_no_tui():
    result = runner.invoke(app, [str(FIXTURES / "simple.csv"), "--no-tui"])
    assert result.exit_code == 0


def test_validate_without_config():
    result = runner.invoke(app, ["validate", str(FIXTURES / "simple.csv")])
    assert result.exit_code != 0


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "1.0.0" in result.stdout


def test_llm_boost_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["scan", str(FIXTURES / "simple.csv"), "--llm-boost", "--no-tui"])
    assert result.exit_code != 0


# --- Error handling tests ---

def test_error_file_not_found():
    result = runner.invoke(app, ["scan", "nonexistent_file.csv", "--no-tui"])
    assert result.exit_code == 1
    assert "Error:" in result.stdout or "Error:" in (result.stderr or "")
    assert "Traceback" not in result.stdout


def test_error_unsupported_format(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("hello")
    result = runner.invoke(app, ["scan", str(f), "--no-tui"])
    assert result.exit_code == 1
    assert "Unsupported" in result.stdout or "Unsupported" in (result.stderr or "")
    assert "Traceback" not in result.stdout


# --- Fix command tests ---

def test_fix_safe_mode(tmp_path):
    csv = tmp_path / "dirty.csv"
    csv.write_text("name,age\n  Alice ,25\nBob  ,30\n")
    result = runner.invoke(app, ["fix", str(csv), "--dry-run"])
    assert result.exit_code == 0
    assert "trim_whitespace" in result.stdout or "clean" in result.stdout


def test_fix_aggressive_requires_force(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\n")
    result = runner.invoke(app, ["fix", str(csv), "--mode", "aggressive"])
    assert result.exit_code == 1


def test_fix_output_path(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\n  Alice ,25\n")
    out = tmp_path / "cleaned.csv"
    result = runner.invoke(app, ["fix", str(csv), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_fix_no_changes(tmp_path):
    csv = tmp_path / "clean.csv"
    csv.write_text("name,age\nAlice,25\nBob,30\n")
    result = runner.invoke(app, ["fix", str(csv)])
    assert result.exit_code == 0
    assert "clean" in result.stdout.lower()


# --- Diff command tests ---

def test_diff_two_files(tmp_path):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    old.write_text("name,age\nAlice,25\nBob,30\n")
    new.write_text("name,age,status\nAlice,25,active\nBob,30,inactive\nCharlie,28,active\n")
    result = runner.invoke(app, ["diff", str(old), str(new)])
    assert result.exit_code == 0
    assert "status" in result.stdout


def test_diff_json_output(tmp_path):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    old.write_text("name,age\nAlice,25\n")
    new.write_text("name,age\nAlice,25\nBob,30\n")
    result = runner.invoke(app, ["diff", str(old), str(new), "--json"])
    assert result.exit_code == 0
    assert "schema_changes" in result.stdout


def test_diff_no_changes(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,25\n")
    result = runner.invoke(app, ["diff", str(f), str(f)])
    assert result.exit_code == 0
    assert "No changes" in result.stdout


# --- Smart/guided tests ---

def test_smart_triage(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\nBob,30\n")
    result = runner.invoke(app, ["scan", str(csv), "--smart", "--no-tui"])
    assert result.exit_code == 0
    assert "Auto-triaged" in result.stdout


def test_smart_and_guided_mutual_exclusion(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\n")
    result = runner.invoke(app, ["scan", str(csv), "--smart", "--guided"])
    assert result.exit_code == 2


# --- History command tests ---

def test_history_empty():
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    assert "No scan history" in result.stdout or "Date" in result.stdout


def test_history_json():
    result = runner.invoke(app, ["history", "--json"])
    assert result.exit_code == 0


# --- Init command tests ---

def test_init_yes_mode(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,email\nAlice,a@b.com\nBob,b@c.com\n")
    result = runner.invoke(app, ["init", str(csv), "--yes"])
    assert result.exit_code == 0
    assert "goldencheck.yml" in result.stdout


# --- Domain flag tests ---

def test_scan_with_domain(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\nBob,30\n")
    result = runner.invoke(app, ["scan", str(csv), "--no-tui", "--domain", "healthcare"])
    assert result.exit_code == 0


def test_scan_invalid_domain(tmp_path):
    csv = tmp_path / "data.csv"
    csv.write_text("name,age\nAlice,25\n")
    result = runner.invoke(app, ["scan", str(csv), "--no-tui", "--domain", "nonexistent"])
    assert result.exit_code == 1


# --- Arg parser edge cases ---

def test_shorthand_no_tui():
    result = runner.invoke(app, [str(FIXTURES / "simple.csv"), "--no-tui", "--json"])
    assert result.exit_code == 0


def test_shorthand_unknown_flag():
    result = runner.invoke(app, [str(FIXTURES / "simple.csv"), "--badopt"])
    assert result.exit_code == 2


def test_no_file_argument():
    result = runner.invoke(app, [])
    assert result.exit_code == 0  # shows help


# --- Multi-file + HTML tests ---

def test_multi_file_scan():
    result = runner.invoke(app, [
        "scan", str(FIXTURES / "simple.csv"), str(FIXTURES / "messy.csv"), "--no-tui"
    ])
    assert result.exit_code == 0


def test_html_report(tmp_path):
    html = tmp_path / "report.html"
    result = runner.invoke(app, ["scan", str(FIXTURES / "simple.csv"), "--no-tui", "--html", str(html)])
    assert result.exit_code == 0
    assert html.exists()
    content = html.read_text()
    assert "GoldenCheck Report" in content
