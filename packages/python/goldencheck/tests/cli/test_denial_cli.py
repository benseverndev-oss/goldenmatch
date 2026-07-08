import inspect

import polars as pl
from goldencheck.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def _planted_csv(tmp_path):
    import random
    rng = random.Random(0)
    rows = []
    for _ in range(300):
        status = rng.choice(["shipped", "pending"])
        order = rng.randint(1, 100)
        ship = order + rng.randint(0, 20)
        rows.append({"status": status, "order": order, "ship": ship})
    for i in range(5):  # inject violations of "if shipped then ship>=order"
        if rows[i]["status"] != "shipped":
            rows[i]["status"] = "shipped"
        rows[i]["ship"] = rows[i]["order"] - 1
    p = tmp_path / "orders.csv"
    pl.DataFrame(rows).write_csv(p)
    return str(p)


def test_denial_command_runs(tmp_path):
    csv = _planted_csv(tmp_path)
    result = runner.invoke(app, ["denial-constraints", csv])
    assert result.exit_code == 0, result.output
    # some discovered rule involving ship/order should print
    assert "ship" in result.output or "order" in result.output


def test_scan_denial_flag_opt_in(tmp_path):
    csv = _planted_csv(tmp_path)
    with_denial = runner.invoke(app, ["scan", csv, "--denial", "--no-tui"])
    assert with_denial.exit_code == 0, with_denial.output
    # plain scan (no --denial) must NOT surface a denial_constraint finding
    plain = runner.invoke(app, ["scan", csv, "--no-tui"])
    assert plain.exit_code == 0, plain.output
    assert "denial_constraint" not in plain.output  # opt-in only


def test_scan_command_has_denial_param():
    # introspect the click command params rather than scraping --help text
    cmd = next(c for c in app.registered_commands if c.callback.__name__ in ("scan", "scan_cmd"))
    # the param exists on the callback signature
    assert "denial" in inspect.signature(cmd.callback).parameters
