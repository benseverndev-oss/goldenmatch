from goldenflow.core import _native_loader as L


def test_phone_validate_is_fallback_only(monkeypatch):
    # Even with native present, phone_validate must NOT dispatch to native:
    # its only native symbol (phone_valid_arrow -> is_valid) is the wrong spec.
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", object())  # pretend native is importable
    assert L.native_enabled("phone_validate") is False


def test_phone_wired_component_enabled_when_symbol_present(monkeypatch):
    class FakeNative:
        def phone_e164_arrow(self): ...
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", FakeNative())
    assert L.native_enabled("phone") is True


def test_force_off(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    monkeypatch.setattr(L, "_native", object())
    assert L.native_enabled("phone") is False
