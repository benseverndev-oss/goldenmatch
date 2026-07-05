from goldenpipe.engine.resolver import WiringError


def test_wiring_error_is_additive():
    # legacy raise (message only) still works
    e0 = WiringError("some message")
    assert str(e0) == "some message"
    assert e0.stage is None and e0.artifact is None and e0.missing is None
    # structured raise carries attrs, message preserved; `.missing` is a legacy alias
    e1 = WiringError("msg", stage="s", artifact="df")
    assert str(e1) == "msg"
    assert (e1.stage, e1.artifact) == ("s", "df")
    assert e1.missing == "df"  # back-compat alias for artifact
