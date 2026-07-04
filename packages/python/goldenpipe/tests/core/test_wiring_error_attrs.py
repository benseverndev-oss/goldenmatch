from goldenpipe.engine.resolver import WiringError


def test_wiring_error_is_additive():
    # legacy raise (message only) still works
    e0 = WiringError("some message")
    assert str(e0) == "some message"
    assert e0.stage is None and e0.missing is None and e0.available is None
    # structured raise carries attrs, message preserved
    e1 = WiringError("msg", stage="s", missing="df", available=["a", "b"])
    assert str(e1) == "msg"
    assert (e1.stage, e1.missing, e1.available) == ("s", "df", ["a", "b"])
