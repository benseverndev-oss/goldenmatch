from goldensuite_mcp.composites import run_step


def _table(**tools):
    return dict(tools)


def test_run_step_success():
    t = _table(foo=lambda n, a: {"value": a["x"] + 1})
    ok, res = run_step(t, "foo", {"x": 1})
    assert ok is True and res == {"value": 2}


def test_run_step_error_dict_is_failure():
    t = _table(foo=lambda n, a: {"error": "boom"})
    ok, res = run_step(t, "foo", {})
    assert ok is False and "boom" in res["error"]


def test_run_step_raise_is_failure():
    def boom(n, a):
        raise ValueError("kaboom")

    ok, res = run_step(_table(foo=boom), "foo", {})
    assert ok is False and "kaboom" in res["error"]


def test_run_step_missing_tool_is_failure():
    ok, res = run_step({}, "nope", {})
    assert ok is False and "nope" in res["error"]
